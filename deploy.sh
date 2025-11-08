#!/usr/bin/env bash
set -euo pipefail

# ----------------------------
# Config (override via env)
# ----------------------------
IMAGE_NAME="${IMAGE_NAME:-hackathon-app}"
CONTAINER_NAME="${CONTAINER_NAME:-hackathon-app}"
PORT_OUT="${PORT_OUT:-4000}"     # host port
PORT_IN="${PORT_IN:-8000}"       # container port (FastAPI)

# Updater controls (won't stop the container)
RUN_UPDATER="${RUN_UPDATER:-1}"  # 1=run updater loop after start, 0=skip
DEAL_RUNS="${DEAL_RUNS:-10}"     # how many times to run updater
DEAL_DELAY="${DEAL_DELAY:-1}"    # seconds between updater runs
UPDATER_CMD="${UPDATER_CMD:-python3 /app/improvise/back_deal.py}"  # updater command per iteration

HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-60}" # seconds to wait for /healthz

# ----------------------------
# Load .env (optional)
# ----------------------------
if [[ -f .env ]]; then
  set -o allexport
  # shellcheck disable=SC1091
  source .env
  set +o allexport
else
  echo "[deploy] NOTE: .env not found (will pass minimal defaults)."
fi

# ----------------------------
# Prepare SQLite & migrations
# ----------------------------
DB_LOCAL_PATH="${DB_PATH:-data/app.db}"
mkdir -p "$(dirname "${DB_LOCAL_PATH}")"

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "[deploy] ERROR: sqlite3 not found on host. Please install sqlite3." >&2
  exit 1
fi

have_table() {
  sqlite3 "${DB_LOCAL_PATH}" "SELECT name FROM sqlite_master WHERE type='table' AND name='$1';" | grep -q "$1"
}
have_column() {
  sqlite3 "${DB_LOCAL_PATH}" "PRAGMA table_info('$1');" | awk -F'|' '{print $2}' | grep -qx "$2"
}

# Apply 001 (if missing)
if [[ -f migrations/001_negotiation.sql ]]; then
  if ! have_table "negotiation_log"; then
    echo "[deploy] Applying migrations/001_negotiation.sql"
    sqlite3 "${DB_LOCAL_PATH}" < migrations/001_negotiation.sql
  else
    echo "[deploy] migrations/001 already applied — skipping"
  fi
fi

# Apply 002 (if any expected column missing)
if [[ -f migrations/002_negotiation_columns.sql ]]; then
  NEED_002=0
  have_table "negotiation_log" || NEED_002=1
  if [[ $NEED_002 -eq 0 ]]; then
    for col in deal_id turn phase; do
      if ! have_column "negotiation_log" "$col"; then NEED_002=1; break; fi
    done
  fi
  if [[ $NEED_002 -eq 1 ]]; then
    echo "[deploy] Applying migrations/002_negotiation_columns.sql"
    sqlite3 "${DB_LOCAL_PATH}" < migrations/002_negotiation_columns.sql || true
  else
    echo "[deploy] migrations/002 columns present — skipping"
  fi
fi

# ----------------------------
# Build image
# ----------------------------
echo "[deploy] Building Docker image: ${IMAGE_NAME}"
docker build -t "${IMAGE_NAME}" .

# ----------------------------
# Remove old container (if any)
# ----------------------------
echo "[deploy] Removing existing container (if any): ${CONTAINER_NAME}"
docker rm -f "${CONTAINER_NAME}" 2>/dev/null || true

# ----------------------------
# Run container (React+API)
# ----------------------------
DOCKER_ARGS=(
  -d
  --name "${CONTAINER_NAME}"
  -p "${PORT_OUT}:${PORT_IN}"
  -v "${PWD}/data:/data"
  -e "DB_PATH=/data/app.db"
)

# Optional API secret
if [[ -d "${PWD}/api_secret" ]]; then
  DOCKER_ARGS+=( -v "${PWD}/api_secret:/app/api_secret:ro" -e "API_KEY_FILE_PATH=/app/api_secret/api_key.conf" )
fi

# Env file or minimal defaults
if [[ -f .env ]]; then
  DOCKER_ARGS+=( --env-file .env )
else
  echo "[deploy] .env missing; using minimal defaults."
  DOCKER_ARGS+=(
    -e "RPC_URL=${RPC_URL:-https://rpc.testnet.arc.network}"
    -e "CHAIN_ID=${CHAIN_ID:-5042002}"
    -e "TOKEN_ADDR=${TOKEN_ADDR:-0x70D758FdFd1Ae0d4Fb2682f50d0228Cd4B07c449}"
  )
fi

echo "[deploy] Starting container…"
docker run "${DOCKER_ARGS[@]}" "${IMAGE_NAME}"

echo
echo "Project is running!"
echo "App:     http://localhost:${PORT_OUT}"
echo "Health:  http://localhost:${PORT_OUT}/healthz"
echo

# ----------------------------
# Health check (non-fatal)
# ----------------------------
echo "[deploy] Waiting for health (timeout ${HEALTH_TIMEOUT}s)…"
for i in $(seq 1 "${HEALTH_TIMEOUT}"); do
  if curl -fsS "http://localhost:${PORT_OUT}/healthz" >/dev/null 2>&1; then
    echo "[deploy] Health OK at t=${i}s"
    break
  fi
  sleep 1
done

# ----------------------------
# Optional bounded updater loop
# (container keeps running)
# ----------------------------
if [[ "${RUN_UPDATER}" -eq 1 ]]; then
  if [[ "${DEAL_RUNS}" -gt 0 ]]; then
    echo "[deploy] Running bounded updater loop: DEAL_RUNS=${DEAL_RUNS}, DEAL_DELAY=${DEAL_DELAY}s"
    for i in $(seq 1 "${DEAL_RUNS}"); do
      echo "[deploy] Updater iteration ${i}/${DEAL_RUNS}: docker exec ${CONTAINER_NAME} ${UPDATER_CMD}"
      if ! docker exec "${CONTAINER_NAME}" bash -lc "${UPDATER_CMD}"; then
        echo "[deploy] WARN: updater iteration ${i} failed (continuing)"; fi
      sleep "${DEAL_DELAY}"
    done
    echo "[deploy] Updater loop finished; leaving container running."
  else
    echo "[deploy] DEAL_RUNS<=0 → skipping updater loop."
  fi
else
  echo "[deploy] RUN_UPDATER=0 → not running updater."
fi

echo
echo "[deploy] Done. Container is still running."
echo "Tail logs:   docker logs -f ${CONTAINER_NAME}"
echo "Open app:    http://localhost:${PORT_OUT}"