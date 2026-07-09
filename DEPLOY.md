# Deploying to production

Deploy the Bug Database API to a Linux VM running Docker, with Caddy providing
automatic HTTPS and the built-in API-key auth for access control. Everything is
automated through the `Makefile`.

Architecture:

```
Internet ──443──> Caddy (TLS, Let's Encrypt) ──8000 (internal)──> FastAPI (api)
                                                                     │
                                                              /srv/data (SQLite,
                                                              persistent disk)
```

---

## Prerequisites

**On the VM** (any cloud provider or bare-metal):
- Ubuntu/Debian (or similar), with Docker and Docker Compose installed.
- A persistent disk mounted at `/srv/data` (for the SQLite database).
- Port 80 and 443 open (for Caddy / Let's Encrypt).

**On your machine:**
- `docker`, `rsync`, `ssh`.
- An SSH key pair with access to the VM.
- A domain name you can point at the VM (an A record).

---

## Step-by-step

### 1. Provision a VM

Create a VM with your cloud provider of choice. Install Docker:

```bash
ssh ubuntu@<vm-ip>
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
sudo mkdir -p /srv/data
```

### 2. Point your domain at the VM

Create an **A record**: `bugs.example.com -> <vm-ip>`.
Wait for DNS to propagate (`dig bugs.example.com`). Caddy needs this to issue
the TLS certificate.

### 3. Configure the deploy env + API key
```bash
make set-key                  # creates deploy/.env.prod with a strong API_KEY
```
Then edit `deploy/.env.prod` and set `DOMAIN` and `ACME_EMAIL`.

### 4. Deploy
```bash
make deploy SSH_HOST=<vm-ip>  # rsyncs the repo to the VM, builds, starts stack
```

### 5. Verify
```bash
make ping SSH_HOST=<vm-ip>    # GET https://<domain>/health -> {"status":"ok",...}
```

### 6. Use it (clients must send the key)
```bash
export BUGDB_API=https://bugs.example.com
export BUGDB_API_KEY=$(make -s show-key)
./bugctl health
./bugctl list
```
For the MCP server, add `"BUGDB_API_KEY": "<key>"` to its `environment` in
`opencode.json` and point `BUGDB_API` at the public URL.

---

## Migrating existing local data to the VM

Both local and prod use SQLite, so migrating is just copying the DB file to the
VM's persistent `/srv/data` disk. One command automates it (snapshot → stop API
→ copy → restart):

```bash
make seed-remote SSH_HOST=<vm-ip>                  # uses ./data/bugs.db by default
make seed-remote SSH_HOST=<vm-ip> LOCAL_DB=path/to/other.db
```

It asks for a `yes` confirmation because it **overwrites** the remote database.
It makes a consistent snapshot first (safe even if the local app is running) and
clears stale WAL/SHM sidecar files on the target. The app auto-upgrades the
schema on restart, so a DB from an older version is fine.

Verify afterwards:
```bash
make ping SSH_HOST=<vm-ip>
BUGDB_API=https://<domain> BUGDB_API_KEY=$(make -s show-key) ./bugctl list
```

> This is for **SQLite → SQLite**. Migrating to PostgreSQL is not a file
> copy — replay the data through the API instead (ask for a transfer script).

---

## Updating the API key (rotation)

One command generates a new key, pushes it to the server, and restarts the API:
```bash
make rotate-key SSH_HOST=<vm-ip>
```
Then update your clients' `BUGDB_API_KEY` (and the MCP `opencode.json`) with the
new value — `make show-key` prints it. Old keys stop working immediately.

To just see or generate keys without deploying:
```bash
make gen-key      # print a random key, save nothing
make show-key     # show the key currently in deploy/.env.prod
```

---

## Day-2 operations

| Command | What it does |
|---------|--------------|
| `make deploy SSH_HOST=<ip>` | Rebuild + restart after code changes |
| `make logs SSH_HOST=<ip>` | Tail remote container logs |
| `make ps SSH_HOST=<ip>` | Remote container status |
| `make ssh SSH_HOST=<ip>` | SSH into the VM |
| `make down SSH_HOST=<ip>` | Stop the stack |

### Backups

- **App-level dumps (built in):**
  ```bash
  make install-backup-cron SSH_HOST=<ip>  # daily 03:30 backup -> /srv/backups
  make backup SSH_HOST=<ip>               # run a one-off backup now
  make fetch-backups SSH_HOST=<ip>        # pull remote backups into ./backups locally
  ```
  Uses `scripts/backup.sh` (SQLite online-backup + gzip + 14-day retention).
- **Disk snapshots:** the DB lives on the persistent `/srv/data` disk; snapshot
  it with your cloud provider's snapshot API on a schedule.

### Continuous deployment (GitHub Actions)

`.github/workflows/deploy.yml` deploys on every push to `main`. Add these
repository secrets (Settings → Secrets and variables → Actions): `SSH_HOST`,
`SSH_USER`, `SSH_PRIVATE_KEY`, `DOMAIN`, `ACME_EMAIL`, `API_KEY`. Nothing
environment-specific is committed — it all comes from secrets.
`.github/workflows/ci.yml` runs a smoke test + Docker build on every push/PR.

---

## Switching to PostgreSQL (optional, for scale/managed backups)

SQLite is single-writer (one API replica). To move to Postgres:

- **Managed option:** create a managed PostgreSQL instance with your cloud
  provider, then set in `deploy/.env.prod`:
  ```
  DATABASE_URL=postgresql+psycopg://user:pass@<host>:6432/bugs
  ```
  and uncomment the `DATABASE_URL` Postgres line in
  `deploy/docker-compose.prod.yml`. Redeploy. No code changes.
- **Self-hosted option:** uncomment the `db` service block in
  `deploy/docker-compose.prod.yml`, set `POSTGRES_PASSWORD` in `.env.prod`.

> Before heavy production use, add Alembic migrations (see `AGENT.md`).

---

## Security notes

- API-key auth is a single shared secret — good for a small internal API, but
  there's no per-user revocation; rotating means updating every client.
- Always keep it behind HTTPS (Caddy does this) so the key isn't sent in clear.
- `deploy/.env.prod` is git-ignored — never commit it.
  For stronger secret handling, store `API_KEY` in a secrets manager.
- Restrict SSH access to your own IP using your cloud provider's firewall rules.
