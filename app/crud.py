"""Business logic: create/update bugs, record occurrences, dedupe & search."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import similarity
from .config import settings
from .models import AuditLog, Bug, BugOccurrence, BugStatus, Severity, Tag
from .schemas import BugCreate, BugUpdate, OccurrenceIn


def _bug_snapshot(bug: Bug) -> dict:
    """Compact JSON snapshot of a bug for the audit trail."""
    return {
        "id": bug.id,
        "title": bug.title,
        "status": bug.status.value if hasattr(bug.status, "value") else bug.status,
        "component": bug.component,
        "severity": bug.severity.value if hasattr(bug.severity, "value") else bug.severity,
        "is_floating": bug.is_floating,
        "times_seen": bug.times_seen,
    }


def add_audit(
    db: Session,
    *,
    actor_role: str,
    action: str,
    bug: Bug | None = None,
    bug_id: int | None = None,
    bug_title: str | None = None,
    detail: dict | None = None,
) -> None:
    """Append one entry to the audit log and commit it."""
    entry = AuditLog(
        actor_role=actor_role or "anonymous",
        action=action,
        bug_id=bug.id if bug is not None else bug_id,
        bug_title=bug.title if bug is not None else bug_title,
        detail=detail,
    )
    db.add(entry)
    db.commit()


def list_audit(
    db: Session,
    *,
    bug_id: int | None = None,
    action: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.at.desc())
    if bug_id is not None:
        stmt = stmt.where(AuditLog.bug_id == bug_id)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
    return list(db.scalars(stmt.limit(limit).offset(offset)).all())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC for safe comparison."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _clean_tag_names(tags: list[str] | None) -> list[str]:
    return sorted({t.strip() for t in (tags or []) if t.strip()})


def _get_or_create_tags(db: Session, names: list[str]) -> list[Tag]:
    """Resolve tag names to Tag rows, creating any that don't exist yet."""
    result: list[Tag] = []
    for name in _clean_tag_names(names):
        tag = db.scalar(select(Tag).where(Tag.name == name))
        if tag is None:
            tag = Tag(name=name)
            db.add(tag)
            db.flush()
        result.append(tag)
    return result


def _signature_of(payload: BugCreate | Bug) -> str | None:
    return similarity.compute_signature(
        component=payload.component,
        k8s_kind=payload.k8s_kind,
        reason=payload.reason,
        exit_code=payload.exit_code,
        error_signature=payload.error_signature,
        namespace=payload.namespace,
    )


def get_bug(db: Session, bug_id: int) -> Bug | None:
    return db.get(Bug, bug_id)


def list_bugs(
    db: Session,
    *,
    status: BugStatus | None = None,
    is_floating: bool | None = None,
    component: str | None = None,
    category=None,
    severity=None,
    root_cause=None,
    tag: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Bug]:
    stmt = select(Bug).order_by(Bug.last_seen_at.desc())
    if status is not None:
        stmt = stmt.where(Bug.status == status)
    if is_floating is not None:
        stmt = stmt.where(Bug.is_floating == is_floating)
    if component is not None:
        stmt = stmt.where(Bug.component == component)
    if category is not None:
        stmt = stmt.where(Bug.category == category)
    if severity is not None:
        stmt = stmt.where(Bug.severity == severity)
    if root_cause is not None:
        stmt = stmt.where(Bug.root_cause == root_cause)
    if tag is not None:
        stmt = stmt.where(Bug.tags_rel.any(Tag.name == tag))
    stmt = stmt.limit(limit).offset(offset)
    return list(db.scalars(stmt).all())


def find_by_signature(db: Session, signature: str | None) -> Bug | None:
    if not signature:
        return None
    return db.scalar(select(Bug).where(Bug.signature == signature))


def find_by_fingerprint(db: Session, fingerprint: str) -> Bug | None:
    return db.scalar(select(Bug).where(Bug.fingerprint == fingerprint))


