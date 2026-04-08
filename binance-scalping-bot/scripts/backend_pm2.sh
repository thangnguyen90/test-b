#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
UVICORN_BIN="$ROOT_DIR/.venv/bin/uvicorn"
ENV_FILE="$BACKEND_DIR/.env"

read_env_value() {
  local key="$1"
  local file="$2"
  [[ -f "$file" ]] || return 1
  grep -E "^[[:space:]]*${key}=" "$file" | tail -n 1 | cut -d= -f2- | tr -d '[:space:]'
}

HOST="${HOST:-}"
PORT="${PORT:-}"

if [[ -z "$HOST" ]]; then
  HOST="$(read_env_value HOST "$ENV_FILE" || true)"
fi

if [[ -z "$PORT" ]]; then
  PORT="$(read_env_value PORT "$ENV_FILE" || true)"
fi

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"

mkdir -p "$BACKEND_DIR/.runtime/pm2"

if [[ ! -x "$UVICORN_BIN" ]]; then
  echo "Missing uvicorn binary: $UVICORN_BIN"
  exit 1
fi

cd "$BACKEND_DIR"
exec "$UVICORN_BIN" app.main:app --host "$HOST" --port "$PORT" --no-access-log
