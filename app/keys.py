"""Runtime API-key management.

The ADMIN key comes from the environment (root of trust, not mutable at
runtime). The READ-ONLY key is stored in the DB so an admin can rotate it via
the API without a redeploy; it is seeded from the READONLY_API_KEY env var on
first boot.
"""

from __future__ import annotations

import secrets

from sqlalchemy.orm import Session

from .config import settings
from .models import AppConfig

_READONLY_KEY_NAME = "readonly_api_key"


def get_readonly_key(db: Session) -> str:
    """Return the effective read-only key (DB value, or '' if unset)."""
    row = db.get(AppConfig, _READONLY_KEY_NAME)
    return row.value if row and row.value else ""


def set_readonly_key(db: Session, value: str) -> None:
    row = db.get(AppConfig, _READONLY_KEY_NAME)
    if row is None:
        row = AppConfig(key=_READONLY_KEY_NAME, value=value)
        db.add(row)
    else:
        row.value = value
    db.commit()


def rotate_readonly_key(db: Session) -> str:
    """Generate a new read-only key, persist it, and return it."""
    new_key = secrets.token_urlsafe(32)
    set_readonly_key(db, new_key)
    return new_key


def seed_readonly_key(db: Session) -> None:
    """On first boot, seed the DB read-only key from the env var if present."""
    if not get_readonly_key(db) and settings.readonly_api_key:
        set_readonly_key(db, settings.readonly_api_key)
