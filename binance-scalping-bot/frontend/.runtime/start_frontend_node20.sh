#!/usr/bin/env bash
set -euo pipefail

cd /home/thangnguyen/project/ml-candles/binance-scalping-bot/frontend
export PATH="/home/thangnguyen/.nvm/versions/node/v20.19.0/bin:$PATH"
exec /home/thangnguyen/.nvm/versions/node/v20.19.0/bin/npm run dev -- --host 127.0.0.1 --port 5199