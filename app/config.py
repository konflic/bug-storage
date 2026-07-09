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

    # --- Issue tracker integration --------------------------------------------
    # OAuth2 credentials for the external issue tracker API.
    tracker_client_id: str = ""
    tracker_client_secret: str = ""
    # Pre-obtained OAuth token (simplest setup: get a token manually once).
    tracker_oauth_token: str = ""
    # Refresh token for automatic renewal (obtained via the authorize flow).
    tracker_refresh_token: str = ""
    # Target queue for new issues (e.g. "MYPROJECT").
    tracker_queue: str = ""
    # API base URL.
    tracker_api_url: str = ""
    # Web UI base URL (for building human-readable issue links).
    tracker_web_url: str = ""
    # OAuth2 token endpoint.
    tracker_token_url: str = ""
    # OAuth2 authorize endpoint (for the one-time browser flow).
    tracker_authorize_url: str = ""
    # --- Tracker issue defaults (saved so they're not repeated each time) -----
    # Issue type key (e.g. "bug", "task").
    tracker_issue_type: str = "bug"
    # Tags to set on every tracker issue (comma-separated).
    tracker_tags: str = "qa,bug"
    # Component name(s) to attach (comma-separated).
    tracker_components: str = ""
    # Board ID to attach the issue to.
    tracker_board_id: int = 0
    # Description language: "ru" or "en".
    tracker_language: str = "ru"
    # OAuth2 scopes requested during authorization (space-separated).
    tracker_scopes: str = ""


    @property
    def tracker_tags_list(self) -> list[str]:
        return [t.strip() for t in self.tracker_tags.split(",") if t.strip()]

    @property
    def tracker_components_list(self) -> list[str]:
        return [c.strip() for c in self.tracker_components.split(",") if c.strip()]

    @property
    def tracker_enabled(self) -> bool:
        """True when there's either a direct token or client credentials."""
        return bool(self.tracker_oauth_token or
                    (self.tracker_client_id and self.tracker_client_secret and
                     self.tracker_refresh_token))

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
