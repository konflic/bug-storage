"""Runtime configuration.

Everything is driven by environment variables so the same image runs against
SQLite (local/dev) or Postgres (prod) with no code changes.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLite by default. For Postgres set e.g.:
    #   DATABASE_URL=postgresql+psycopg://bugs:bugs@db:5432/bugs
    database_url: str = "sqlite:///./data/bugs.db"

    # How "close" two bugs must score (0..1) to be considered the same bug.
    # Used by the dedupe / similarity endpoint.
    similarity_threshold: float = 0.45

    # Max similar candidates returned by /bugs/search.
    similarity_limit: int = 10

    app_title: str = "Bug Database Service"

    # --- Authorization (two roles) -----------------------------------------
    # ADMIN key: full access (read + all writes + admin endpoints).
    # READ-ONLY key: GET requests only; safe to embed in a shared URL.
    #
    # `api_key` is the legacy single key. If `admin_api_key` is unset it is used
    # as the admin key (backwards compatible with older deployments).
    admin_api_key: str = ""
    api_key: str = ""  # legacy alias for the admin key

    # Seed value for the read-only key. The *effective* read-only key is stored
    # in the DB (so admins can rotate it at runtime); this env var only seeds it
    # on first boot when the DB has no key yet.
    readonly_api_key: str = ""

    # Paths that never require a key: health check (for probes/healthchecks),
    # the web UI, and the OpenAPI docs. Comma-separated env override supported.
    auth_open_paths: str = "/health,/ui,/,/docs,/redoc,/openapi.json,/favicon.ico"

    @property
    def open_paths(self) -> set[str]:
        return {p.strip() for p in self.auth_open_paths.split(",") if p.strip()}

    @property
    def effective_admin_key(self) -> str:
        """Admin key: prefer the explicit admin key, fall back to legacy api_key."""
        return self.admin_api_key or self.api_key

    @property
    def auth_enabled(self) -> bool:
        return bool(self.effective_admin_key)


settings = Settings()


def is_sqlite() -> bool:
    return settings.database_url.startswith("sqlite")
