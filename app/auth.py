"""Role-based shared-secret authorization.

Two roles:

* **admin**  — full access (all reads + all writes + admin endpoints). Key comes
  from the ADMIN_API_KEY (or legacy API_KEY) env var.
* **readonly** — GET requests only. Safe to embed in a shared URL. The key is
  stored in the DB and can be rotated at runtime by an admin.

A request may present its key via any of:

    X-API-Key: <key>
    Authorization: Bearer <key>
    ?key=<key>            (query string — convenient for shareable links)

The resolved role is attached to ``request.state.actor_role`` so route handlers
can record it in the audit log. A small allowlist of paths (health, UI, docs)
stays open. Comparisons are constant-time.
"""

from __future__ import annotations

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import settings
from .database import SessionLocal
from .keys import get_readonly_key


def _extract_key(request: Request) -> str | None:
    """Pull the presented key from header, bearer token, or ?key= query."""
    header_key = request.headers.get("x-api-key")
    if header_key:
        return header_key
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    q = request.query_params.get("key")
    if q:
        return q
    return None


def _is_open(path: str) -> bool:
    """True if `path` is in the allowlist (exact match or a static prefix)."""
    open_paths = settings.open_paths
    if path in open_paths:
        return True
    return any(path.startswith(p + "/") for p in open_paths if p not in ("/",))


def _match_role(presented: str) -> str | None:
    """Return 'admin' | 'readonly' | None for the presented key."""
    admin = settings.effective_admin_key
    if admin and secrets.compare_digest(presented, admin):
        return "admin"
    # Read-only key lives in the DB (rotatable). Open a short-lived session.
    db = SessionLocal()
    try:
        ro = get_readonly_key(db)
    finally:
        db.close()
    if ro and secrets.compare_digest(presented, ro):
        return "readonly"
    return None


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Enforce auth + role. readonly may only issue safe (GET/HEAD) requests."""

    async def dispatch(self, request: Request, call_next):
        request.state.actor_role = "anonymous"

        if not settings.auth_enabled:
            request.state.actor_role = "admin"  # auth off -> treat as admin
            return await call_next(request)

        if request.method == "OPTIONS" or _is_open(request.url.path):
            return await call_next(request)

        presented = _extract_key(request)
        role = _match_role(presented) if presented else None
        if role is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid API key."},
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Read-only key may only perform safe methods.
        if role == "readonly" and request.method not in ("GET", "HEAD"):
            return JSONResponse(
                status_code=403,
                content={"detail": "Read-only key cannot perform write operations."},
            )

        request.state.actor_role = role
        return await call_next(request)
