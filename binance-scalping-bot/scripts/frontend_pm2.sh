#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$ROOT_DIR/frontend"

mkdir -p "$FRONTEND_DIR/.runtime/pm2"

NODE_BIN="${PM2_NODE_BIN:-}"
if [[ -z "$NODE_BIN" ]]; then
  for candidate in \
    "$HOME/.nvm/versions/node/v20.19.0/bin/node" \
    "$HOME/.nvm/versions/node/v20.19.2/bin/node" \
    "$HOME/.nvm/versions/node/v22.12.0/bin/node" \
    "$(command -v node || true)"
  do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
      NODE_BIN="$candidate"
      break
    fi
  done
fi

if [[ -z "$NODE_BIN" || ! -x "$NODE_BIN" ]]; then
  echo "No usable node binary found for frontend PM2 process."
  exit 1
fi

NODE_DIR="$(dirname "$NODE_BIN")"
export PATH="$NODE_DIR:$PATH"

FRONTEND_PORT="${FRONTEND_PORT:-5199}"
FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"

cd "$FRONTEND_DIR"
npm run build
exec "$NODE_BIN" node_modules/vite/bin/vite.js preview --host "$FRONTEND_HOST" --port "$FRONTEND_PORT" --strictPort
