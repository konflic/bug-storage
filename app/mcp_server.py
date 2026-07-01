"""MCP server for the bug database.

Exposes the bug DB as MCP tools so an analysis agent can call it natively
instead of shelling out to `bugctl`. The server is a thin client over the
running HTTP API (single source of truth, works for SQLite or Postgres, and
avoids two processes contending on the SQLite file).

Run (stdio transport, the default for MCP clients):

    BUGDB_API=http://localhost:8000 python -m app.mcp_server

If the API requires a key, also set BUGDB_API_KEY; it is sent as X-API-Key.

Configure your agent/host to launch that command. Example opencode MCP config:

    {
      "mcp": {
        "bugdb": {
          "type": "local",
          "command": ["python", "-m", "app.mcp_server"],
          "environment": {
            "BUGDB_API": "http://localhost:8000",
            "BUGDB_API_KEY": "your-secret-key"
          }
        }
      }
    }
"""

from __future__ import annotations

import os

import httpx
from mcp.server.fastmcp import FastMCP

BUGDB_API = os.environ.get("BUGDB_API", "http://localhost:8000")
BUGDB_API_KEY = os.environ.get("BUGDB_API_KEY", "")

mcp = FastMCP("bugdb")


def _client() -> httpx.Client:
    headers = {"X-API-Key": BUGDB_API_KEY} if BUGDB_API_KEY else {}
    return httpx.Client(base_url=BUGDB_API, timeout=30.0, headers=headers)


def _raise_for_status(resp: httpx.Response) -> dict:
    if resp.status_code >= 400:
        raise RuntimeError(f"bugdb API error {resp.status_code}: {resp.text}")
    if resp.status_code == 204:
        return {"ok": True}
    return resp.json()


def _str_or_none(value) -> str | None:
    """Coerce cluster-like inputs (an agent may pass an int) to a string."""
    return None if value is None else str(value)


@mcp.tool()
def health() -> dict:
    """Check that the bug database service is reachable."""
    with _client() as c:
        return _raise_for_status(c.get("/health"))


@mcp.tool()
def report_bug(
    title: str,
    short_description: str = "",
    full_description: str = "",
    steps_to_reproduce: str = "",
    suggested_fix: str = "",
    component: str | None = None,
    finalizer: str | None = None,
    cluster: str | int | None = None,
    tags: list[str] | None = None,
    is_floating: bool = False,
    category: str | None = None,
    severity: str | None = None,
    root_cause: str | None = None,
    k8s_kind: str | None = None,
    namespace: str | None = None,
    reason: str | None = None,
    exit_code: int | None = None,
    error_signature: str | None = None,
    issue_key: str | None = None,
    issue_url: str | None = None,
    occurrence: dict | None = None,
) -> dict:
    """Find-or-create a bug. USE THIS for every finding during analysis.

    Matching order: authoritative `signature` (component + k8s_kind + reason +
    exit_code + error_signature) first, then legacy `fingerprint`, then fuzzy
    text. If a match is found it records a NEW OCCURRENCE on that bug and returns
    it with `created=false` and `matched_by`/`score`. Otherwise it creates a new
    bug and returns `created=true`.

    ALWAYS pass the structured fields below when known — they make dedupe robust
    to reworded titles and make analytics a SQL GROUP BY.

    Args:
        title: One-line human-readable bug title (required).
        short_description / full_description / steps_to_reproduce / suggested_fix.
        component: Owning operator/component (part of signature).
        finalizer: Kubernetes finalizer involved, if any.
        cluster: Cluster id where it was observed (e.g. "12345").
        tags: Free-form labels (stored as a queryable relation).
        is_floating: True if the bug is intermittent/flaky.
        category: Coarse class: oom | crashloop | image_pull | mount_failure |
            sync_failure | stuck_finalizer | stuck_terminating | probe_failure |
            scheduling | reconcile_error | network | rbac | config_error | other.
        severity: critical | high | medium | low | unknown.
        root_cause: under_provisioned | ordering_race | external_dep | code_bug |
            misconfiguration | flaky_test | infra | unknown. (Distinguish the
            trigger from the root cause, e.g. test-load spike vs low mem limit.)
        k8s_kind: Workload kind, e.g. StatefulSet, Deployment, AvailableReleases.
        namespace: Namespace where observed.
        reason: Container/termination reason, e.g. OOMKilled, SyncFailed.
        exit_code: Container exit code, e.g. 137 (OOMKilled).
        error_signature: Grep-able root token, e.g. MEMORY_LIMIT_EXCEEDED.
        issue_key / issue_url: Tracker (e.g. Jira) key/link if filed as a task.
        occurrence: Optional sighting snapshot, e.g.
            {"restart_count": 28, "resolved": false,
             "evidence": {"mem_limit": "512Mi", "mem_used": "479Mi"}}.
    """
    payload = {
        "title": title,
        "short_description": short_description,
        "full_description": full_description,
        "steps_to_reproduce": steps_to_reproduce,
        "suggested_fix": suggested_fix,
        "component": component,
        "finalizer": finalizer,
        "cluster": _str_or_none(cluster),
        "tags": tags or [],
        "is_floating": is_floating,
        "category": category,
        "severity": severity or "unknown",
        "root_cause": root_cause,
        "k8s_kind": k8s_kind,
        "namespace": namespace,
        "reason": reason,
        "exit_code": exit_code,
        "error_signature": error_signature,
        "issue_key": issue_key,
        "issue_url": issue_url,
        "occurrence": occurrence,
    }
    with _client() as c:
        return _raise_for_status(c.post("/bugs/report", json=payload))


