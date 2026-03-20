#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BASE_ENV="$ROOT_DIR/backend/.env"
MAC_ENV="$ROOT_DIR/backend/.mac.env"
WINDOW_ENV="$ROOT_DIR/backend/.window.env"
WINDOW_NEV="$ROOT_DIR/backend/.window.nev"

if [[ ! -f "$BASE_ENV" ]]; then
  echo "Missing base env: $BASE_ENV"
  exit 1
fi

cp "$BASE_ENV" "$MAC_ENV"
cp "$BASE_ENV" "$WINDOW_ENV"
cp "$BASE_ENV" "$WINDOW_NEV"

# macOS paths
perl -0pi -e \
  's|^SQLITE_DB_PATH=.*$|SQLITE_DB_PATH=/Users/thang/Desktop/TEST/binance-scalping-bot/backend/backend_data/trading_bot.db|m;
   s|^LIQUID_ML_MODEL_PATH=.*$|LIQUID_ML_MODEL_PATH=/Users/thang/Desktop/TEST/binance-scalping-bot/backend/backend_data/liquid_rf_model.joblib|m;
   s|^ML_TEST_MODEL_PATH=.*$|ML_TEST_MODEL_PATH=/Users/thang/Desktop/TEST/binance-scalping-bot/backend/backend_data/rf_model_test.joblib|m;
   s|^ML_MODEL_PATH=.*$|ML_MODEL_PATH=/Users/thang/Desktop/TEST/binance-scalping-bot/backend/backend_data/rf_model.joblib|m;
   s|^#\s*MYSQL_USER=.*\r?\n||mg;
   s|^MYSQL_USER=.*$|MYSQL_USER=root|m;
   s|^(MYSQL_USER=root)$|$1\n# MYSQL_USER=navicat|m;' \
  "$MAC_ENV"

# Windows/Linux box paths (the machine currently using /home/thangnguyen/...)
perl -0pi -e \
  's|^SQLITE_DB_PATH=.*$|SQLITE_DB_PATH=/home/thangnguyen/project/test-b/binance-scalping-bot/backend/backend_data/trading_bot.db|m;
   s|^LIQUID_ML_MODEL_PATH=.*$|LIQUID_ML_MODEL_PATH=/home/thangnguyen/project/test-b/binance-scalping-bot/backend/backend_data/liquid_rf_model.joblib|m;
   s|^ML_TEST_MODEL_PATH=.*$|ML_TEST_MODEL_PATH=/home/thangnguyen/project/test-b/binance-scalping-bot/backend/backend_data/rf_model_test.joblib|m;
   s|^ML_MODEL_PATH=.*$|ML_MODEL_PATH=/home/thangnguyen/project/test-b/binance-scalping-bot/backend/backend_data/rf_model.joblib|m;
   s|^#\s*MYSQL_USER=.*\r?\n||mg;
   s|^MYSQL_USER=.*$|MYSQL_USER=navicat|m;
   s|^(MYSQL_USER=navicat)$|$1\n# MYSQL_USER=root|m;' \
  "$WINDOW_ENV" \
  "$WINDOW_NEV"

echo "Updated:"
echo "  - $MAC_ENV"
echo "  - $WINDOW_ENV"
echo "  - $WINDOW_NEV"
