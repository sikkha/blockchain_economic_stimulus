#!/usr/bin/env bash
set -euo pipefail

# --- helpers to keep values clean ---
trim() { awk '{$1=$1;print}' <<<"$1"; }
strip_comment() { sed 's/[[:space:]]*#.*$//' <<<"$1"; }

# Defaults
: "${RUN_AGENT_ON_START:=1}"
: "${AGENT_INTERVAL_SECONDS:=0}"
: "${DB_PATH:=data/app.db}"
: "${AGENT_SCRIPT:=/app/improvise/agent_settle.py}"

# Strip trailing comments/spaces (in case env lines had them)
RUN_AGENT_ON_START="$(strip_comment "$RUN_AGENT_ON_START")"
AGENT_INTERVAL_SECONDS="$(strip_comment "$AGENT_INTERVAL_SECONDS")"
AGENT_SCRIPT="$(strip_comment "$AGENT_SCRIPT")"
DB_PATH="$(strip_comment "$DB_PATH")"

echo "[start] ==========================================="
echo "[start] Boot sequence initiated..."
echo "[start] DB_PATH=${DB_PATH}"
echo "[start] AGENT_SCRIPT=${AGENT_SCRIPT}"
echo "[start] RUN_AGENT_ON_START=${RUN_AGENT_ON_START}"
echo "[start] AGENT_INTERVAL_SECONDS=${AGENT_INTERVAL_SECONDS}"
echo "[start] ==========================================="

mkdir -p "$(dirname "$DB_PATH")"

run_agent_once() {
  echo "[agent] Launching: ${AGENT_SCRIPT}"
  python "${AGENT_SCRIPT}" || echo "[agent] WARNING: agent crashed or exited with error (continuing)"
}

is_int='^[0-9]+$'
if [[ "${RUN_AGENT_ON_START}" == "1" ]]; then
  if [[ "${AGENT_INTERVAL_SECONDS}" =~ ${is_int} ]] && [[ "${AGENT_INTERVAL_SECONDS}" -gt 0 ]]; then
    (
      while true; do
        run_agent_once
        echo "[agent] Sleeping ${AGENT_INTERVAL_SECONDS}s..."
        sleep "${AGENT_INTERVAL_SECONDS}"
      done
    ) &
  else
    run_agent_once &
  fi
else
  echo "[start] RUN_AGENT_ON_START=0 (agent auto-run disabled)"
fi

echo "[start] Launching FastAPI (uvicorn)..."
exec uvicorn backend.main:app --host 0.0.0.0 --port 8000
