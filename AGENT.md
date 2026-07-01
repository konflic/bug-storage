# AGENT.md — Bug Database

This is the operating guide for using the local **bug database** while analyzing
Kubernetes cluster issues. The goal is to *remember* bugs across analysis runs:
store each distinct bug once, count how often it recurs, track whether it's
floating/fixed/confirmed, and — crucially — **check for an existing similar bug
before creating a new one** so analysis improves over time instead of repeating.

---

## Two ways the agent talks to the DB

1. **MCP tools (preferred for an agent).** Run the MCP server and call tools like
   `report_bug`, `search_bugs`, `confirm_bug` directly. See **MCP server** below.
2. **`bugctl` CLI / REST.** Shell out to `./bugctl ...` or hit the HTTP API.

Both go through the same HTTP API, so behavior (dedupe, counters) is identical.

## TL;DR workflow for the agent

When you find an issue in a cluster:

1. **Always go through `report` (find-or-create), not raw create.**
   ```bash
   ./bugctl report \
     --title "<one-line title>" \
     --short "<short description>" \
     --full  "<full description>" \
     --steps "<steps to reproduce>" \
     --fix   "<suggested fix>" \
     --finalizer "<k8s finalizer if any>" \
     --component "<operator/component>" \
     --cluster "<cluster id, e.g. 12345>" \
     [--floating] [--tags terminating,kubernetes]
   ```
   - If a similar bug already exists → it records a **new occurrence** and prints
     `[DUP] matched bug #N`. `times_seen` increments and `last_seen_at` updates.
   - If nothing matches → it creates a **new bug** and prints `[NEW] created bug #N`.

2. **If you just want to look (no writes), use `search`:**
   ```bash
   ./bugctl search --title "namespace stuck terminating bucket finalizer" \
     --finalizer example.com/bucket-finalizer
   ```
   Returns ranked matches with a `score` and a `reason` (`fingerprint` or `text`).

3. **When a fix is verified in the cluster, confirm it:**
   ```bash
   ./bugctl confirm <id>     # sets is_fixed=true, is_confirmed=true, status=confirmed
   ```

4. **If the same bug shows up again later, record the sighting:**
   ```bash
   ./bugctl seen <id> --cluster 12345 --namespace my-namespace \
     --note "reappeared after operator restart"
   ```

> Rule of thumb: **`report` on every finding.** It is idempotent-ish by design —
> it will never create an obvious duplicate, it just adds an occurrence.

---

## What is stored (data model)

Each **bug** record holds exactly what the task asked for:

| Field | Meaning |
|-------|---------|
| `id` | stable numeric ID |
| `fingerprint` | SHA-256 of identity (finalizer + component + title) for exact dedupe |
| `title` | one-line title |
| `short_description` | short description |
| `full_description` | full description |
| `steps_to_reproduce` | repro steps |
| `suggested_fix` | mitigation / fix |
| `component`, `finalizer`, `cluster` | structured identity / context |
| `tags` | free-form labels (list) |
| `status` | `open` / `fixed` / `confirmed` / `wont_fix` / `duplicate` |
| `is_floating` | intermittent/flaky bug |
| `is_fixed`, `is_confirmed` | fix applied / fix verified |
| `times_seen` | **how many times the bug was met** |
| `times_updated` | how many times the record was edited |
| `first_seen_at`, `last_seen_at` | first / **last time it was met** |
| `created_at`, `updated_at` | record lifecycle timestamps |
| `occurrences[]` | full history: one row per sighting (time, cluster, namespace, note) |

`times_seen` and `last_seen_at` are derived from the **occurrences** table, so you
always have an auditable history of every time the bug was encountered.

---

## Running the service

### Option A — Docker (recommended)
```bash
docker compose up -d --build      # serves on http://localhost:8000
curl http://localhost:8000/health
```
Data persists in the `bugdata` Docker volume (SQLite file at `/srv/data/bugs.db`).

