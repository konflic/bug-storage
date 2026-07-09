# Bug Database

A tiny local service that stores **bug reports** found while analyzing Kubernetes
clusters, so they're remembered across runs. Every time a bug is seen again it's
matched to the existing record (instead of creating a duplicate), and the service
keeps track of how often it recurs, when it was last seen, and whether it's been
fixed and confirmed.

It's a small **FastAPI** app on **SQLite** (with a one-line switch to Postgres),
packaged to run with **Docker**. You interact with it three ways:

- a command-line tool (`bugctl`) — easiest for humans,
- a REST API with interactive docs — for scripting/integration,
- an MCP server — so an AI agent can use it natively (see `AGENT.md`).

> This README is for **humans**. If you're configuring an AI agent to use the
> database, read **[AGENT.md](./AGENT.md)** instead.

---

## Why it exists

When you investigate cluster problems you keep hitting the *same* bugs. Without a
memory you re-diagnose them every time. This service gives you that memory:

- **Store once, count many.** Each distinct bug is one record; repeated sightings
  bump a counter and a "last seen" timestamp instead of piling up duplicates.
- **Automatic dedupe.** Before saving, it checks for an already-known similar bug
  (exact identity match first, then fuzzy text similarity).
- **Track the lifecycle.** Mark bugs as floating (intermittent), fixed, and
  confirmed-fixed; see full history of when/where each was observed.

---

## Quick start

### 1. Start the service (Docker — recommended)
```bash
docker compose up -d --build
curl http://localhost:8000/health      # {"status":"ok",...}
```
Your data is stored in a Docker volume (`bugdata`), so it survives restarts.

