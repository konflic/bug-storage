# Changelog

All notable changes to the Bug Database Service are documented in this file.

The format is loosely based on [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased] - 2026-06-30

Structured-analytics and dedupe overhaul to make the database easier for LLM
analysis and better for analytics. All changes are additive at the API level
and backward compatible; existing data is preserved by an in-place migration
that runs automatically on startup.

### Added

- **Signature-based identity (authoritative dedupe).**
  `app/similarity.py:compute_signature()` derives a SHA-256 identity hash from
  STABLE failure facts (`component + k8s_kind + reason + exit_code +
  error_signature`) instead of free text. This prevents an LLM that rewords a
  title from creating duplicate bugs. Matching order is now
  `signature` -> `fingerprint` (legacy) -> fuzzy text.
- **First-class analytics columns on `bugs`** (`app/models.py`), all indexed:
  - `category` (enum: oom, crashloop, image_pull, mount_failure, sync_failure,
    stuck_finalizer, stuck_terminating, probe_failure, scheduling,
    reconcile_error, network, rbac, config_error, other)
  - `severity` (enum: critical, high, medium, low, unknown)
  - `root_cause` (enum: under_provisioned, ordering_race, external_dep,
    code_bug, misconfiguration, flaky_test, infra, unknown) — distinguishes the
    *trigger* from the *root cause*.
  - `k8s_kind`, `namespace`, `reason`, `exit_code`, `error_signature`
  - `signature` (authoritative identity hash; `fingerprint` retained as legacy).
- **Tags as a proper relation.** New `tags` and `bug_tags` tables replace the
  comma-separated `bugs.tags` text column, so faceted queries (e.g. `?tag=oom`)
  are indexable instead of `LIKE` scans.
- **Enriched occurrences** (`bug_occurrences`): `restart_count`,
  `severity_at_sighting`, `resolved` (bool), and `evidence` (JSON snapshot,
  e.g. `{"mem_limit":"512Mi","mem_used":"479Mi"}`). Enables trend analysis such
  as "is this getting worse?".
- **Tracker link fields** on `bugs`: `issue_key` (e.g. `PROJ-2011`) and
  `issue_url`, for linking a bug to a tracker task (e.g. Jira).
- **`GET /bugs/stats` endpoint** (`StatsOut`): aggregated counts by status /
  category / severity / root_cause / component, floating count, top-recurring
  bugs (by `times_seen`), and recent occurrences-per-day. Lets callers/LLMs get
  analytics without paging all rows.
- **New list filters** on `GET /bugs`: `category`, `severity`, `root_cause`,
  `tag`.
- **MCP tooling** (`app/mcp_server.py`): new `bug_stats` tool; `report_bug`,
  `search_bugs`, `list_bugs`, `record_sighting`, and `update_bug` extended with
  the structured fields, occurrence snapshot fields, and tracker link. Tool
  docstrings now prompt the agent to supply the structured facts (which is what
  makes signature dedupe reliable).
- **In-place migration** `app/database.py:ensure_schema()`: idempotently adds
  the new columns to existing tables, creates the new `tags`/`bug_tags` tables,
  backfills the legacy CSV `tags` into the relation, and drops the obsolete
  NOT NULL columns. A lightweight stand-in for Alembic so existing SQLite /
  Postgres data survives the upgrade. Invoked on startup in `app/main.py`.

### Changed

- **Lifecycle flags collapsed onto `status`.** `is_fixed` and `is_confirmed`
  are now read-only properties derived from `BugStatus` (single source of
  truth). `is_floating` remains an orthogonal column. The API/MCP still accept
  `is_fixed`/`is_confirmed` as convenience inputs that map onto `status`.
- **`POST /bugs`** now rejects duplicates by `signature` first (then
  `fingerprint`), pointing callers to `POST /bugs/report`.
- **`search_similar` / `report`** (`app/crud.py`) accept and use the structured
  failure facts for signature matching; fuzzy corpus now also includes
  `error_signature` and `reason`.

### Migration / deployment notes

- The migration drops three obsolete columns from `bugs` (`tags`, `is_fixed`,
  `is_confirmed`) after backfilling tags into the relation. `DROP COLUMN`
  requires SQLite >= 3.35 or Postgres (both supported in this deployment);
  the drop is best-effort and guarded.
- Pre-existing bugs keep matching via `fingerprint` until enriched with the
  structured fields (then they gain a `signature`).
- Verified against the live dataset before and after deploy: all 9 bugs and
  their occurrences preserved, legacy tags migrated to the relation, counters
  intact. Signature dedupe confirmed live (a fully reworded clickhouse report
  matched the existing bug at score 1.0 with no duplicate created).
- A safety backup of the pre-migration SQLite DB (db + WAL + shm) was taken
  before rebuilding the container.

### Files touched

- `app/models.py` — new enums (`Category`, `Severity`, `RootCause`),
  `Tag`/`bug_tags`, structured + tracker columns, derived `is_fixed`/
  `is_confirmed`, enriched `BugOccurrence`.
- `app/similarity.py` — `compute_signature()`; corpus extended.
- `app/schemas.py` — new fields on `BugBase`/`BugCreate`/`BugUpdate`/`BugOut`,
  enriched `OccurrenceIn`/`OccurrenceOut`, new `StatsOut`.
- `app/crud.py` — signature-first matching, tag relation handling, status
  coherence, occurrence enrichment, `stats()` aggregation.
- `app/main.py` — `/bugs/stats`, new list filters, signature-aware create,
  `ensure_schema()` wired into startup.
- `app/database.py` — `ensure_schema()` migration, legacy tag backfill, legacy
  column drop.
- `app/mcp_server.py` — structured fields, occurrence snapshot, tracker link,
  `bug_stats` tool.