@mcp.tool()
def search_bugs(
    title: str,
    short_description: str = "",
    full_description: str = "",
    steps_to_reproduce: str = "",
    component: str | None = None,
    finalizer: str | None = None,
    k8s_kind: str | None = None,
    reason: str | None = None,
    exit_code: int | None = None,
    error_signature: str | None = None,
    namespace: str | None = None,
    threshold: float | None = None,
    limit: int | None = None,
) -> dict:
    """Search for similar existing bugs WITHOUT writing anything.

    Use this to check whether a bug is already known before deciding what to do.
    Returns ranked matches, each with a `score` (0..1) and `reason`
    ("signature" = authoritative identity match, "fingerprint" = legacy identity
    match, "text" = fuzzy similarity). Pass the structured failure facts
    (component/k8s_kind/reason/exit_code/error_signature) for a precise match.

    Args:
        threshold: Override match threshold (0..1, higher = stricter).
        limit: Max number of matches to return.
    """
    payload = {
        "title": title,
        "short_description": short_description,
        "full_description": full_description,
        "steps_to_reproduce": steps_to_reproduce,
        "component": component,
        "finalizer": finalizer,
        "k8s_kind": k8s_kind,
        "reason": reason,
        "exit_code": exit_code,
        "error_signature": error_signature,
        "namespace": namespace,
        "tags": [],
        "is_floating": False,
    }
    params: dict = {}
    if threshold is not None:
        params["threshold"] = threshold
    if limit is not None:
        params["limit"] = limit
    with _client() as c:
        return _raise_for_status(c.post("/bugs/search", params=params, json=payload))


@mcp.tool()
def list_bugs(
    status: str | None = None,
    is_floating: bool | None = None,
    component: str | None = None,
    category: str | None = None,
    severity: str | None = None,
    root_cause: str | None = None,
    tag: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict]:
    """List stored bugs, newest sighting first.

    Args:
        status: Filter by status: open | fixed | confirmed | wont_fix | duplicate.
        is_floating: Filter to only floating (or non-floating) bugs.
        component: Filter by owning component.
        category: Filter by failure class (oom, crashloop, sync_failure, ...).
        severity: Filter by critical | high | medium | low | unknown.
        root_cause: Filter by under_provisioned | ordering_race | external_dep | ...
        tag: Filter to bugs carrying this tag.
    """
    params: dict = {"limit": limit, "offset": offset}
    for key, value in {
        "status": status,
        "is_floating": is_floating,
        "component": component,
        "category": category,
        "severity": severity,
        "root_cause": root_cause,
        "tag": tag,
    }.items():
        if value is not None:
            params[key] = value
    with _client() as c:
        return _raise_for_status(c.get("/bugs", params=params))


