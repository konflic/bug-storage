"""Simple shared-secret API-key authorization.

Enabled by setting the API_KEY environment variable. When enabled, every
request must present the key via either:

    X-API-Key: <key>
    Authorization: Bearer <key>

A small allowlist of paths (health check, web UI, OpenAPI docs) stays open so
that container/uptime probes and the browser UI keep working. See
`Settings.auth_open_paths` in config.py.

Comparison is constant-time to avoid leaking the key through timing.
"""

from __future__ import annotations

import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from .config import settings


def _extract_key(request: Request) -> str | None:
    """Pull the presented key from X-API-Key or Authorization: Bearer."""
    header_key = request.headers.get("x-api-key")
    if header_key:
        return header_key
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


def _is_open(path: str) -> bool:
    """True if `path` is in the allowlist (exact match or a static prefix)."""
    open_paths = settings.open_paths
    if path in open_paths:
        return True
    # Allow anything served under the static UI mount, if present.
    return any(path.startswith(p + "/") for p in open_paths if p not in ("/",))


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Reject requests lacking a valid API key, unless the path is open."""

    async def dispatch(self, request: Request, call_next):
        if not settings.auth_enabled:
            return await call_next(request)

        # Always allow CORS/preflight requests through.
        if request.method == "OPTIONS" or _is_open(request.url.path):
            return await call_next(request)

        presented = _extract_key(request)
        if not presented or not secrets.compare_digest(presented, settings.api_key):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid API key."},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)
