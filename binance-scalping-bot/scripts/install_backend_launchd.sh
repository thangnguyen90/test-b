#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICE_SCRIPT="$ROOT_DIR/scripts/backend_service.sh"
RUNTIME_DIR="$ROOT_DIR/backend/.runtime"
mkdir -p "$RUNTIME_DIR"

LABEL="com.thang.binance-scalping-bot.backend-restart"
PLIST_PATH="$HOME/Library/LaunchAgents/$LABEL.plist"
STDOUT_PATH="$RUNTIME_DIR/launchd.out.log"
STDERR_PATH="$RUNTIME_DIR/launchd.err.log"
UID_VALUE="$(id -u)"
DEFAULT_INTERVAL_SEC=7200

validate_interval() {
  local value="$1"
  if ! [[ "$value" =~ ^[0-9]+$ ]]; then
    echo "Invalid interval seconds: $value"
    exit 1
  fi
  if [[ "$value" -lt 300 ]]; then
    echo "Interval too low ($value). Use >= 300 seconds."
    exit 1
  fi
}

install_agent() {
  local interval_sec="${1:-$DEFAULT_INTERVAL_SEC}"
  validate_interval "$interval_sec"

  cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$SERVICE_SCRIPT</string>
    <string>restart</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>$interval_sec</integer>
  <key>StandardOutPath</key>
  <string>$STDOUT_PATH</string>
  <key>StandardErrorPath</key>
  <string>$STDERR_PATH</string>
</dict>
</plist>
EOF

  launchctl bootout "gui/$UID_VALUE/$LABEL" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_VALUE" "$PLIST_PATH"
  launchctl enable "gui/$UID_VALUE/$LABEL"
  launchctl kickstart -k "gui/$UID_VALUE/$LABEL"
  echo "Installed launchd agent: $PLIST_PATH (interval=${interval_sec}s)"
}

uninstall_agent() {
  launchctl bootout "gui/$UID_VALUE/$LABEL" 2>/dev/null || true
  rm -f "$PLIST_PATH"
  echo "Uninstalled launchd agent: $PLIST_PATH"
}

status_agent() {
  launchctl print "gui/$UID_VALUE/$LABEL" 2>/dev/null || echo "Agent not loaded"
}

case "${1:-}" in
  install) install_agent "${2:-$DEFAULT_INTERVAL_SEC}" ;;
  uninstall) uninstall_agent ;;
  status) status_agent ;;
  *)
    echo "Usage: scripts/install_backend_launchd.sh <install [interval_sec]|uninstall|status>"
    exit 1
    ;;
esac
