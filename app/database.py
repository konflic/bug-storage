"""Database engine / session management.

The engine is created from settings.database_url. SQLite needs a couple of
special arguments; everything else uses sane defaults that also work for
Postgres.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import is_sqlite, settings


class Base(DeclarativeBase):
    pass


# Columns added to existing tables after the first release. Lightweight,
# idempotent stand-in for Alembic so upgrading an existing DB preserves data.
# (col_name -> SQL type fragment). Types are chosen to be valid on both SQLite
# and Postgres.
_BUGS_NEW_COLUMNS: dict[str, str] = {
    "signature": "VARCHAR(64)",
    "category": "VARCHAR(20)",
    "severity": "VARCHAR(20)",
    "root_cause": "VARCHAR(20)",
    "k8s_kind": "VARCHAR(64)",
    "namespace": "VARCHAR(255)",
    "reason": "VARCHAR(64)",
    "exit_code": "INTEGER",
    "error_signature": "VARCHAR(255)",
    "issue_key": "VARCHAR(64)",
    "issue_url": "VARCHAR(500)",
    "fix_notes": "TEXT",
}

_OCCURRENCE_NEW_COLUMNS: dict[str, str] = {
    "restart_count": "INTEGER",
    "severity_at_sighting": "VARCHAR(20)",
    "resolved": "BOOLEAN",
    "evidence": "JSON",
}


def ensure_schema(engine) -> None:
    """Add any newly-introduced columns to existing tables, in place.

    ``Base.metadata.create_all`` creates missing tables but never ALTERs
    existing ones. This adds the new analytics/tracker columns if they're
    absent, so an already-populated SQLite/Postgres DB keeps its rows on
    upgrade. New tables (e.g. ``tags``/``bug_tags``) are handled by create_all.
    """
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    def _add_missing(table: str, columns: dict[str, str]) -> None:
        if table not in existing_tables:
            return
        present = {c["name"] for c in inspector.get_columns(table)}
        with engine.begin() as conn:
            for name, sql_type in columns.items():
                if name not in present:
                    conn.execute(
                        text(f'ALTER TABLE {table} ADD COLUMN {name} {sql_type}')
                    )

    _add_missing("bugs", _BUGS_NEW_COLUMNS)
    _add_missing("bug_occurrences", _OCCURRENCE_NEW_COLUMNS)

    # Default severity for pre-existing rows (NULL -> 'unknown') so the NOT-NULL
    # intent of the model holds and stats group cleanly.
    if "bugs" in existing_tables:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE bugs SET severity = 'unknown' WHERE severity IS NULL")
            )

    _backfill_legacy_tags(engine)


def _backfill_legacy_tags(engine) -> None:
    """Migrate the old comma-separated ``bugs.tags`` text column into the new
    ``tags`` / ``bug_tags`` relation. Idempotent: only inserts links that don't
    already exist, and only runs when the legacy text column is still present.
    """
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if not {"bugs", "tags", "bug_tags"} <= tables:
        return
    bug_cols = {c["name"] for c in inspector.get_columns("bugs")}
    if "tags" not in bug_cols:
        return  # already on the relation-only schema; nothing to backfill.

    with engine.begin() as conn:
        rows = conn.execute(text('SELECT id, tags FROM bugs')).all()
        for bug_id, raw in rows:
            names = [t.strip() for t in (raw or "").split(",") if t.strip()]
            for name in names:
                tag_id = conn.execute(
                    text("SELECT id FROM tags WHERE name = :n"), {"n": name}
                ).scalar()
                if tag_id is None:
                    tag_id = conn.execute(
                        text("INSERT INTO tags (name) VALUES (:n)"), {"n": name}
                    ).lastrowid
                exists = conn.execute(
                    text(
                        "SELECT 1 FROM bug_tags WHERE bug_id = :b AND tag_id = :t"
                    ),
                    {"b": bug_id, "t": tag_id},
                ).scalar()
                if not exists:
                    conn.execute(
                        text(
                            "INSERT INTO bug_tags (bug_id, tag_id) VALUES (:b, :t)"
                        ),
                        {"b": bug_id, "t": tag_id},
                    )

    _drop_legacy_columns(engine)


# Columns that existed in the original schema but are no longer mapped by the
# model. They carried NOT NULL constraints, so leaving them in place would break
# INSERTs (the ORM never supplies them). ``tags`` is migrated into the relation
# first (see _backfill_legacy_tags); is_fixed/is_confirmed are now derived from
# ``status``. DROP COLUMN works on SQLite >= 3.35 and Postgres.
_LEGACY_DROP_COLUMNS = ("tags", "is_fixed", "is_confirmed")


def _drop_legacy_columns(engine) -> None:
    inspector = inspect(engine)
    if "bugs" not in set(inspector.get_table_names()):
        return
    present = {c["name"] for c in inspector.get_columns("bugs")}
    for col in _LEGACY_DROP_COLUMNS:
        if col not in present:
            continue
        try:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE bugs DROP COLUMN {col}"))
        except Exception:  # noqa: BLE001 - best-effort if engine lacks DROP COLUMN.
            pass


def _make_engine():
    if is_sqlite():
        # Ensure the directory for the sqlite file exists.
        path = settings.database_url.replace("sqlite:///", "", 1)
        if path and path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        engine = create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False},
            future=True,
        )

        # Enable WAL + foreign keys for SQLite (better concurrency / integrity).
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

        return engine

    # Postgres / others.
    return create_engine(settings.database_url, pool_pre_ping=True, future=True)


engine = _make_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


def get_db():
    """FastAPI dependency yielding a session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