### Option B — local (no Docker)
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python index.py                   # http://localhost:8000  (auto-reload)
```

### Interactive API docs
Open <http://localhost:8000/docs> (Swagger UI, auto-generated).

---

## The `bugctl` helper

`bugctl` is a thin HTTP client for the API. Point it at a non-default host with
`--api` or the `BUGDB_API` env var.

```bash
./bugctl health
./bugctl report   --title "..." [fields...]      # find-or-create (use this!)
./bugctl search   --title "..." [--threshold 0.5]# similarity search, no writes
./bugctl list     [--floating] [--status open] [--component strimzi]
./bugctl get      <id>
./bugctl update   <id> [--status fixed] [--fix "..."] [--floating true]
./bugctl confirm  <id>                           # mark fixed + confirmed
./bugctl seen     <id> --cluster 12345 --namespace <ns> --note "..."
./bugctl import-dir <folder>                     # bulk-import *.txt issue files
```

### Bulk import issue files
`import-dir` ingests a folder of plain-text reports that use a `TITLE / SHORT
DESCRIPTION / FULL DESCRIPTION / SUGGESTED FIX / STEPS TO REPRODUCE` layout:
```bash
./bugctl import-dir <folder>
```
Each file goes through `report` (find-or-create), so re-importing is safe — it
won't create duplicates, it records occurrences. Overview/summary files (names
starting with `00-`, or containing `overview`/`problems`) are skipped automatically.

---

## MCP server (native agent integration)

The agent can call the bug DB as **MCP tools** instead of shelling out. The MCP
server (`app/mcp_server.py`) is a thin client over the HTTP API and uses stdio
transport, so the agent host launches it as a subprocess.

**Prerequisite:** the HTTP API must be running (e.g. `docker compose up -d`).

### Tools exposed
| Tool | Purpose |
|------|---------|
| `health` | check the service is reachable |
| `report_bug` | **find-or-create** (records occurrence on match) — use for every finding |
| `search_bugs` | similarity search, no writes |
| `list_bugs` | list with filters (status / is_floating / component) |
| `get_bug` | fetch one bug with occurrence history |
| `record_sighting` | record a new occurrence of a known bug |
| `confirm_bug` | mark fixed + confirmed |
| `update_bug` | edit fields / status |

### Configure your agent host (opencode)
A ready-to-use **project-level `opencode.json`** ships in the repo root — opencode
loads it automatically when you work in this directory. It looks like:
```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "bugdb": {
      "type": "local",
      "command": [".venv/bin/python", "-m", "app.mcp_server"],
      "cwd": ".",
      "enabled": true,
      "environment": {
        "BUGDB_API": "http://localhost:8000",
        "BUGDB_API_KEY": ""
      }
    }
  }
}
```
Notes:
- Paths are **relative to the repo root**. If opencode launches the subprocess
  from elsewhere, use an absolute venv Python path, or a system `python` that has
  the `mcp` package installed.
- `cwd` ensures `app.mcp_server` is importable.
- If the API requires a key, set `BUGDB_API_KEY` (sent as `X-API-Key`).
- The MCP server is a thin client over the HTTP API, so **the API must be running**
  (`docker compose up -d`) and `BUGDB_API` must point at it.
- Config is read only at startup — **restart opencode** after changing it.

Any MCP client works the same way (the `command`/`env` shape is standard).

Run it manually to test (it waits silently for an MCP client over stdio):
```bash
BUGDB_API=http://localhost:8000 .venv/bin/python -m app.mcp_server
```

## How similarity / dedupe works

Two layers (see `app/similarity.py`):

1. **Fingerprint (exact).** A hash of `finalizer | component | normalized-title`.
   Same fingerprint ⇒ definitely the same bug (score `1.0`, reason `fingerprint`).
2. **Fuzzy text (portable).** Token-set scoring (blended Jaccard + containment)
   over title + descriptions + repro steps. A match counts when
   `score >= SIMILARITY_THRESHOLD` (default `0.45`).

Tune sensitivity with the `SIMILARITY_THRESHOLD` env var (0..1, higher = stricter)
or per-call with `./bugctl search --threshold 0.6`.

---

## REST API summary

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/health` | liveness + which DB backend |
| `POST` | `/bugs/report` | **find-or-create** (records occurrence on match) |
| `POST` | `/bugs/search` | similarity search, no writes |
| `POST` | `/bugs` | create (409 if identity already exists) |
| `GET`  | `/bugs` | list (filters: `status`, `is_floating`, `component`) |
| `GET`  | `/bugs/{id}` | fetch one (with occurrences) |
| `PATCH`| `/bugs/{id}` | update fields / status |
| `POST` | `/bugs/{id}/occurrences` | record a sighting |
| `DELETE`| `/bugs/{id}` | delete |

