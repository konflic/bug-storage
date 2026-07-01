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

    # Simple shared-secret authorization. When set, every request (except the
    # open paths below) must present this key via the `X-API-Key` header or
    # `Authorization: Bearer <key>`. Leave empty to disable auth (dev only).
    api_key: str = ""

    # Paths that never require a key: health check (for probes/healthchecks),
    # the web UI, and the OpenAPI docs. Comma-separated env override supported.
    auth_open_paths: str = "/health,/ui,/,/docs,/redoc,/openapi.json,/favicon.ico"

    @property
    def open_paths(self) -> set[str]:
        return {p.strip() for p in self.auth_open_paths.split(",") if p.strip()}

    @property
    def auth_enabled(self) -> bool:
        return bool(self.api_key)


settings = Settings()


def is_sqlite() -> bool:
    return settings.database_url.startswith("sqlite")
