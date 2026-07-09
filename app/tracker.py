"""Issue tracker integration client.

Provides OAuth2 token management and thin wrappers around the tracker REST API
for creating, reading, updating and searching issues.

Supports two authentication modes:

1. **Direct token** (simplest): set ``TRACKER_OAUTH_TOKEN`` in your ``.env``.
   The token is used as-is until it expires. No automatic refresh.

2. **Refresh-token flow** (recommended for long-lived deployments): set
   ``TRACKER_CLIENT_ID``, ``TRACKER_CLIENT_SECRET``, and
   ``TRACKER_REFRESH_TOKEN``. The client will exchange the refresh token for
   a fresh access token automatically. Use ``python -m app.tracker_auth``
   to do the one-time browser authorization and obtain the refresh token.

Usage::

    from app.tracker import TrackerClient, TrackerError

    client = TrackerClient()        # reads settings from app.config
    issue = client.create_issue(
        queue="MYPROJECT",
        summary="storage-operator hot-loop",
        description="...",
        tags=["bug", "kubernetes"],
    )
    print(issue["key"])             # e.g. "MYPROJECT-123"

All methods raise ``TrackerError`` on HTTP / auth failures so the caller can
distinguish "tracker is down" from application bugs.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .config import settings

log = logging.getLogger(__name__)


class TrackerError(Exception):
    """Raised when a Tracker API call fails."""

    def __init__(self, status_code: int, detail: str) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"Tracker API {status_code}: {detail}")


class TrackerClient:
    """Stateful client that caches the OAuth2 token and refreshes it on expiry.

    Two modes:
    - **Direct token**: uses ``settings.tracker_oauth_token`` as-is.
    - **Refresh token**: exchanges ``settings.tracker_refresh_token`` for a fresh
      access token via the OAuth2 token endpoint when needed.
    """

    def __init__(self) -> None:
        self._access_token: str | None = None
        self._token_expires_at: float = 0.0

    # ---------------------------------------------------------------------- #
    # OAuth2 token management
    # ---------------------------------------------------------------------- #

    def _ensure_token(self) -> str:
        """Return a valid OAuth2 bearer token, refreshing as needed."""
        # Already have a valid cached token?
        if self._access_token and time.time() < self._token_expires_at:
            return self._access_token

        # Mode 1: direct (pre-obtained) token.
        if settings.tracker_oauth_token:
            self._access_token = settings.tracker_oauth_token
            # No expiry info; assume it's valid for a long time.
            # If it expires, the 401 handler below will surface the error.
            self._token_expires_at = time.time() + 86400
            return self._access_token

        # Mode 2: refresh-token exchange.
        if (settings.tracker_client_id and settings.tracker_client_secret
                and settings.tracker_refresh_token):
            return self._refresh_access_token()

        raise TrackerError(
            503,
            "Tracker integration is not configured. "
            "Set TRACKER_OAUTH_TOKEN, or set TRACKER_CLIENT_ID + "
            "TRACKER_CLIENT_SECRET + TRACKER_REFRESH_TOKEN. "
            "Run 'python -m app.tracker_auth' to obtain a refresh token.",
        )

    def _refresh_access_token(self) -> str:
        """Exchange the refresh token for a new access token."""
        if not settings.tracker_token_url:
            raise TrackerError(503, "TRACKER_TOKEN_URL is not set.")

        log.info("Refreshing OAuth2 token via %s", settings.tracker_token_url)
        resp = httpx.post(
            settings.tracker_token_url,
            data={
                "grant_type": "refresh_token",
                "refresh_token": settings.tracker_refresh_token,
                "client_id": settings.tracker_client_id,
                "client_secret": settings.tracker_client_secret,
            },
            timeout=15.0,
        )
        if resp.status_code != 200:
            raise TrackerError(
                resp.status_code,
                f"OAuth token refresh failed: {resp.text}",
            )

        body = resp.json()
        self._access_token = body["access_token"]
        expires_in = body.get("expires_in", 3600)
        # Refresh 60s before actual expiry to avoid edge-case 401s.
        self._token_expires_at = time.time() + max(expires_in - 60, 0)
        log.info("OAuth2 token refreshed, expires in %ds", expires_in)
        return self._access_token

    # ---------------------------------------------------------------------- #
    # Low-level HTTP helpers
    # ---------------------------------------------------------------------- #

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"OAuth {self._ensure_token()}",
            "Content-Type": "application/json",
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
    ) -> dict | list:
        url = f"{settings.tracker_api_url}{path}"
        resp = httpx.request(
            method, url, headers=self._headers(),
            json=json, params=params, timeout=30.0,
        )
        if resp.status_code >= 400:
            raise TrackerError(resp.status_code, resp.text[:2000])
        return resp.json()

    # ---------------------------------------------------------------------- #
    # Issue operations
    # ---------------------------------------------------------------------- #

    def create_issue(
        self,
        *,
        queue: str | None = None,
        summary: str,
        description: str = "",
        type: str | None = None,
        priority: str | None = None,
        tags: list[str] | None = None,
        components: list[str] | None = None,
        assignee: str | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> dict:
        """Create a new issue in the tracker.

        Returns the full issue object (with ``key``, ``id``, ``self``, etc.).
        """
        body: dict[str, Any] = {
            "queue": queue or settings.tracker_queue,
            "summary": summary,
        }
        if description:
            body["description"] = description
        if type:
            body["type"] = type
        if priority:
            body["priority"] = priority
        if tags:
            body["tags"] = tags
        if components:
            body["components"] = components
        if assignee:
            body["assignee"] = assignee
        if extra_fields:
            body.update(extra_fields)

        result = self._request("POST", "/v2/issues", json=body)
        log.info("Created tracker issue %s", result.get("key"))
        return result

    def get_issue(self, issue_key: str) -> dict:
        """Fetch an issue by its key (e.g. ``PROJECT-123``)."""
        return self._request("GET", f"/v2/issues/{issue_key}")

    def update_issue(
        self,
        issue_key: str,
        *,
        summary: str | None = None,
        description: str | None = None,
        priority: str | None = None,
        tags: list[str] | None = None,
        extra_fields: dict[str, Any] | None = None,
    ) -> dict:
        """Update fields on an existing issue. Only provided fields change."""
        body: dict[str, Any] = {}
        if summary is not None:
            body["summary"] = summary
        if description is not None:
            body["description"] = description
        if priority is not None:
            body["priority"] = priority
        if tags is not None:
            body["tags"] = tags
        if extra_fields:
            body.update(extra_fields)

        if not body:
            return self.get_issue(issue_key)

        result = self._request("PATCH", f"/v2/issues/{issue_key}", json=body)
        log.info("Updated tracker issue %s", issue_key)
        return result

    def search_issues(
        self,
        *,
        query: str | None = None,
        filter: dict[str, Any] | None = None,
        keys: list[str] | None = None,
        queue: str | None = None,
        order: str | None = None,
        page: int = 1,
        per_page: int = 50,
    ) -> list[dict]:
        """Search for issues using the tracker query language or filter object.

        Examples::

            # By query language
            client.search_issues(query='Queue: MYPROJECT Tags: "bugdb"')

            # By structured filter
            client.search_issues(filter={"queue": "MYPROJECT", "tags": "bugdb"})

            # By explicit keys
            client.search_issues(keys=["MYPROJECT-101", "MYPROJECT-102"])
        """
        body: dict[str, Any] = {}
        if query is not None:
            body["query"] = query
        if filter is not None:
            body["filter"] = filter
        if keys is not None:
            body["keys"] = keys
        if queue is not None:
            body["queue"] = queue
        if order is not None:
            body["order"] = order

        params = {"page": page, "perPage": per_page}
        return self._request("POST", "/v2/issues/_search", json=body, params=params)


# Module-level singleton (lazy-initialized on first use).
_client: TrackerClient | None = None


def get_tracker_client() -> TrackerClient:
    """Return the module-level TrackerClient singleton.

    Raises ``TrackerError`` if tracker integration is not configured.
    """
    global _client
    if not settings.tracker_enabled:
        raise TrackerError(
            503,
            "Tracker integration is not configured. "
            "Set TRACKER_OAUTH_TOKEN, or set TRACKER_CLIENT_ID + "
            "TRACKER_CLIENT_SECRET + TRACKER_REFRESH_TOKEN. "
            "Run 'python -m app.tracker_auth' to obtain a refresh token.",
        )
    if _client is None:
        _client = TrackerClient()
    return _client
