"""SQLAlchemy ORM models.

Schema overview
---------------
bugs
    The canonical record for a distinct bug. Holds human-readable descriptions
    and repro steps PLUS a set of structured, typed analytics columns
    (category, severity, root_cause, k8s_kind, reason, exit_code,
    error_signature, namespace) so that grouping/triage is a SQL ``GROUP BY``
    instead of NLP over prose. Denormalized counters (times_seen,
    last_seen_at) are kept in sync from occurrences for fast reads.

bug_occurrences
    One row per time the bug was observed/analyzed. Source of truth for "how
    many times the bug was met" and "when it was met last time". Each occurrence
    can carry a point-in-time snapshot (restart_count, severity_at_sighting,
    resolved flag, and a free-form ``evidence`` JSON blob) so trends like
    "is this getting worse?" are answerable directly.

tags
    Tags are a proper many-to-many relation (``tags`` table + ``bug_tags``
    association) instead of a comma-joined string, so faceted analytics like
    ``WHERE tag = 'oom'`` are indexable rather than a ``LIKE`` scan.

Identity / dedupe
-----------------
``signature`` is the AUTHORITATIVE identity hash. It is derived from STABLE
failure facts (component + k8s_kind + reason + exit_code + error_signature),
NOT from free-text the caller writes, so a reworded title no longer creates a
duplicate. ``fingerprint`` (title+component+finalizer) is kept as a secondary,
backwards-compatible identity hash. Fuzzy text similarity remains the last line.

Types chosen here (String, Text, JSON, DateTime, Boolean, Integer) are all
portable to Postgres, so migration is just a DATABASE_URL change + Alembic.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class BugStatus(str, enum.Enum):
    open = "open"
    fixed = "fixed"            # a fix has been applied
    confirmed = "confirmed"    # fix verified in the field
    wont_fix = "wont_fix"
    duplicate = "duplicate"


class Category(str, enum.Enum):
    """Coarse failure class. Lets you GROUP BY without reading descriptions."""

    oom = "oom"
    crashloop = "crashloop"
    image_pull = "image_pull"
    mount_failure = "mount_failure"
    sync_failure = "sync_failure"
    stuck_finalizer = "stuck_finalizer"
    stuck_terminating = "stuck_terminating"
    probe_failure = "probe_failure"
    scheduling = "scheduling"
    reconcile_error = "reconcile_error"
    network = "network"
    rbac = "rbac"
    config_error = "config_error"
    other = "other"


class Severity(str, enum.Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"
    unknown = "unknown"


class RootCause(str, enum.Enum):
    """Distinguishes the *trigger* from the *root cause* of a failure.

    e.g. an OOM whose trigger is a test-load spike but whose root cause is an
    under-provisioned memory limit -> ``under_provisioned``.
    """

    under_provisioned = "under_provisioned"
    ordering_race = "ordering_race"
    external_dep = "external_dep"
    code_bug = "code_bug"
    misconfiguration = "misconfiguration"
    flaky_test = "flaky_test"
    infra = "infra"
    unknown = "unknown"


# Many-to-many association between bugs and tags.
bug_tags = Table(
    "bug_tags",
    Base.metadata,
    Column("bug_id", ForeignKey("bugs.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)

    bugs: Mapped[list["Bug"]] = relationship(
        secondary=bug_tags, back_populates="tags_rel"
    )


class Bug(Base):
    __tablename__ = "bugs"

    id: Mapped[int] = mapped_column(primary_key=True)

    # Authoritative identity hash derived from stable failure facts
    # (see similarity.compute_signature). Robust to reworded titles.
    signature: Mapped[str | None] = mapped_column(String(64), unique=True, index=True, default=None)

    # Secondary/legacy identity hash for backwards compatibility
    # (see similarity.compute_fingerprint).
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    title: Mapped[str] = mapped_column(String(500), index=True)
    short_description: Mapped[str] = mapped_column(Text, default="")
    full_description: Mapped[str] = mapped_column(Text, default="")
    steps_to_reproduce: Mapped[str] = mapped_column(Text, default="")
    # The PROPOSED fix (what we think should be done). Distinct from fix_notes.
    suggested_fix: Mapped[str] = mapped_column(Text, default="")
    # Post-fix record: what was ACTUALLY done and verified (PR/commit, clusters
    # verified on, dates). Kept separate from suggested_fix so the proposal and
    # the resolution history don't get conflated.
    fix_notes: Mapped[str] = mapped_column(Text, default="")

    # Optional structured identity fields (also feed fingerprint/signature).
    component: Mapped[str | None] = mapped_column(String(255), index=True, default=None)
    finalizer: Mapped[str | None] = mapped_column(String(255), index=True, default=None)
    cluster: Mapped[str | None] = mapped_column(String(255), index=True, default=None)

    # --- Structured analytics columns (first-class, indexed) ---
    category: Mapped[Category | None] = mapped_column(
        Enum(Category, native_enum=False, length=20), index=True, default=None
    )
    severity: Mapped[Severity] = mapped_column(
        Enum(Severity, native_enum=False, length=20), index=True, default=Severity.unknown
    )
    root_cause: Mapped[RootCause | None] = mapped_column(
        Enum(RootCause, native_enum=False, length=20), index=True, default=None
    )
    # Kubernetes workload kind, e.g. StatefulSet, Deployment, AvailableReleases.
    k8s_kind: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    # Canonical namespace for the bug (occurrences may carry their own).
    namespace: Mapped[str | None] = mapped_column(String(255), index=True, default=None)
    # Container/termination reason, e.g. OOMKilled, CrashLoopBackOff, SyncFailed.
    reason: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    # Process/container exit code, e.g. 137 for OOMKilled.
    exit_code: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    # Grep-able root error token, e.g. MEMORY_LIMIT_EXCEEDED, no_tags_found.
    error_signature: Mapped[str | None] = mapped_column(String(255), index=True, default=None)

    # Optional link to a tracker task (e.g. Jira) when the bug is turned into
    # an issue. ``issue_key`` is the short human key (e.g. PROJ-2011) and
    # ``issue_url`` the full link.
    issue_key: Mapped[str | None] = mapped_column(String(64), index=True, default=None)
    issue_url: Mapped[str | None] = mapped_column(String(500), default=None)

    # Tags as a proper relation (queryable facets).
    tags_rel: Mapped[list["Tag"]] = relationship(
        secondary=bug_tags, back_populates="bugs", lazy="selectin"
    )

    @property
    def tags(self) -> list[str]:
        return sorted(t.name for t in self.tags_rel)

    # Lifecycle. ``status`` is the single source of truth; is_fixed/is_confirmed
    # are derived read-only properties from it. ``is_floating`` is orthogonal
    # (the bug is intermittent/flaky) and remains a real column.
    status: Mapped[BugStatus] = mapped_column(
        Enum(BugStatus, native_enum=False, length=20), default=BugStatus.open, index=True
    )
    is_floating: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    @property
    def is_fixed(self) -> bool:
        return self.status in (BugStatus.fixed, BugStatus.confirmed)

    @property
    def is_confirmed(self) -> bool:
        return self.status == BugStatus.confirmed

    # Denormalized counters (kept in sync from occurrences for fast queries).
    times_seen: Mapped[int] = mapped_column(Integer, default=1)
    times_updated: Mapped[int] = mapped_column(Integer, default=0)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    occurrences: Mapped[list["BugOccurrence"]] = relationship(
        back_populates="bug", cascade="all, delete-orphan", order_by="BugOccurrence.seen_at"
    )


class BugOccurrence(Base):
    __tablename__ = "bug_occurrences"
    __table_args__ = (UniqueConstraint("bug_id", "seen_at", name="uq_bug_seen"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    bug_id: Mapped[int] = mapped_column(ForeignKey("bugs.id", ondelete="CASCADE"), index=True)

    seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    cluster: Mapped[str | None] = mapped_column(String(255), index=True, default=None)
    namespace: Mapped[str | None] = mapped_column(String(255), default=None)
    note: Mapped[str] = mapped_column(Text, default="")

    # --- Point-in-time analytics snapshot (enables trend analysis) ---
    # Observed restart count at this sighting (track "is it getting worse?").
    restart_count: Mapped[int | None] = mapped_column(Integer, default=None)
    severity_at_sighting: Mapped[Severity | None] = mapped_column(
        Enum(Severity, native_enum=False, length=20), default=None
    )
    # Whether the bug appeared resolved/healthy at this sighting.
    resolved: Mapped[bool | None] = mapped_column(Boolean, default=None)
    # Free-form structured evidence, e.g.
    # {"exit_code":137,"mem_limit":"512Mi","mem_used":"479Mi","last_terminated_at":"..."}.
    evidence: Mapped[dict | None] = mapped_column(JSON, default=None)

    bug: Mapped["Bug"] = relationship(back_populates="occurrences")


class AuditLog(Base):
    """Append-only history of mutating actions.

    One row per write (create / update / delete / confirm / sighting). Stores
    who did it (the role that authenticated), what changed (a before/after diff
    for updates, the full snapshot for create/delete), and when. Never updated
    or deleted by the app, so it is a trustworthy audit trail.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    # Role that performed the action: "admin" | "readonly" | "anonymous".
    actor_role: Mapped[str] = mapped_column(String(20), default="admin", index=True)
    # Action verb: create | update | delete | confirm | sighting | rotate_readonly_key.
    action: Mapped[str] = mapped_column(String(32), index=True)

    # Target bug (nullable: e.g. key rotation isn't tied to a bug). No FK so the
    # audit row survives the bug being deleted.
    bug_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    bug_title: Mapped[str | None] = mapped_column(String(500), default=None)

    # Structured change detail. For update: {"field": {"old":..., "new":...}}.
    # For create/delete: a snapshot of the record. For sighting: the occurrence.
    detail: Mapped[dict | None] = mapped_column(JSON, default=None)


class AppConfig(Base):
    """Tiny key/value store for runtime-mutable settings.

    Used to hold the *effective* read-only API key so an admin can rotate it at
    runtime (env vars can't be changed by a running process). Seeded from the
    READONLY_API_KEY env var on first boot.
    """

    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )
