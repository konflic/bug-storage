"""FastAPI application exposing the bug database."""

from __future__ import annotations

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query, Response
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.orm import Session

from . import __version__, crud, similarity
from .auth import ApiKeyMiddleware
from .config import settings
from .database import Base, engine, ensure_schema, get_db
from .models import BugStatus, Category, RootCause, Severity
from .schemas import (
    BugCreate,
    BugOut,
    BugUpdate,
    OccurrenceIn,
    ReportResult,
    SearchResult,
    SimilarBug,
    StatsOut,
)

# Create tables on startup. For real migrations use Alembic (see AGENT.md);
# create_all is fine for SQLite dev and idempotent.
Base.metadata.create_all(bind=engine)
# Idempotently add any columns/tables introduced after the DB was first created
# (lightweight stand-in for Alembic so existing data is preserved on upgrade).
ensure_schema(engine)

app = FastAPI(title=settings.app_title, version=__version__)

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


@app.post("/bugs/report", response_model=ReportResult, summary="Find-or-create a bug")
def report_bug(payload: BugCreate, db: Session = Depends(get_db)) -> ReportResult:
    """Primary entry point during analysis.

    Checks for an existing similar bug. If found, records a new occurrence and
    returns it (created=False). Otherwise creates a new bug (created=True).
    """
    bug, created, matched_by, score = crud.report(db, payload)
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
def create_bug(payload: BugCreate, db: Session = Depends(get_db)) -> BugOut:
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
    return BugOut.model_validate(crud.create_bug(db, payload))


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
def update_bug(bug_id: int, payload: BugUpdate, db: Session = Depends(get_db)) -> BugOut:
    bug = crud.get_bug(db, bug_id)
    if not bug:
        raise HTTPException(404, "Bug not found")
    return BugOut.model_validate(crud.update_bug(db, bug, payload))


@app.post("/bugs/{bug_id}/occurrences", response_model=BugOut, summary="Record a new sighting")
def add_occurrence(bug_id: int, occ: OccurrenceIn, db: Session = Depends(get_db)) -> BugOut:
    bug = crud.get_bug(db, bug_id)
    if not bug:
        raise HTTPException(404, "Bug not found")
    return BugOut.model_validate(crud.record_occurrence(db, bug, occ))


@app.delete("/bugs/{bug_id}", status_code=204, summary="Delete a bug")
def delete_bug(bug_id: int, db: Session = Depends(get_db)) -> Response:
    bug = crud.get_bug(db, bug_id)
    if not bug:
        raise HTTPException(404, "Bug not found")
    crud.delete_bug(db, bug)
    return Response(status_code=204)
