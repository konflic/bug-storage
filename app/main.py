"""FastAPI application exposing the bug database."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from . import __version__, crud, similarity
from .auth import ApiKeyMiddleware
from .config import settings
from .database import Base, SessionLocal, engine, ensure_schema, get_db
from .keys import get_readonly_key, rotate_readonly_key, seed_readonly_key
from .models import BugStatus, Category, RootCause, Severity
from .schemas import (
    AuditOut,
    BugCreate,
    BugOut,
    BugUpdate,
    OccurrenceIn,
    ReportResult,
    RotateKeyResult,
    SearchResult,
    SimilarBug,
    StatsOut,
    WhoAmI,
)

# Create tables on startup. For real migrations use Alembic (see AGENT.md);
# create_all is fine for SQLite dev and idempotent.
Base.metadata.create_all(bind=engine)
# Idempotently add any columns/tables introduced after the DB was first created
# (lightweight stand-in for Alembic so existing data is preserved on upgrade).
ensure_schema(engine)

# Seed the read-only key from env on first boot (no-op if already set in DB).
with SessionLocal() as _db:
    seed_readonly_key(_db)

app = FastAPI(title=settings.app_title, version=__version__)


def require_admin(request: Request) -> str:
    """Dependency: allow only the admin role (or auth-disabled)."""
    role = getattr(request.state, "actor_role", "anonymous")
    if role != "admin":
        raise HTTPException(403, "Admin key required.")
    return role


def _role(request: Request) -> str:
    return getattr(request.state, "actor_role", "anonymous")

# Shared-secret auth. No-op unless API_KEY is set; leaves /health, /ui and the
# OpenAPI docs open (see app/auth.py and config.auth_open_paths).
app.add_middleware(ApiKeyMiddleware)

_STATIC_DIR = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    """Redirect the root URL to the web UI."""
    return RedirectResponse(url="/ui")


@app.get("/ui", include_in_schema=False)
def ui() -> FileResponse:
    """Serve the single-page web UI."""
    return FileResponse(_STATIC_DIR / "index.html")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__, "database": settings.database_url.split("://")[0]}


@app.get("/whoami", response_model=WhoAmI, summary="Report the caller's role")
def whoami(request: Request) -> WhoAmI:
    """Return the authenticated role (admin | readonly | anonymous). The UI uses
    this to decide whether to show edit/delete controls."""
    return WhoAmI(role=_role(request), auth_enabled=settings.auth_enabled)


@app.post("/bugs/report", response_model=ReportResult, summary="Find-or-create a bug")
def report_bug(payload: BugCreate, request: Request, db: Session = Depends(get_db)) -> ReportResult:
    """Primary entry point during analysis.

    Checks for an existing similar bug. If found, records a new occurrence and
    returns it (created=False). Otherwise creates a new bug (created=True).
    """
    bug, created, matched_by, score = crud.report(db, payload)
    crud.add_audit(
        db,
        actor_role=_role(request),
        action="create" if created else "sighting",
        bug=bug,
        detail={"created": created, "matched_by": matched_by},
    )
    return ReportResult(bug=BugOut.model_validate(bug), created=created, matched_by=matched_by, score=score)


@app.post("/bugs/search", response_model=SearchResult, summary="Find similar bugs (no writes)")
def search_bugs(
    payload: BugCreate,
    threshold: float | None = Query(default=None, ge=0, le=1),
    limit: int | None = Query(default=None, ge=1, le=100),
    db: Session = Depends(get_db),
) -> SearchResult:
    matches = crud.search_similar(
        db,
        title=payload.title,
        short_description=payload.short_description,
        full_description=payload.full_description,
        steps_to_reproduce=payload.steps_to_reproduce,
        component=payload.component,
        finalizer=payload.finalizer,
        k8s_kind=payload.k8s_kind,
        reason=payload.reason,
        exit_code=payload.exit_code,
        error_signature=payload.error_signature,
        namespace=payload.namespace,
        threshold=threshold,
        limit=limit,
    )
    return SearchResult(
        query_title=payload.title,
        matches=[
            SimilarBug(bug=BugOut.model_validate(b), score=round(s, 4), reason=r)
            for b, s, r in matches
        ],
    )


@app.post("/bugs", response_model=BugOut, status_code=201, summary="Create a bug")
def create_bug(payload: BugCreate, request: Request, db: Session = Depends(get_db)) -> BugOut:
    sig = similarity.compute_signature(
        component=payload.component,
        k8s_kind=payload.k8s_kind,
        reason=payload.reason,
        exit_code=payload.exit_code,
        error_signature=payload.error_signature,
        namespace=payload.namespace,
    )
    if crud.find_by_signature(db, sig):
        raise HTTPException(409, "A bug with this signature already exists; use /bugs/report.")
    fp = similarity.compute_fingerprint(payload.title, payload.component, payload.finalizer)
    if crud.find_by_fingerprint(db, fp):
        raise HTTPException(409, "A bug with this identity already exists; use /bugs/report.")
    bug = crud.create_bug(db, payload)
    crud.add_audit(db, actor_role=_role(request), action="create", bug=bug,
                   detail={"snapshot": crud._bug_snapshot(bug)})
    return BugOut.model_validate(bug)


@app.get("/bugs/stats", response_model=StatsOut, summary="Aggregated analytics")
def bug_stats(
    days: int = Query(14, ge=1, le=365),
    top: int = Query(10, ge=1, le=100),
    db: Session = Depends(get_db),
) -> StatsOut:
    """Counts by status/category/severity/root_cause/component, plus top
    recurring bugs and recent occurrences-per-day. Cheaper than paging /bugs."""
    return StatsOut(**crud.stats(db, days=days, top=top))


@app.get("/bugs", response_model=list[BugOut], summary="List bugs")
def list_bugs(
    status: BugStatus | None = None,
    is_floating: bool | None = None,
    component: str | None = None,
    category: Category | None = None,
    severity: Severity | None = None,
    root_cause: RootCause | None = None,
    tag: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
) -> list[BugOut]:
    bugs = crud.list_bugs(
        db,
        status=status,
        is_floating=is_floating,
        component=component,
        category=category,
        severity=severity,
        root_cause=root_cause,
        tag=tag,
        limit=limit,
        offset=offset,
    )
    return [BugOut.model_validate(b) for b in bugs]


@app.get("/bugs/{bug_id}", response_model=BugOut, summary="Get one bug")
def get_bug(bug_id: int, db: Session = Depends(get_db)) -> BugOut:
    bug = crud.get_bug(db, bug_id)
    if not bug:
        raise HTTPException(404, "Bug not found")
    return BugOut.model_validate(bug)


@app.patch("/bugs/{bug_id}", response_model=BugOut, summary="Update a bug / its status")
def update_bug(bug_id: int, payload: BugUpdate, request: Request, db: Session = Depends(get_db)) -> BugOut:
    bug = crud.get_bug(db, bug_id)
    if not bug:
        raise HTTPException(404, "Bug not found")
    updated = crud.update_bug(db, bug, payload)
    diff = getattr(updated, "last_diff", None) or {}
    # "confirm" is a meaningful, distinct action worth its own audit verb.
    action = "confirm" if diff.get("status", {}).get("new") == "confirmed" else "update"
    crud.add_audit(db, actor_role=_role(request), action=action, bug=updated, detail={"changes": diff})
    return BugOut.model_validate(updated)


@app.post("/bugs/{bug_id}/occurrences", response_model=BugOut, summary="Record a new sighting")
def add_occurrence(bug_id: int, occ: OccurrenceIn, request: Request, db: Session = Depends(get_db)) -> BugOut:
    bug = crud.get_bug(db, bug_id)
    if not bug:
        raise HTTPException(404, "Bug not found")
    bug = crud.record_occurrence(db, bug, occ)
    crud.add_audit(db, actor_role=_role(request), action="sighting", bug=bug,
                   detail={"cluster": occ.cluster, "namespace": occ.namespace, "note": occ.note})
    return BugOut.model_validate(bug)


@app.delete("/bugs/{bug_id}", status_code=204, summary="Delete a bug")
def delete_bug(bug_id: int, request: Request, db: Session = Depends(get_db)) -> Response:
    bug = crud.get_bug(db, bug_id)
    if not bug:
        raise HTTPException(404, "Bug not found")
    snapshot = crud._bug_snapshot(bug)
    title = bug.title
    crud.delete_bug(db, bug)
    # Log AFTER delete; keep id/title in the audit row (no FK) so history survives.
    crud.add_audit(db, actor_role=_role(request), action="delete",
                   bug_id=bug_id, bug_title=title, detail={"snapshot": snapshot})
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Audit history + admin
# --------------------------------------------------------------------------- #
@app.get("/audit", response_model=list[AuditOut], summary="Action history (admin)")
def get_audit(
    request: Request,
    bug_id: int | None = None,
    action: str | None = None,
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _admin: str = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[AuditOut]:
    """Full history of mutating actions (create/update/delete/confirm/sighting).
    Admin only."""
    return [AuditOut.model_validate(a) for a in crud.list_audit(
        db, bug_id=bug_id, action=action, limit=limit, offset=offset)]


@app.get("/admin/readonly-key", response_model=RotateKeyResult,
         summary="Get the current read-only key (admin)")
def get_readonly_key_ep(_admin: str = Depends(require_admin),
                        db: Session = Depends(get_db)) -> RotateKeyResult:
    """Return the current read-only key so an admin can build a shareable link.
    Admin only; the read-only key itself can never read this endpoint."""
    return RotateKeyResult(readonly_api_key=get_readonly_key(db))


@app.post("/admin/rotate-readonly-key", response_model=RotateKeyResult,
          summary="Rotate the read-only key (admin)")
def rotate_key(request: Request, _admin: str = Depends(require_admin),
               db: Session = Depends(get_db)) -> RotateKeyResult:
    """Generate a NEW read-only key. Old shared links stop working immediately."""
    new_key = rotate_readonly_key(db)
    crud.add_audit(db, actor_role=_role(request), action="rotate_readonly_key")
    return RotateKeyResult(readonly_api_key=new_key)
