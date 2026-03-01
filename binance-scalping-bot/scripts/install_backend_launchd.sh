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

install_agent() {
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
  <integer>21600</integer>
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
  echo "Installed launchd agent: $PLIST_PATH"
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
  install) install_agent ;;
  uninstall) uninstall_agent ;;
  status) status_agent ;;
  *)
    echo "Usage: scripts/install_backend_launchd.sh <install|uninstall|status>"
    exit 1
    ;;
esac