def search_similar(
    db: Session,
    *,
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
) -> list[tuple[Bug, float, str]]:
    """Return [(bug, score, reason)] ordered by score desc.

    Matching order: signature (authoritative) -> fingerprint (legacy) -> text.
    """
    threshold = settings.similarity_threshold if threshold is None else threshold
    limit = settings.similarity_limit if limit is None else limit

    results: list[tuple[Bug, float, str]] = []
    seen_ids: set[int] = set()

    # 0. Authoritative signature match.
    sig = similarity.compute_signature(
        component=component,
        k8s_kind=k8s_kind,
        reason=reason,
        exit_code=exit_code,
        error_signature=error_signature,
        namespace=namespace,
    )
    sig_match = find_by_signature(db, sig)
    if sig_match is not None:
        results.append((sig_match, 1.0, "signature"))
        seen_ids.add(sig_match.id)

    # 1. Legacy fingerprint match.
    fp = similarity.compute_fingerprint(title, component, finalizer)
    exact = find_by_fingerprint(db, fp)
    if exact is not None and exact.id not in seen_ids:
        results.append((exact, 1.0, "fingerprint"))
        seen_ids.add(exact.id)

    # 2. Fuzzy text similarity.
    query_tokens = similarity.bug_corpus(
        title, short_description, full_description, steps_to_reproduce,
        component, finalizer, error_signature, reason,
    )
    for bug in db.scalars(select(Bug)).all():
        if bug.id in seen_ids:
            continue
        cand_tokens = similarity.bug_corpus(
            bug.title,
            bug.short_description,
            bug.full_description,
            bug.steps_to_reproduce,
            bug.component,
            bug.finalizer,
            bug.error_signature,
            bug.reason,
        )
        score = similarity.text_score(query_tokens, cand_tokens)
        if score >= threshold:
            results.append((bug, score, "text"))

    results.sort(key=lambda r: r[1], reverse=True)
    return results[:limit]


def create_bug(db: Session, payload: BugCreate) -> Bug:
    fp = similarity.compute_fingerprint(payload.title, payload.component, payload.finalizer)
    sig = _signature_of(payload)
    now = _utcnow()
    bug = Bug(
        signature=sig,
        fingerprint=fp,
        title=payload.title,
        short_description=payload.short_description,
        full_description=payload.full_description,
        steps_to_reproduce=payload.steps_to_reproduce,
        suggested_fix=payload.suggested_fix,
        fix_notes=payload.fix_notes,
        component=payload.component,
        finalizer=payload.finalizer,
        cluster=payload.cluster,
        category=payload.category,
        severity=payload.severity or Severity.unknown,
        root_cause=payload.root_cause,
        k8s_kind=payload.k8s_kind,
        namespace=payload.namespace,
        reason=payload.reason,
        exit_code=payload.exit_code,
        error_signature=payload.error_signature,
        issue_key=payload.issue_key,
        issue_url=payload.issue_url,
        is_floating=payload.is_floating,
        status=BugStatus.open,
        times_seen=1,
        times_updated=0,
        first_seen_at=now,
        last_seen_at=now,
    )
    bug.tags_rel = _get_or_create_tags(db, payload.tags)
    occ = payload.occurrence or OccurrenceIn()
    bug.occurrences.append(
        BugOccurrence(
            seen_at=occ.seen_at or now,
            cluster=occ.cluster or payload.cluster,
            namespace=occ.namespace or payload.namespace,
            note=occ.note,
            restart_count=occ.restart_count,
            severity_at_sighting=occ.severity_at_sighting,
            resolved=occ.resolved,
            evidence=occ.evidence,
        )
    )
    db.add(bug)
    db.commit()
    db.refresh(bug)
    return bug


def record_occurrence(db: Session, bug: Bug, occ: OccurrenceIn) -> Bug:
    """Add an occurrence and bump counters / last_seen."""
    now = occ.seen_at or _utcnow()
    bug.occurrences.append(
        BugOccurrence(
            seen_at=now,
            cluster=occ.cluster,
            namespace=occ.namespace,
            note=occ.note,
            restart_count=occ.restart_count,
            severity_at_sighting=occ.severity_at_sighting,
            resolved=occ.resolved,
            evidence=occ.evidence,
        )
    )
    bug.times_seen += 1
    last = _as_aware(bug.last_seen_at)
    if last is None or _as_aware(now) > last:
        bug.last_seen_at = now
    db.add(bug)
    db.commit()
    db.refresh(bug)
    return bug


def _compute_diff(bug: Bug, data: dict) -> dict:
    """Before/after values for fields being changed (for the audit log)."""
    diff: dict = {}
    for key, new in data.items():
        old = getattr(bug, key, None)
        old_v = old.value if hasattr(old, "value") else old
        new_v = new.value if hasattr(new, "value") else new
        if old_v != new_v:
            diff[key] = {"old": old_v, "new": new_v}
    return diff


