# Contributing

Thanks for your interest in improving the Bug Database service! This is a small
FastAPI + SQLite project — contributions of all sizes are welcome.

## Development setup

```bash
git clone <your-fork-url>
cd bugs
make venv          # create .venv and install requirements
make dev           # run the API locally at http://localhost:8000 (auto-reload)
```

Verify it works:
```bash
curl http://localhost:8000/health
./bugctl health
```

## Making changes

- **Code style:** standard PEP 8; keep functions small and typed (the codebase
  uses `from __future__ import annotations` and PEP 604 unions).
- **Layout:**
  - `app/main.py` — routes
  - `app/crud.py` — business logic
  - `app/models.py` / `app/schemas.py` — ORM + Pydantic
  - `app/similarity.py` — dedupe scoring
  - `app/mcp_server.py` — MCP tools (thin client over the HTTP API)
- **Database changes:** update `app/models.py` and add an idempotent step to
  `ensure_schema()` in `app/database.py` (the lightweight migration stand-in).
  For anything non-trivial, consider adding Alembic (see `AGENT.md`).
- **Docs:** update `README.md` (humans), `AGENT.md` (agent/MCP), and
  `CHANGELOG.md` when behavior changes.
- **No secrets or environment-specific data** in commits. Do not hardcode
  hostnames, IPs, cluster ids, tokens, or absolute local paths — use env vars,
  placeholders, or `*.example` files.

## Before opening a PR

```bash
# quick smoke check (mirrors CI)
API_KEY=test DATABASE_URL=sqlite:///./data/test.db python - <<'PY'
from fastapi.testclient import TestClient
from app.main import app
c = TestClient(app)
assert c.get("/health").status_code == 200
assert c.get("/bugs").status_code == 401
assert c.get("/bugs", headers={"X-API-Key": "test"}).status_code == 200
print("ok")
PY

docker build -t bugdb:dev .   # ensure the image builds
```

CI (`.github/workflows/ci.yml`) runs the same smoke test and a Docker build on
every push and pull request.

## Pull request guidelines

- Keep PRs focused; one logical change per PR.
- Describe **what** and **why**, and how you tested it.
- Update relevant docs and `CHANGELOG.md`.

## Reporting bugs / requesting features

Open a GitHub issue with reproduction steps (for bugs) or a clear use case (for
features). Security issues: please follow [SECURITY.md](./SECURITY.md) instead
of filing a public issue.
