"""Pydantic request/response models (API contract)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .models import BugStatus, Category, RootCause, Severity


class OccurrenceIn(BaseModel):
    seen_at: datetime | None = None
    cluster: str | None = None
    namespace: str | None = None
    note: str = ""
    # Point-in-time analytics snapshot.
    restart_count: int | None = None
    severity_at_sighting: Severity | None = None
    resolved: bool | None = None
    evidence: dict | None = None


class OccurrenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    seen_at: datetime
    cluster: str | None
    namespace: str | None
    note: str
    restart_count: int | None = None
    severity_at_sighting: Severity | None = None
    resolved: bool | None = None
    evidence: dict | None = None


class BugBase(BaseModel):
    title: str = Field(min_length=3, max_length=500)
    short_description: str = ""
    full_description: str = ""
    steps_to_reproduce: str = ""
    suggested_fix: str = ""
    # Post-fix record: what was actually done & verified (PR/commit, clusters,
    # dates). Keep the proposal in suggested_fix and the resolution here.
    fix_notes: str = ""
    # Potential risks / blast radius of this bug (data loss, downtime, etc.).
    impact: str = ""
    component: str | None = None
    finalizer: str | None = None
    cluster: str | None = None
    tags: list[str] = Field(default_factory=list)
    is_floating: bool = False

    # Structured analytics fields (all optional; strongly recommended for LLMs).
    category: Category | None = None
    severity: Severity = Severity.unknown
    root_cause: RootCause | None = None
    k8s_kind: str | None = None
    namespace: str | None = None
    reason: str | None = None
    exit_code: int | None = None
    error_signature: str | None = None
    # Optional tracker link (e.g. Jira) when the bug is tracked as a task.
    issue_key: str | None = None
    issue_url: str | None = None


class BugCreate(BugBase):
    """Create a new bug. Optionally attach the first occurrence."""
    occurrence: OccurrenceIn | None = None


class BugUpdate(BaseModel):
    """Partial update of bug fields/status."""
    title: str | None = None
    short_description: str | None = None
    full_description: str | None = None
    steps_to_reproduce: str | None = None
    suggested_fix: str | None = None
    fix_notes: str | None = None
    impact: str | None = None
    component: str | None = None
    finalizer: str | None = None
    cluster: str | None = None
    tags: list[str] | None = None
    status: BugStatus | None = None
    is_floating: bool | None = None
    # Convenience flags that map onto ``status`` (kept for API back-compat).
    is_fixed: bool | None = None
    is_confirmed: bool | None = None
    # Structured analytics fields.
    category: Category | None = None
    severity: Severity | None = None
    root_cause: RootCause | None = None
    k8s_kind: str | None = None
    namespace: str | None = None
    reason: str | None = None
    exit_code: int | None = None
    error_signature: str | None = None
    issue_key: str | None = None
    issue_url: str | None = None


class BugOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    signature: str | None
    fingerprint: str
    title: str
    short_description: str
    full_description: str
    steps_to_reproduce: str
    suggested_fix: str
    fix_notes: str
    impact: str
    component: str | None
    finalizer: str | None
    cluster: str | None
    category: Category | None
    severity: Severity
    root_cause: RootCause | None
    k8s_kind: str | None
    namespace: str | None
    reason: str | None
    exit_code: int | None
    error_signature: str | None
    issue_key: str | None
    issue_url: str | None
    tags: list[str]
    status: BugStatus
    is_floating: bool
    is_fixed: bool
    is_confirmed: bool
    times_seen: int
    times_updated: int
    first_seen_at: datetime
    last_seen_at: datetime
    created_at: datetime
    updated_at: datetime
    occurrences: list[OccurrenceOut] = []


class SimilarBug(BaseModel):
    bug: BugOut
    score: float
    reason: str  # "signature" | "fingerprint" | "text"


class SearchResult(BaseModel):
    query_title: str
    matches: list[SimilarBug]


class ReportResult(BaseModel):
    """Result of the high-level /bugs/report (find-or-create) call."""
    bug: BugOut
    created: bool
    matched_by: str | None  # "signature" | "fingerprint" | "text" | None when created new
    score: float | None


class StatsOut(BaseModel):
    """Aggregated analytics so callers don't page all rows to compute them."""
    total_bugs: int
    total_occurrences: int
    by_status: dict[str, int]
    by_category: dict[str, int]
    by_severity: dict[str, int]
    by_root_cause: dict[str, int]
    by_component: dict[str, int]
    floating_count: int
    top_recurring: list[dict]  # [{id, title, times_seen}]
    recent_occurrences_per_day: dict[str, int]  # {"YYYY-MM-DD": count}


class WhoAmI(BaseModel):
    """Caller's authenticated role; used by the UI to gate edit controls."""
    role: str  # "admin" | "readonly" | "anonymous"
    auth_enabled: bool


class AuditOut(BaseModel):
    """One entry in the append-only action history."""
    model_config = ConfigDict(from_attributes=True)
    id: int
    at: datetime
    actor_role: str
    action: str
    bug_id: int | None
    bug_title: str | None
    detail: dict | None


class RotateKeyResult(BaseModel):
    readonly_api_key: str


class TrackerIssueOut(BaseModel):
    """Result of creating/syncing a tracker issue for a bug."""
    issue_key: str          # e.g. "PROJECT-123"
    issue_url: str          # full URL to the issue in the tracker
    created: bool           # True if new issue was created, False if updated
    bug_id: int             # The bugsdb bug id that was linked