def update_bug(db: Session, bug: Bug, payload: BugUpdate) -> Bug:
    data = payload.model_dump(exclude_unset=True)

    # Capture a diff of scalar fields BEFORE mutating (tags handled separately).
    bug.last_diff = _compute_diff(bug, {k: v for k, v in data.items() if k != "tags"})

    if "tags" in data:
        old_tags = bug.tags
        new_tags = _clean_tag_names(data["tags"])
        if old_tags != new_tags:
            bug.last_diff["tags"] = {"old": old_tags, "new": new_tags}
        bug.tags_rel = _get_or_create_tags(db, data.pop("tags"))

    # Map the convenience flags onto the single source of truth (status).
    is_confirmed = data.pop("is_confirmed", None)
    is_fixed = data.pop("is_fixed", None)
    if is_confirmed:
        data.setdefault("status", BugStatus.confirmed)
    elif is_fixed:
        data.setdefault("status", BugStatus.fixed)
    elif is_fixed is False and bug.status in (BugStatus.fixed, BugStatus.confirmed):
        data.setdefault("status", BugStatus.open)

    identity_changed = any(
        k in data for k in (
            "title", "component", "finalizer",
            "k8s_kind", "reason", "exit_code", "error_signature", "namespace",
        )
    )
    for key, value in data.items():
        setattr(bug, key, value)

    if identity_changed:
        bug.fingerprint = similarity.compute_fingerprint(
            bug.title, bug.component, bug.finalizer
        )
        bug.signature = similarity.compute_signature(
            component=bug.component,
            k8s_kind=bug.k8s_kind,
            reason=bug.reason,
            exit_code=bug.exit_code,
            error_signature=bug.error_signature,
            namespace=bug.namespace,
        )

    bug.times_updated += 1
    db.add(bug)
    db.commit()
    db.refresh(bug)
    return bug


def report(db: Session, payload: BugCreate) -> tuple[Bug, bool, str | None, float | None]:
    """Find-or-create entry point used during cluster analysis.

    1. If a similar bug exists (signature, fingerprint or text), record a new
       occurrence on it and return (bug, created=False, matched_by, score).
    2. Otherwise create a new bug and return (bug, created=True, None, None).
    """
    matches = search_similar(
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
    )
    if matches:
        bug, score, reason = matches[0]
        occ = payload.occurrence or OccurrenceIn(
            cluster=payload.cluster,
            namespace=payload.namespace,
            note="seen during analysis",
            severity_at_sighting=payload.severity,
        )
        bug = record_occurrence(db, bug, occ)
        return bug, False, reason, score

    return create_bug(db, payload), True, None, None


def stats(db: Session, *, days: int = 14, top: int = 10) -> dict:
    """Aggregate analytics in SQL instead of paging all rows client-side."""
    def _count_by(column) -> dict[str, int]:
        rows = db.execute(
            select(column, func.count()).group_by(column)
        ).all()
        out: dict[str, int] = {}
        for value, count in rows:
            key = value.value if hasattr(value, "value") else (value if value is not None else "none")
            out[str(key)] = count
        return out

    total_bugs = db.scalar(select(func.count()).select_from(Bug)) or 0
    total_occ = db.scalar(select(func.count()).select_from(BugOccurrence)) or 0
    floating = db.scalar(
        select(func.count()).select_from(Bug).where(Bug.is_floating.is_(True))
    ) or 0

    top_rows = db.execute(
        select(Bug.id, Bug.title, Bug.times_seen)
        .order_by(Bug.times_seen.desc())
        .limit(top)
    ).all()
    top_recurring = [
        {"id": i, "title": t, "times_seen": s} for i, t, s in top_rows
    ]

    # Occurrences per day over the recent window (done in Python for SQLite /
    # Postgres portability — avoids dialect-specific date_trunc).
    since = _utcnow() - timedelta(days=days)
    occ_rows = db.scalars(
        select(BugOccurrence.seen_at).where(BugOccurrence.seen_at >= since)
    ).all()
    per_day: Counter[str] = Counter()
    for seen_at in occ_rows:
        day = _as_aware(seen_at).date().isoformat()
        per_day[day] += 1

    return {
        "total_bugs": total_bugs,
        "total_occurrences": total_occ,
        "by_status": _count_by(Bug.status),
        "by_category": _count_by(Bug.category),
        "by_severity": _count_by(Bug.severity),
        "by_root_cause": _count_by(Bug.root_cause),
        "by_component": _count_by(Bug.component),
        "floating_count": floating,
        "top_recurring": top_recurring,
        "recent_occurrences_per_day": dict(sorted(per_day.items())),
    }


def delete_bug(db: Session, bug: Bug) -> None:
    db.delete(bug)
    db.commit()
