#!/usr/bin/env bash
# Back up the bug database.
#
# Run ON THE VM (or anywhere the data dir is reachable). It makes a consistent
# copy of the SQLite DB using the online-backup API (safe while the API runs),
# timestamps it, and prunes copies older than RETENTION_DAYS.
#
# Env:
#   DATA_DIR        directory holding bugs.db      (default /srv/data)
#   BACKUP_DIR      where to write backups         (default /srv/backups)
#   RETENTION_DAYS  delete backups older than this (default 14)
#
# For Postgres, replace the sqlite3 step with `pg_dump` (see comment below).
#
# Install as a daily cron with: scripts/install-backup-cron.sh
set -euo pipefail

DATA_DIR="${DATA_DIR:-/srv/data}"
BACKUP_DIR="${BACKUP_DIR:-/srv/backups}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
DB="$DATA_DIR/bugs.db"

mkdir -p "$BACKUP_DIR"
ts="$(date +%Y%m%d-%H%M%S)"
out="$BACKUP_DIR/bugs-$ts.db"

if [ ! -f "$DB" ]; then
	echo "No database at $DB — nothing to back up." >&2
	exit 0
fi

# Consistent online backup (does not corrupt a live WAL-mode DB).
sqlite3 "$DB" ".backup '$out'"
gzip -f "$out"
echo "Wrote $out.gz"

# --- Postgres alternative (uncomment, set PG* env) ---
# pg_dump "$DATABASE_URL" | gzip > "$BACKUP_DIR/bugs-$ts.sql.gz"

# Prune old backups.
find "$BACKUP_DIR" -name 'bugs-*.gz' -type f -mtime "+$RETENTION_DAYS" -delete
echo "Pruned backups older than $RETENTION_DAYS days."