@mcp.tool()
def bug_stats(days: int = 14, top: int = 10) -> dict:
    """Aggregated analytics across the whole database (cheaper than listing all).

    Returns counts by status/category/severity/root_cause/component, the
    floating count, the top recurring bugs (by times_seen), and recent
    occurrences-per-day over the last `days`. Use this to answer "what are the
    most common / most recurring problems?" without paging every bug.

    Args:
        days: Window (days) for the occurrences-per-day trend.
        top: How many top-recurring bugs to return.
    """
    with _client() as c:
        return _raise_for_status(c.get("/bugs/stats", params={"days": days, "top": top}))


@mcp.tool()
def get_bug(bug_id: int) -> dict:
    """Fetch one bug by id, including its full occurrence history."""
    with _client() as c:
        return _raise_for_status(c.get(f"/bugs/{bug_id}"))


@mcp.tool()
def record_sighting(
    bug_id: int,
    cluster: str | int | None = None,
    namespace: str | None = None,
    note: str = "",
    restart_count: int | None = None,
    severity_at_sighting: str | None = None,
    resolved: bool | None = None,
    evidence: dict | None = None,
) -> dict:
    """Record that a known bug was seen again (bumps times_seen / last_seen_at).

    Args:
        restart_count: Observed restart count now (track "is it getting worse?").
        severity_at_sighting: Severity at this sighting.
        resolved: True if the bug looked resolved/healthy at this sighting.
        evidence: Structured snapshot, e.g.
            {"exit_code": 137, "mem_limit": "512Mi", "mem_used": "479Mi"}.
    """
    payload = {
        "cluster": _str_or_none(cluster),
        "namespace": namespace,
        "note": note,
        "restart_count": restart_count,
        "severity_at_sighting": severity_at_sighting,
        "resolved": resolved,
        "evidence": evidence,
    }
    with _client() as c:
        return _raise_for_status(c.post(f"/bugs/{bug_id}/occurrences", json=payload))


@mcp.tool()
def confirm_bug(bug_id: int) -> dict:
    """Mark a bug as fixed AND confirmed (fix verified in the field)."""
    with _client() as c:
        return _raise_for_status(c.patch(f"/bugs/{bug_id}", json={"is_fixed": True, "is_confirmed": True}))


@mcp.tool()
def update_bug(
    bug_id: int,
    title: str | None = None,
    short_description: str | None = None,
    full_description: str | None = None,
    steps_to_reproduce: str | None = None,
    suggested_fix: str | None = None,
    component: str | None = None,
    finalizer: str | None = None,
    status: str | None = None,
    is_floating: bool | None = None,
    is_fixed: bool | None = None,
    is_confirmed: bool | None = None,
    category: str | None = None,
    severity: str | None = None,
    root_cause: str | None = None,
    k8s_kind: str | None = None,
    namespace: str | None = None,
    reason: str | None = None,
    exit_code: int | None = None,
    error_signature: str | None = None,
    tags: list[str] | None = None,
    issue_key: str | None = None,
    issue_url: str | None = None,
) -> dict:
    """Update fields/status of an existing bug. Only provided fields change.

    Set `issue_key`/`issue_url` to link the bug to a tracker task (e.g. Jira).
    Setting any identity field (component/k8s_kind/reason/exit_code/
    error_signature) recomputes the dedupe signature.
    """
    body: dict = {}
    for key, value in {
        "title": title,
        "short_description": short_description,
        "full_description": full_description,
        "steps_to_reproduce": steps_to_reproduce,
        "suggested_fix": suggested_fix,
        "component": component,
        "finalizer": finalizer,
        "status": status,
        "is_floating": is_floating,
        "is_fixed": is_fixed,
        "is_confirmed": is_confirmed,
        "category": category,
        "severity": severity,
        "root_cause": root_cause,
        "k8s_kind": k8s_kind,
        "namespace": namespace,
        "reason": reason,
        "exit_code": exit_code,
        "error_signature": error_signature,
        "tags": tags,
        "issue_key": issue_key,
        "issue_url": issue_url,
    }.items():
        if value is not None:
            body[key] = value
    with _client() as c:
        return _raise_for_status(c.patch(f"/bugs/{bug_id}", json=body))


def main() -> None:
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
