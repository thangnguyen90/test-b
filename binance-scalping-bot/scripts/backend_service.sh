#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
UVICORN_BIN="$ROOT_DIR/.venv/bin/uvicorn"
RUNTIME_DIR="$BACKEND_DIR/.runtime"
PID_FILE="$RUNTIME_DIR/backend.pid"
LOG_FILE="$RUNTIME_DIR/backend.log"
LOG_MAX_MB="${BACKEND_LOG_MAX_MB:-128}"
LOG_KEEP_FILES="${BACKEND_LOG_KEEP_FILES:-5}"
HOST="127.0.0.1"
PORT="8000"

mkdir -p "$RUNTIME_DIR"

rotate_log_if_needed() {
  local max_bytes
  local current_size
  local ts
  max_bytes=$((LOG_MAX_MB * 1024 * 1024))
  [[ -f "$LOG_FILE" ]] || return 0
  current_size="$(wc -c < "$LOG_FILE" 2>/dev/null || echo 0)"
  [[ "$current_size" =~ ^[0-9]+$ ]] || current_size=0
  if [[ "$current_size" -lt "$max_bytes" ]]; then
    return 0
  fi

  ts="$(date +%Y%m%d_%H%M%S)"
  mv "$LOG_FILE" "$LOG_FILE.$ts"
  : > "$LOG_FILE"

  find "$RUNTIME_DIR" -maxdepth 1 -type f -name 'backend.log.*' -print \
    | sort -r \
    | tail -n +$((LOG_KEEP_FILES + 1)) \
    | xargs -I {} rm -f "{}"
}

is_running() {
  if [[ ! -f "$PID_FILE" ]]; then
    return 1
  fi
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [[ -n "${pid}" ]] || return 1
  kill -0 "$pid" 2>/dev/null
}

start_backend() {
  if is_running; then
    echo "Backend already running (pid=$(cat "$PID_FILE"))"
    return 0
  fi

  if [[ ! -x "$UVICORN_BIN" ]]; then
    echo "Missing uvicorn: $UVICORN_BIN"
    exit 1
  fi

  rotate_log_if_needed

  (
    cd "$BACKEND_DIR"
    nohup "$UVICORN_BIN" app.main:app --host "$HOST" --port "$PORT" --no-access-log >>"$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
  )

  sleep 1
  if is_running; then
    echo "Backend started (pid=$(cat "$PID_FILE"))"
  else
    echo "Backend failed to start. Last log lines:"
    tail -n 40 "$LOG_FILE" || true
    exit 1
  fi
}

stop_backend() {
  local force_kill_port="${1:-false}"
  local stopped=0
  if is_running; then
    local pid
    pid="$(cat "$PID_FILE")"
    kill "$pid" 2>/dev/null || true
    sleep 1
    kill -9 "$pid" 2>/dev/null || true
    stopped=1
  fi

  if [[ "$force_kill_port" == "true" ]] && command -v lsof >/dev/null 2>&1; then
    local pids
    pids="$(lsof -ti "tcp:$PORT" 2>/dev/null || true)"
    if [[ -n "$pids" ]]; then
      echo "$pids" | xargs kill -9 2>/dev/null || true
      stopped=1
    fi
  fi

  rm -f "$PID_FILE"
  if [[ "$stopped" -eq 1 ]]; then
    echo "Backend stopped"
  else
    echo "Backend is not running"
  fi
}

status_backend() {
  if is_running; then
    local pid
    pid="$(cat "$PID_FILE")"
    echo "Backend running (pid=$pid)"
    ps -o pid,%cpu,%mem,rss,etime,command -p "$pid"
    return 0
  fi
  echo "Backend not running"
  return 1
}

health_backend() {
  curl -sS "http://$HOST:$PORT/health" || true
  echo
}

is_training_in_progress() {
  local status_json
  status_json="$(curl -fsS --max-time 2 "http://$HOST:$PORT/api/v1/ml/status" 2>/dev/null || true)"
  if [[ -z "$status_json" ]]; then
    return 1
  fi

  python3 -c 'import json,sys; d=json.loads(sys.argv[1]); sys.exit(0 if d.get("training_in_progress") else 1)' "$status_json" 2>/dev/null
}

restart_backend_safely() {
  if is_training_in_progress; then
    echo "Skip restart: ML training is in progress."
    return 0
  fi
  stop_backend false
  start_backend
}

usage() {
  cat <<'EOF'
Usage: scripts/backend_service.sh <start|stop|stop-force|restart|restart-force|status|health|logs|trim-log>
EOF
}

cmd="${1:-}"
case "$cmd" in
  start) start_backend ;;
  stop) stop_backend false ;;
  stop-force) stop_backend true ;;
  restart) restart_backend_safely ;;
  restart-force) stop_backend true; start_backend ;;
  status) status_backend ;;
  health) health_backend ;;
  logs) tail -n 200 -f "$LOG_FILE" ;;
  trim-log) : > "$LOG_FILE"; echo "Log truncated: $LOG_FILE" ;;
  *) usage; exit 1 ;;
esac
