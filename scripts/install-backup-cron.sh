#!/usr/bin/env bash
# Install a daily cron job (03:30) that runs scripts/backup.sh.
# Run this ON THE VM, once, e.g.:  sudo bash scripts/install-backup-cron.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CRON_FILE="/etc/cron.d/bugdb-backup"

cat > "$CRON_FILE" <<EOF
# Daily bug DB backup (managed by install-backup-cron.sh)
SHELL=/bin/bash
30 3 * * * root DATA_DIR=/srv/data BACKUP_DIR=/srv/backups RETENTION_DAYS=14 $SCRIPT_DIR/backup.sh >> /var/log/bugdb-backup.log 2>&1
EOF

chmod 0644 "$CRON_FILE"
echo "Installed $CRON_FILE (daily at 03:30). Logs: /var/log/bugdb-backup.log"
