#!/usr/bin/env bash
# Nightly platform backup: both databases, GLPI documents, Git repositories, Semaphore and
# Grafana data, the secrets folder and the configuration. Run from cron on the platform host:
#   15 0 * * *  /opt/ioh-msp-stack/scripts/backup.sh >> /var/log/msp-backup.log 2>&1
#
# Copy the result off the host (the guide shows restic to object storage). Keep at least one
# restore test per quarter: a backup that was never restored is only a hope.
set -euo pipefail
cd "$(dirname "$0")/.."

BACKUP_ROOT=${BACKUP_ROOT:-/var/backups/msp}
KEEP_DAYS=${KEEP_DAYS:-14}
PROJECT=${COMPOSE_PROJECT_NAME:-$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' .env 2>/dev/null | tail -n 1)}
PROJECT=${PROJECT:-$(basename "$PWD")}
DEST="$BACKUP_ROOT/$(date +%Y%m%d-%H%M)"
mkdir -p "$DEST"
chmod 700 "$BACKUP_ROOT" "$DEST"

echo "$(date -Is) backup to $DEST"

# PostgreSQL: Nautobot, Zabbix, Semaphore, Forgejo, Keycloak, Grafana in one consistent dump.
docker compose exec -T postgres pg_dumpall -U postgres | gzip > "$DEST/postgres.sql.gz"

# MariaDB: GLPI.
docker compose exec -T mariadb sh -c \
  'mariadb-dump -uroot -p"$MARIADB_ROOT_PASSWORD" --single-transaction --routines --triggers --all-databases' \
  | gzip > "$DEST/mariadb.sql.gz"

# File volumes (only those that exist on this host). caddy_data holds the reverse proxy's own
# certificate authority: without it, every NOC PC would need a new caddy-root.crt after a rebuild.
for vol in glpi_data forgejo_data semaphore_data grafana_data caddy_data; do
  if docker volume inspect "${PROJECT}_${vol}" >/dev/null 2>&1; then
    docker run --rm -v "${PROJECT}_${vol}:/data:ro" -v "$DEST:/backup" alpine:3.22 \
      tar czf "/backup/${vol}.tar.gz" -C /data .
  fi
done

# Configuration and secrets (encrypt the backup target: these files hold every password), plus
# IOH certificates for the reverse proxy when they are installed.
config=(.env secrets glue/mappings.yaml)
[ -f components/proxy/certs/privkey.pem ] && config+=(components/proxy/certs)
tar czf "$DEST/config.tar.gz" "${config[@]}"

# Optional off-host copy with restic (set RESTIC_REPOSITORY and RESTIC_PASSWORD_FILE).
if [ -n "${RESTIC_REPOSITORY:-}" ]; then
  restic backup "$DEST" --tag msp-platform
  restic forget --tag msp-platform --keep-daily 14 --keep-weekly 8 --keep-monthly 6 --prune
fi

# Local retention.
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" -exec rm -rf {} +

du -sh "$DEST"
echo "$(date -Is) backup finished"
