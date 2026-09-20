#!/usr/bin/env bash
set -Eeuo pipefail

CONTAINER="${CONTAINER:-finance-api}"
HOST_ROOT="${HOST_ROOT:-/volume1/docker/finance-api}"
COMPOSE_FILE="$HOST_ROOT/docker-compose.yml"
HOST_DB="$HOST_ROOT/config/finance.db"
BACKUP_DIR="${1:-}"

health_check() {
    local attempt
    for attempt in $(seq 1 15); do
        if curl --fail --silent --show-error --max-time 5 http://127.0.0.1:5009/health >/dev/null; then
            return 0
        fi
        sleep 2
    done
    return 1
}

[[ -n "$BACKUP_DIR" ]] || {
    echo "Usage: $0 /volume1/docker/finance-api/backups/<timestamp>" >&2
    exit 2
}
case "$BACKUP_DIR" in
    /|/volume1|/volume1/docker|"$HOST_ROOT"|"$HOST_ROOT"/backups) echo "Unsafe backup path." >&2; exit 2 ;;
    "$HOST_ROOT"/backups/*) ;;
    *) echo "Backup must be under $HOST_ROOT/backups/." >&2; exit 2 ;;
esac
[[ -f "$BACKUP_DIR/finance.db" && -f "$BACKUP_DIR/requirements.txt" && -d "$BACKUP_DIR/build-context-app" && -d "$BACKUP_DIR/build-context-frontend" ]] || {
    echo "Invalid deployment backup: $BACKUP_DIR" >&2
    exit 2
}

docker compose -f "$COMPOSE_FILE" config -q
# Preserve the state being replaced, including WAL, before restoring old data.
# mktemp avoids overwriting a recovery snapshot on repeated attempts.
RECOVERY_DIR="$(mktemp -d "$HOST_ROOT/backups/pre-rollback-XXXXXXXX")"
docker stop "$CONTAINER" >/dev/null
for suffix in '' -wal -shm; do
    if [[ -f "$HOST_DB$suffix" ]]; then
        cp -p "$HOST_DB$suffix" "$RECOVERY_DIR/finance.db$suffix"
    fi
done
echo "Current database preserved: $RECOVERY_DIR"
rm -rf "$HOST_ROOT/app" "$HOST_ROOT/frontend"
cp -a "$BACKUP_DIR/build-context-app" "$HOST_ROOT/app"
cp -a "$BACKUP_DIR/build-context-frontend" "$HOST_ROOT/frontend"
cp -p "$BACKUP_DIR/requirements.txt" "$HOST_ROOT/requirements.txt"
# A WAL from the replaced database must never be replayed onto the restored one.
rm -f "$HOST_DB-wal" "$HOST_DB-shm"
cp -p "$BACKUP_DIR/finance.db" "$HOST_DB"
for suffix in -wal -shm; do
    if [[ -f "$BACKUP_DIR/finance.db$suffix" ]]; then
        cp -p "$BACKUP_DIR/finance.db$suffix" "$HOST_DB$suffix"
    fi
done
docker compose -f "$COMPOSE_FILE" build "$CONTAINER"
docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-build "$CONTAINER"
health_check

echo "Rollback healthy: $BACKUP_DIR"
