#!/usr/bin/env bash
set -euo pipefail

cd /home/thangnguyen/project/ml-candles/binance-scalping-bot/frontend
export PATH="/home/thangnguyen/.nvm/versions/node/v20.19.0/bin:$PATH"
exec /home/thangnguyen/.nvm/versions/node/v20.19.0/bin/node \
  /home/thangnguyen/project/ml-candles/binance-scalping-bot/frontend/node_modules/vite/bin/vite.js \
  --host 0.0.0.0 \
  --port 5199
