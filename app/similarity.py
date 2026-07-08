"""Dedupe / similarity helpers.

Three layers of matching (cheapest / most authoritative first):

0. Signature (exact, authoritative).
   A SHA-256 over STABLE failure facts (component + k8s_kind + reason +
   exit_code + error_signature). Because it does not depend on free-text the
   caller writes, a reworded title cannot create a duplicate. This is the
   preferred identity when those facts are supplied.

1. Fingerprint (exact, legacy/back-compat).
   A SHA-256 over the normalized identity (finalizer + component + title).
   Kept so existing rows keep matching even before they are backfilled with a
   signature.

2. Fuzzy text similarity (portable, no extra deps).
   Token-set scoring over title + descriptions + repro steps. Blends a weighted
   Jaccard with a containment score so a short new report still matches a longer
   existing one. Runs in Python so it works identically on SQLite and Postgres.
   For large datasets swap this for Postgres pg_trgm / FTS or a vector index
   without changing the API.
"""

from __future__ import annotations

import hashlib
import re

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9._/-]*")

# Very small stopword set; we keep domain tokens like "finalizer", "namespace".
_STOP = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "is", "are", "be",
    "for", "with", "that", "this", "it", "its", "as", "by", "from", "at", "so",
}


def normalize(text: str | None) -> str:
    return (text or "").strip().lower()


def _norm_token(value: str | None) -> str:
    """Normalize an identity token: lowercase, collapse separators."""
    return re.sub(r"[\s]+", "_", normalize(value))


def tokenize(text: str | None) -> set[str]:
    toks = _TOKEN_RE.findall(normalize(text))
    return {t for t in toks if t not in _STOP and len(t) > 1}


def compute_signature(
    component: str | None = None,
    k8s_kind: str | None = None,
    reason: str | None = None,
    exit_code: int | None = None,
    error_signature: str | None = None,
    namespace: str | None = None,
) -> str | None:
    """Authoritative identity hash from stable failure facts.

    Returns ``None`` when there is not enough structured signal to identify the
    bug (caller then falls back to fingerprint/text). We require at least a
    component plus one discriminating fact so we never collapse unrelated bugs.
    """
    facts = [
        _norm_token(component),
        _norm_token(k8s_kind),
        _norm_token(reason),
        "" if exit_code is None else str(exit_code),
        _norm_token(error_signature),
        # Namespace is intentionally part of identity only when nothing else is
        # available; we keep it last and low-weight by excluding it unless a
        # discriminating fact is present (handled below).
    ]
    discriminating = [f for f in (facts[1], facts[2], facts[3], facts[4]) if f]
    if not facts[0] or not discriminating:
        return None
    basis = "|".join(facts)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def compute_fingerprint(
    title: str,
    component: str | None = None,
    finalizer: str | None = None,
) -> str:
    """Stable identity hash. Prefer structured identity, fall back to title."""
    parts = [
        normalize(finalizer),
        normalize(component),
        # Collapse whitespace in the title so trivial formatting differs don't matter.
        re.sub(r"\s+", " ", normalize(title)),
    ]
    basis = "|".join(parts)
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def text_score(a_tokens: set[str], b_tokens: set[str]) -> float:
    """Blend of Jaccard and containment in [0, 1]."""
    if not a_tokens or not b_tokens:
        return 0.0
    inter = len(a_tokens & b_tokens)
    if inter == 0:
        return 0.0
    union = len(a_tokens | b_tokens)
    jaccard = inter / union
    containment = inter / min(len(a_tokens), len(b_tokens))
    # Containment helps short new reports match longer stored ones.
    return 0.5 * jaccard + 0.5 * containment


def bug_corpus(
    title: str,
    short_description: str = "",
    full_description: str = "",
    steps_to_reproduce: str = "",
    impact: str = "",
    component: str | None = None,
    finalizer: str | None = None,
    error_signature: str | None = None,
    reason: str | None = None,
) -> set[str]:
    """Token set used for fuzzy matching. Title/identity weighted by repetition."""
    tokens: set[str] = set()
    tokens |= tokenize(title)
    tokens |= tokenize(short_description)
    tokens |= tokenize(full_description)
    tokens |= tokenize(steps_to_reproduce)
    tokens |= tokenize(impact)
    tokens |= tokenize(component)
    tokens |= tokenize(finalizer)
    tokens |= tokenize(error_signature)
    tokens |= tokenize(reason)
    return tokens