---

## Migrating SQLite → Postgres → "real service"

The code is DB-agnostic (SQLAlchemy). To switch backends you only change the
`DATABASE_URL`; **no code changes**.

1. In `docker-compose.yml`, uncomment the `db` (Postgres) service and the
   `depends_on`, and set on the `api` service:
   ```yaml
   DATABASE_URL: "postgresql+psycopg://bugs:bugs@db:5432/bugs"
   ```
2. `docker compose up -d --build`.

The Postgres driver (`psycopg`) is already in `requirements.txt`.

**Before production**, replace the dev-time `Base.metadata.create_all()` with
**Alembic** migrations so schema changes are versioned (the models are already
written with portable column types). Suggested next step:
```bash
pip install alembic && alembic init migrations
# point sqlalchemy.url at DATABASE_URL, then: alembic revision --autogenerate
```

---

## Project layout
```
app/
  config.py      # env-driven settings (DATABASE_URL, thresholds)
  database.py    # engine/session; SQLite WAL+FK; Postgres-ready
  models.py      # ORM: Bug, BugOccurrence
  schemas.py     # Pydantic request/response contract
  similarity.py  # fingerprint + fuzzy text scoring
  crud.py        # business logic (report/search/update/...)
  main.py        # FastAPI routes
  mcp_server.py  # MCP server exposing the DB as agent tools (stdio)
bugctl           # helper CLI (HTTP client + .txt importer)
index.py         # local dev runner (uvicorn --reload)
opencode.json    # opencode MCP config (registers the bugdb server)
.env.example     # copy to .env to override DATABASE_URL / thresholds
Dockerfile, docker-compose.yml, requirements.txt
```

---

## Suggested improvements (roadmap)

These were deliberately left out to keep v1 simple; pick up as needed:

- **Alembic migrations** — required before Postgres in prod (see above).
- **AuthN/AuthZ** — add an API token / mTLS before exposing beyond localhost.
- **Better similarity at scale** — the current Python token scorer loads all bugs
  per search (fine for hundreds, not millions). On Postgres, switch to
  `pg_trgm` (`gin` index + `similarity()`), full-text `tsvector`, or `pgvector`
  + embeddings for semantic matching. The `search_similar` function is the only
  thing to change.
- **Automatic floating detection** — flip `is_floating=true` automatically when a
  bug has occurrences spread across N distinct days/runs with gaps.
- **De-dup merge endpoint** — `POST /bugs/{id}/merge/{other}` to fold a
  mistakenly-created duplicate into the canonical bug (move occurrences, set the
  loser's `status=duplicate`).
- ~~**MCP / agent tool wrapper**~~ — done (`app/mcp_server.py`). Possible follow-up:
  add an HTTP/SSE transport variant so the MCP server can run as a compose service.
- **Webhook/notify** — alert when a `confirmed`-fixed bug reappears (regression).
- **Backups** — for SQLite, snapshot the volume; for Postgres, `pg_dump` cron.
- **Tests** — add pytest coverage for `similarity` scoring and the dedupe flow.
```
