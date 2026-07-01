# Deploying to Yandex Cloud

Deploy the Bug Database API to a single Yandex Cloud VM running Docker, with
Caddy providing automatic HTTPS and the built-in API-key auth for access
control. Everything is automated through the `Makefile`.

Architecture:

```
Internet ──443──> Caddy (TLS, Let's Encrypt) ──8000 (internal)──> FastAPI (api)
                                                                     │
                                                              /srv/data (SQLite,
                                                              persistent disk)
```

---

## Prerequisites (on your machine)

- `terraform`, `docker`, `rsync`, `ssh`, and the `yc` CLI (for auth).
- An SSH key pair (e.g. `~/.ssh/id_ed25519.pub`).
- A domain name you can point at the VM (an A record).
- A Yandex Cloud account with a `cloud_id` and `folder_id`.

Authenticate the Terraform provider once:
```bash
yc init                       # or export a service-account key
export YC_TOKEN=$(yc iam create-token)
```

---

## Step-by-step

### 1. Provision the VM
```bash
cd deploy/terraform
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: cloud_id, folder_id, ssh key path,
# and lock ssh_allowed_cidrs to your IP (curl ifconfig.me)
cd ../..

make tf-init
make tf-apply                 # prints the VM's public_ip when done
```

### 2. Point your domain at the VM
Create an **A record**: `bugs.example.com -> <public_ip from tf output>`.
Wait for DNS to propagate (`dig bugs.example.com`). Caddy needs this to issue
the TLS certificate.

### 3. Configure the deploy env + API key
```bash
make set-key                  # creates deploy/.env.prod with a strong API_KEY
```
Then edit `deploy/.env.prod` and set `DOMAIN` and `ACME_EMAIL`.

### 4. Deploy
```bash
make deploy                   # rsyncs the repo to the VM, builds, starts stack
```
(The VM's Docker + `/srv/data` disk were set up automatically by cloud-init.)

### 5. Verify
```bash
make ping                     # GET https://<domain>/health -> {"status":"ok",...}
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

## Updating the API key (rotation)

One command generates a new key, pushes it to the server, and restarts the API:
```bash
make rotate-key
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
| `make deploy` | Rebuild + restart after code changes |
| `make logs` | Tail remote container logs |
| `make ps` | Remote container status |
| `make ssh` | SSH into the VM |
| `make down` | Stop the stack |
| `make tf-destroy` | Delete all cloud resources |

### Backups

Two options (use both for defense in depth):

- **App-level dumps (built in):**
  ```bash
  make install-backup-cron    # daily 03:30 backup on the VM -> /srv/backups
  make backup                 # run a one-off backup now
  make fetch-backups          # pull remote backups into ./backups locally
  ```
  Uses `scripts/backup.sh` (SQLite online-backup + gzip + 14-day retention).
- **Disk snapshots:** the DB lives on the persistent `/srv/data` disk; snapshot
  it from the YC console or `yc compute snapshot create` on a schedule.

### Continuous deployment (GitHub Actions)

`.github/workflows/deploy.yml` deploys on every push to `main`. Add these
repository secrets (Settings → Secrets and variables → Actions): `SSH_HOST`,
`SSH_USER`, `SSH_PRIVATE_KEY`, `DOMAIN`, `ACME_EMAIL`, `API_KEY`. Nothing
environment-specific is committed — it all comes from secrets.
`.github/workflows/ci.yml` runs a smoke test + Docker build on every push/PR.

---

## Switching to Managed PostgreSQL (optional, for scale/managed backups)

SQLite is single-writer (one API replica). To move to Postgres:

- **Managed option:** create a *Managed Service for PostgreSQL* cluster in YC,
  then set in `deploy/.env.prod`:
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
- `deploy/.env.prod` and `terraform.tfvars` are git-ignored — never commit them.
  For stronger secret handling, store `API_KEY` in **YC Lockbox**.
- SSH is restricted by `ssh_allowed_cidrs`; keep it locked to your IP.
```
