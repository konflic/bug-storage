#!/usr/bin/env bash
# Volume-aware backup for the bug DB running via docker compose.
#
# Use this when the SQLite DB lives inside a Docker named volume (mounted at
# /srv/data in the api container) rather than on a host path. It dumps a
# consistent snapshot from inside the running container using SQLite's online
# backup API (WAL-safe), gzips it, and prunes copies older than RETENTION_DAYS.
#
# Env:
#   CONTAINER       api container name             (default bug-storage-api-1)
#   BACKUP_DIR      where to write backups         (default ./backups on host)
#   RETENTION_DAYS  delete backups older than this (default 14)
#
# Install as a daily cron with scripts/install-backup-cron.sh (adjust the
# script path/CONTAINER there to match this deployment).
set -euo pipefail

CONTAINER="${CONTAINER:-bugdb-api-1}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"

mkdir -p "$BACKUP_DIR"
ts="$(date +%Y%m%d-%H%M%S)"
out="$BACKUP_DIR/bugs-$ts.db"

# Consistent online backup via the container's python + sqlite3.
docker exec "$CONTAINER" python -c "import sqlite3; s=sqlite3.connect(\"/srv/data/bugs.db\"); d=sqlite3.connect(\"/tmp/backup.db\"); s.backup(d); d.close(); s.close()"
docker cp "$CONTAINER:/tmp/backup.db" "$out"
docker exec "$CONTAINER" rm -f /tmp/backup.db
gzip -f "$out"
echo "Wrote $out.gz"

# Prune old backups.
find "$BACKUP_DIR" -name "bugs-*.gz" -type f -mtime "+$RETENTION_DAYS" -delete
echo "Pruned backups older than $RETENTION_DAYS days."