Prefer no Docker? See [Running without Docker](#running-without-docker).

### 2. Bulk-import issue files (optional)
If you have a directory of plain-text issue reports using the
`TITLE / SHORT DESCRIPTION / FULL DESCRIPTION / SUGGESTED FIX / STEPS TO REPRODUCE`
layout, import them all at once:
```bash
./bugctl import-dir <path-to-folder>
```

### 3. Use it
```bash
./bugctl list                    # see what's stored
./bugctl get 1                   # full details of bug #1
```

> **Note:** `bugctl` needs Python with the `httpx` package. If you ran the
> Docker option only, the easiest path is to create the local virtualenv shown
> in [Running without Docker](#running-without-docker) just for the CLI, or run
> commands inside the container: `docker compose exec api python bugctl list`.

---

## Everyday usage

`bugctl` is the human-friendly command-line tool. By default it talks to
`http://localhost:8000`; override with `--api` or the `BUGDB_API` env var.

### Record a bug you found
Use `report` — it will either create a new bug or attach your sighting to a
matching existing one:
```bash
./bugctl report \
  --title "KafkaTopic finalizer never removed when Kafka cluster deleted first" \
  --short "Namespace stuck Terminating; topic finalizer never cleared" \
  --component strimzi \
  --finalizer strimzi.io/topic-operator \
  --cluster 12345 \
  --floating
```
- New bug → prints `[NEW] created bug #N`.
- Looks like an existing one → prints `[DUP] matched bug #N` and records the
  sighting (its `times_seen` goes up and `last_seen` is refreshed).

### Check if something is already known (no changes made)
```bash
./bugctl search --title "namespace stuck terminating bucket finalizer"
```
Prints ranked matches with a similarity `score` and the match `reason`.

### List and inspect
```bash
./bugctl list                              # newest sighting first
./bugctl list --floating                   # only intermittent bugs
./bugctl list --status open --component strimzi
./bugctl get 4                             # one bug + its full sighting history
```

### Update status as work progresses
```bash
./bugctl update  4 --status fixed --fix "Delete KafkaTopics before the Kafka CR"
./bugctl confirm 4                          # mark fixed AND confirmed-in-the-field
```

### Log that a known bug happened again
```bash
./bugctl seen 4 --cluster 12345 --namespace my-namespace \
  --note "reappeared after operator restart"
```

Full command reference: run `./bugctl --help` or any `./bugctl <command> --help`.

---

## What gets stored

Each bug record contains:

| Field | Meaning |
|-------|---------|
| `id` | stable numeric identifier |
| `title` | one-line summary |
| `short_description` / `full_description` | the write-up |
| `steps_to_reproduce` | how to reproduce it |
| `suggested_fix` | recommended fix or mitigation |
| `component` / `finalizer` / `cluster` | where/what it affects |
| `tags` | free-form labels |
| `status` | `open` / `fixed` / `confirmed` / `wont_fix` / `duplicate` |
| `is_floating` | intermittent/flaky |
| `is_fixed` / `is_confirmed` | fix applied / fix verified |
| `times_seen` | how many times it has been encountered |
| `first_seen_at` / `last_seen_at` | first and most recent sighting |
| occurrences | full history: one entry per sighting (time, cluster, namespace, note) |

---

## The web API

The service also exposes a REST API with **interactive documentation** you can
click through in a browser:

- Open <http://localhost:8000/docs>

Main endpoints: `POST /bugs/report` (find-or-create), `POST /bugs/search`,
`GET /bugs`, `GET /bugs/{id}`, `PATCH /bugs/{id}`,
`POST /bugs/{id}/occurrences`, `DELETE /bugs/{id}`. Full list in
[AGENT.md](./AGENT.md#rest-api-summary).

---

## Use it from an AI agent (opencode / MCP)

The repo includes a small **MCP server** so an AI agent (e.g. opencode) can use
the bug database directly — searching and recording bugs for you instead of you
typing `bugctl` commands.

A ready-to-use **`opencode.json`** already lives in the repo root, so opencode
picks it up automatically when you work in this folder. It registers a `bugdb`
MCP server:

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

To use it:

1. **Keep the service running** (`docker compose up -d`) — the MCP server talks to
   the API at `BUGDB_API`.
2. **Make sure the venv exists** (it provides the `mcp` package). If you only ran
   Docker so far, create it once: see [Running without Docker](#running-without-docker).
   The paths in `opencode.json` are relative to the repo root; adjust if your
   Python lives elsewhere. If the API requires a key, set `BUGDB_API_KEY` there.
3. **Restart opencode** — config is read only at startup.

After that you can just ask the agent things like *"search the bug DB for a stuck
bucket finalizer"* or *"report this finding to the bug database"*. Full tool list
and details are in [AGENT.md](./AGENT.md#mcp-server-native-agent-integration).

---

## Running without Docker

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python index.py            # serves http://localhost:8000 with auto-reload
```
Data is stored in a local SQLite file at `./data/bugs.db`.

The `bugctl` tool uses this same virtualenv:
```bash
./bugctl health
```

---

## Configuration

Settings are environment variables (copy `.env.example` to `.env` to override):

| Variable | Default | Purpose |
|----------|---------|---------|
| `DATABASE_URL` | `sqlite:///./data/bugs.db` | where data is stored |
| `SIMILARITY_THRESHOLD` | `0.45` | how close two bugs must be to count as the same (0–1, higher = stricter) |
| `SIMILARITY_LIMIT` | `10` | max similar matches returned |
| `API_KEY` | *(empty)* | shared-secret key; empty = auth disabled (see below) |
| `AUTH_OPEN_PATHS` | `/health,/ui,/,/docs,/redoc,/openapi.json,/favicon.ico` | paths that never require a key |
| `BUGDB_API` | `http://localhost:8000` | (for `bugctl`/MCP) which server to talk to |
| `BUGDB_API_KEY` | *(empty)* | (for `bugctl`/MCP) key sent as `X-API-Key` |

---

## Authorization

The service supports **simple shared-secret authorization**. It is **off by
default** (so local/dev use needs no setup) and turns on the moment you set an
`API_KEY`.

Generate a strong key and put it in your `.env`:
```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```
```
API_KEY=<paste-the-generated-key>
```

Once set, every request must present the key via **either** header:
```bash
curl -H "X-API-Key: $API_KEY" http://localhost:8000/bugs
curl -H "Authorization: Bearer $API_KEY" http://localhost:8000/bugs
```
Requests without a valid key get `401`. The health check, web UI and OpenAPI
docs stay open (configurable via `AUTH_OPEN_PATHS`) so container probes and the
browser keep working.

The clients pick the key up from the environment automatically:
```bash
export BUGDB_API_KEY=$API_KEY
./bugctl list                       # or: ./bugctl --api-key <key> list
```
For the MCP server, add `"BUGDB_API_KEY": "<key>"` to its `environment` block
in `opencode.json`.

The **web UI** (`/ui`) prompts for the key on first use (or via the header
"🔑 Set key" button) and stores it in your browser's `localStorage`, sending it
as `X-API-Key` on every request.

### Roles: admin vs read-only

Two keys with different power:

| Key | Access | Use |
|-----|--------|-----|
| `ADMIN_API_KEY` | Full: read + all writes + `/audit` + key rotation | Your key |
| `READONLY_API_KEY` | **GET only** (writes → `403`) | Share with others |

A request can present its key in a header, a bearer token, **or a `?key=` query
param** — the last makes shareable links possible:

```
http://<host>/ui?key=<READ_ONLY_KEY>
```

Anyone opening that link gets a read-only view (edit/delete/new buttons are
hidden). In the UI, an admin can **Share read-only** (copy such a link) and
**Rotate read key** (mint a new read-only key, invalidating old links). Rotate
via API:

```bash
curl -X POST -H "X-API-Key: $ADMIN_API_KEY" http://<host>/admin/rotate-readonly-key
```

The read-only key is stored in the DB (seeded from `READONLY_API_KEY` on first
boot) so rotation works at runtime without a redeploy.

### Audit history

Every mutating action (create / update / delete / confirm / sighting / key
rotation) is recorded in an append-only audit log with **who** (role), **what**
(before/after diff for edits, snapshot for delete), and **when**. Admins view it
in the UI ("📜 Audit" / per-bug "📜 History") or via the API:

```bash
curl -H "X-API-Key: $ADMIN_API_KEY" "http://<host>/audit?limit=100"
curl -H "X-API-Key: $ADMIN_API_KEY" "http://<host>/audit?bug_id=8"
```

> Auth is intentionally minimal (one shared secret). Always pair it with HTTPS
> in production so the key isn't sent in clear text — see the deployment notes.

---

## Switching to Postgres later

The app is database-agnostic — moving to Postgres is a configuration change, not
a code change:

1. In `docker-compose.yml`, uncomment the `db` (Postgres) service and `depends_on`.
2. Set `DATABASE_URL` on the `api` service to the Postgres line.
3. `docker compose up -d --build`.

Details (and notes on adding versioned migrations before production) are in
[AGENT.md](./AGENT.md#migrating-sqlite--postgres--real-service).

---

## Project layout
```
app/             FastAPI service (models, API routes, dedupe logic, MCP server)
bugctl           command-line tool (also imports *.txt issue files)
index.py         run the service locally without Docker
Dockerfile       container image
docker-compose.yml   one-command run (SQLite now, Postgres-ready)
requirements.txt dependencies
opencode.json    opencode MCP config (lets an AI agent use the DB)
AGENT.md         guide for AI agents (MCP tools + workflow)
```

---

## Deploying to production

A one-command deploy to any Linux VM (Docker + Caddy HTTPS + API-key auth) is
described in **[DEPLOY.md](./DEPLOY.md)**. In short:

```bash
make set-key      # create deploy/.env.prod with a strong API_KEY (edit DOMAIN/email)
make deploy SSH_HOST=<your-vm-ip>  # rsync + build + start on the VM
make ping         # verify https://<domain>/health
make rotate-key   # rotate the API key later
```

---

## More

- **Production deployment:** [DEPLOY.md](./DEPLOY.md)
- **AI agent integration & full workflow:** [AGENT.md](./AGENT.md)
- **Contributing:** [CONTRIBUTING.md](./CONTRIBUTING.md)
- **Security policy:** [SECURITY.md](./SECURITY.md)
- **License:** [MIT](./LICENSE)
- **Roadmap / planned improvements:** see the end of [AGENT.md](./AGENT.md#suggested-improvements-roadmap)
