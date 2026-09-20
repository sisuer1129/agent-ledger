#!/usr/bin/env bash
set -Eeuo pipefail

CONTAINER="${CONTAINER:-finance-api}"
HOST_ROOT="${HOST_ROOT:-/volume1/docker/finance-api}"
COMPOSE_FILE="$HOST_ROOT/docker-compose.yml"
HOST_DB="$HOST_ROOT/config/finance.db"
PACKAGE_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_DIR="$HOST_ROOT/backups/$STAMP"

rollback_hint() {
    echo "Deployment stopped. Application startup may have changed the database; inspect container logs before recovery." >&2
    echo "After checking that the backup is complete, restore with:" >&2
    echo "  sudo bash $PACKAGE_DIR/scripts/rollback.sh $BACKUP_DIR" >&2
}

trap rollback_hint ERR

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

[[ -d "$PACKAGE_DIR/app" && -d "$PACKAGE_DIR/frontend" && -f "$PACKAGE_DIR/requirements.txt" ]] || {
    echo "Package is incomplete." >&2
    exit 2
}
[[ -f "$COMPOSE_FILE" && -f "$HOST_DB" && -d "$HOST_ROOT/app" && -d "$HOST_ROOT/frontend" ]] || {
    echo "Missing compose file, database, or Docker build context." >&2
    exit 2
}
grep -q '^[[:space:]]*-[[:space:]]*FINANCE_DB_PATH=/app/config/finance.db[[:space:]]*$' "$COMPOSE_FILE" || {
    echo "Refusing deployment: docker-compose.yml must set FINANCE_DB_PATH=/app/config/finance.db." >&2
    exit 2
}
[[ ! -e "$BACKUP_DIR" ]] || { echo "Backup path already exists: $BACKUP_DIR" >&2; exit 2; }

docker inspect --type container "$CONTAINER" >/dev/null
docker compose -f "$COMPOSE_FILE" config -q
mkdir -p "$BACKUP_DIR"
# Stop the only database writer before copying SQLite and any uncheckpointed WAL.
docker stop "$CONTAINER" >/dev/null
cp -p "$HOST_DB" "$BACKUP_DIR/finance.db"
for suffix in -wal -shm; do
    if [[ -f "$HOST_DB$suffix" ]]; then
        cp -p "$HOST_DB$suffix" "$BACKUP_DIR/finance.db$suffix"
    fi
done
cp -p "$HOST_ROOT/requirements.txt" "$BACKUP_DIR/requirements.txt"
cp -a "$HOST_ROOT/app" "$BACKUP_DIR/build-context-app"
cp -a "$HOST_ROOT/frontend" "$BACKUP_DIR/build-context-frontend"
docker cp "$CONTAINER:/app/app" "$BACKUP_DIR/container-app"
docker cp "$CONTAINER:/app/frontend" "$BACKUP_DIR/container-frontend"
echo "Backup created: $BACKUP_DIR"

# Install the complete build context. Docker Compose then builds the same code
# that is in this package; no container-only copy is relied upon.
rm -rf "$HOST_ROOT/app" "$HOST_ROOT/frontend"
cp -a "$PACKAGE_DIR/app" "$HOST_ROOT/app"
cp -a "$PACKAGE_DIR/frontend" "$HOST_ROOT/frontend"
cp -p "$PACKAGE_DIR/requirements.txt" "$HOST_ROOT/requirements.txt"

# Application startup runs its existing idempotent schema initialization. This
# package never invokes historical classification or taxonomy migration tools.
docker compose -f "$COMPOSE_FILE" build "$CONTAINER"
docker compose -f "$COMPOSE_FILE" up -d --force-recreate --no-build "$CONTAINER"
health_check
docker exec "$CONTAINER" sh -ec 'test "${FINANCE_DB_PATH:-}" = /app/config/finance.db'

trap - ERR
echo "Deployment healthy. Backup: $BACKUP_DIR"
