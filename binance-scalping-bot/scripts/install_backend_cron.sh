#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_SCRIPT="$ROOT_DIR/scripts/backend_service.sh"
RUNTIME_DIR="$ROOT_DIR/backend/.runtime"
CRON_LOG="$RUNTIME_DIR/cron_restart.log"
CRON_TAG="# binance-scalping-bot-backend-restart"
DEFAULT_INTERVAL_HOURS=2

mkdir -p "$RUNTIME_DIR"

validate_hours() {
  local value="$1"
  if ! [[ "$value" =~ ^[0-9]+$ ]]; then
    echo "Invalid interval hours: $value"
    exit 1
  fi
  if [[ "$value" -lt 1 || "$value" -gt 23 ]]; then
    echo "Interval hours must be in range 1..23."
    exit 1
  fi
}

build_cron_line() {
  local interval_hours="$1"
  echo "0 */$interval_hours * * * /bin/bash \"$SERVICE_SCRIPT\" restart >> \"$CRON_LOG\" 2>&1 $CRON_TAG"
}

install_cron() {
  local interval_hours="${1:-$DEFAULT_INTERVAL_HOURS}"
  validate_hours "$interval_hours"
  local new_line
  new_line="$(build_cron_line "$interval_hours")"
  (
    crontab -l 2>/dev/null | grep -vF "$CRON_TAG" || true
    echo "$new_line"
  ) | crontab -
  echo "Installed cron auto-restart every ${interval_hours}h"
  echo "Log: $CRON_LOG"
}

uninstall_cron() {
  (
    crontab -l 2>/dev/null | grep -vF "$CRON_TAG" || true
  ) | crontab -
  echo "Removed cron auto-restart"
}

status_cron() {
  local line
  line="$(crontab -l 2>/dev/null | grep -F "$CRON_TAG" || true)"
  if [[ -z "$line" ]]; then
    echo "Cron auto-restart not installed"
  else
    echo "$line"
  fi
}

case "${1:-}" in
  install) install_cron "${2:-$DEFAULT_INTERVAL_HOURS}" ;;
  uninstall) uninstall_cron ;;
  status) status_cron ;;
  *)
    echo "Usage: scripts/install_backend_cron.sh <install [interval_hours]|uninstall|status>"
    exit 1
    ;;
esac
