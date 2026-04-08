# Binance Scalping Bot (Backend + Frontend)

## 1) YÃƒÆ’Ã‚Âªu cÃƒÂ¡Ã‚ÂºÃ‚Â§u mÃƒÆ’Ã‚Â´i trÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Âng

- Python 3.11+ (Ãƒâ€žÃ¢â‚¬Ëœang dÃƒÆ’Ã‚Â¹ng venv tÃƒÂ¡Ã‚ÂºÃ‚Â¡i `.venv`)
- Node.js 18+
- npm
- Internet Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ train model (fetch dÃƒÂ¡Ã‚Â»Ã‚Â¯ liÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡u Binance qua `ccxt`)

## 2) CÃƒÆ’Ã‚Â i dependencies

### Backend

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot/backend
cp .mac.env .env
/Users/thang/Desktop/TEST/binance-scalping-bot/.venv/bin/pip install -r requirements.txt
```

### Frontend

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot/frontend
npm install
```

## 3) ChÃƒÂ¡Ã‚ÂºÃ‚Â¡y project

### Start nhanh Backend (terminal 1)

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot
./scripts/backend_service.sh start
```

- Script se tu doc `PORT` tu `backend/.env`.
- Vi du: neu `backend/.env` dang de `PORT=8005` thi backend se len o `http://127.0.0.1:8005`.
### Start nhanh Frontend (terminal 2)

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot/frontend
npm run dev -- --host 127.0.0.1 --port 5199
```

- Frontend: `http://127.0.0.1:5199`
- Backend API docs: dung cung port trong `backend/.env` (vi du `http://127.0.0.1:8005/docs` neu `PORT=8005`)

### Ghi chÃƒÆ’Ã‚Âº env

- macOS dev: dÃƒÆ’Ã‚Â¹ng `backend/.mac.env` lÃƒÆ’Ã‚Â m nguÃƒÂ¡Ã‚Â»Ã¢â‚¬Å“n chÃƒÆ’Ã‚Â­nh, copy sang `backend/.env`
- Khi thÃƒÆ’Ã‚Âªm env mÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºi, nhÃƒÂ¡Ã‚Â»Ã¢â‚¬Âº thÃƒÆ’Ã‚Âªm Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Å“ng thÃƒÂ¡Ã‚Â»Ã‚Âi vÃƒÆ’Ã‚Â o:
  - `backend/.mac.env`
  - `backend/.window.env`
  - `backend/.window.nev`

### LÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh restart backend

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot
./scripts/backend_service.sh restart-force
```

### PM2 + log rotate (WSL/Linux)

File config PM2 cua repo nay:

```bash
/home/thangnguyen/project/ml-candles/binance-scalping-bot/ecosystem.pm2.config.cjs
```

Ten process da kem port de tranh trung voi app khac:

- `ml-candles-backend-8005`
- `ml-candles-frontend-5199`

Lenh setup PM2 + log rotate:

```bash
cd /home/thangnguyen/project/ml-candles/binance-scalping-bot
chmod +x scripts/backend_pm2.sh scripts/frontend_pm2.sh
pm2 install pm2-logrotate
pm2 set pm2-logrotate:max_size 50M
pm2 set pm2-logrotate:retain 10
pm2 set pm2-logrotate:compress true
pm2 set pm2-logrotate:dateFormat YYYY-MM-DD_HH-mm-ss
pm2 set pm2-logrotate:workerInterval 30
pm2 set pm2-logrotate:rotateInterval @daily
```

Start/restart app bang PM2:

```bash
cd /home/thangnguyen/project/ml-candles/binance-scalping-bot
./scripts/backend_service.sh stop-force || true
if lsof -tiTCP:5199 -sTCP:LISTEN -Pn >/dev/null 2>&1; then lsof -tiTCP:5199 -sTCP:LISTEN -Pn | xargs kill -9; fi
pm2 start ecosystem.pm2.config.cjs
pm2 restart ml-candles-backend-8005
pm2 restart ml-candles-frontend-5199
pm2 save
```

Lenh quan ly thuong dung:

```bash
pm2 ls
pm2 logs ml-candles-backend-8005 --lines 200
pm2 logs ml-candles-frontend-5199 --lines 200
pm2 status ml-candles-backend-8005
pm2 status ml-candles-frontend-5199
pm2 stop ml-candles-backend-8005
pm2 stop ml-candles-frontend-5199
pm2 delete ml-candles-backend-8005
pm2 delete ml-candles-frontend-5199
```

- Backend log PM2: `backend/.runtime/pm2/ml-candles-backend-8005.out.log` va `backend/.runtime/pm2/ml-candles-backend-8005.err.log`
- Frontend log PM2: `frontend/.runtime/pm2/ml-candles-frontend-5199.out.log` va `frontend/.runtime/pm2/ml-candles-frontend-5199.err.log`
- `frontend_pm2.sh` tu build frontend roi moi chay `vite preview` bang Node `20.19.0`, nen khong bi dung Node `18` mac dinh.
- PM2 backend dang bind `0.0.0.0:8005`, nen khi mo frontend bang IP WSL nhu `http://172.27.x.x:5199` thi frontend van goi duoc API `:8005`.

## 4) Train model ML (RandomForest)

### CÃƒÆ’Ã‚Â¡ch 1: curl

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ml/train \
  -H 'Content-Type: application/json' \
  -d '{"limit": 800, "horizon": 4, "rr_ratio": 1.5}'
```

### CÃƒÆ’Ã‚Â¡ch 2: Swagger UI

- MÃƒÂ¡Ã‚Â»Ã…Â¸ `http://127.0.0.1:8000/docs`
- ChÃƒÂ¡Ã‚Â»Ã‚Ân endpoint `POST /api/v1/ml/train`
- BÃƒÂ¡Ã‚ÂºÃ‚Â¥m `Try it out` -> `Execute`

### Train model riÃƒÆ’Ã‚Âªng cho liquid + EMA99 (Top Volatility)

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ml/liquid/train \
  -H 'Content-Type: application/json' \
  -d '{"limit": 900, "horizon": 16, "rr_ratio": 1.5, "top_vol_days": 1, "max_symbols": 30}'
```

## 5) KiÃƒÂ¡Ã‚Â»Ã†â€™m tra trÃƒÂ¡Ã‚ÂºÃ‚Â¡ng thÃƒÆ’Ã‚Â¡i model

```bash
curl http://127.0.0.1:8000/api/v1/ml/status
```

```bash
curl http://127.0.0.1:8000/api/v1/ml/liquid/status
```

KÃƒÂ¡Ã‚Â»Ã‚Â³ vÃƒÂ¡Ã‚Â»Ã‚Âng:
- `is_loaded: true` sau khi train thÃƒÆ’Ã‚Â nh cÃƒÆ’Ã‚Â´ng
- `accuracy`, `roc_auc`, `trained_at` cÃƒÆ’Ã‚Â³ giÃƒÆ’Ã‚Â¡ trÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹

## 6) Command test nhanh API

### Health

```bash
curl http://127.0.0.1:8000/health
```

### LÃƒÂ¡Ã‚ÂºÃ‚Â¥y signal mÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºi nhÃƒÂ¡Ã‚ÂºÃ‚Â¥t

```bash
curl "http://127.0.0.1:8000/api/v1/signals/latest?symbol=BTC/USDT&mark_price=62000"
```

### LÃƒÂ¡Ã‚ÂºÃ‚Â¥y full symbol Binance Futures (USDT perpetual)

```bash
curl http://127.0.0.1:8000/api/v1/market/symbols
```

### LÃƒÂ¡Ã‚ÂºÃ‚Â¥y giÃƒÆ’Ã‚Â¡ thÃƒÂ¡Ã‚ÂºÃ‚Â­t theo symbol

```bash
curl "http://127.0.0.1:8000/api/v1/market/price?symbol=RAVE/USDT:USDT"
```

### Paper Trading (MySQL)

```bash
curl http://127.0.0.1:8000/api/v1/paper-trades/stats
curl http://127.0.0.1:8000/api/v1/paper-trades/open
curl "http://127.0.0.1:8000/api/v1/paper-trades/history?limit=100"
```

- `history` hiÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡n cÃƒÆ’Ã‚Â³ thÃƒÆ’Ã‚Âªm `close_reason` Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ biÃƒÂ¡Ã‚ÂºÃ‚Â¿t lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â³ng do `SL`, `TP`, `TIMEOUT_*` hay `MANUAL_*`.

### Analytics tables

```bash
curl "http://127.0.0.1:8000/api/v1/analytics/top-volatility?days=1&limit=30"
curl "http://127.0.0.1:8000/api/v1/analytics/top-volatility?days=3&limit=30"
curl "http://127.0.0.1:8000/api/v1/analytics/top-volatility?days=5&limit=30"
curl "http://127.0.0.1:8000/api/v1/analytics/top-volatility?days=7&limit=30"
curl "http://127.0.0.1:8000/api/v1/analytics/liquidation-overview?limit=30"
```

### TÃƒÂ¡Ã‚ÂºÃ‚Â¡o lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh pending demo

```bash
curl -X POST http://127.0.0.1:8000/api/v1/orders/pending \
  -H 'Content-Type: application/json' \
  -d '{
    "symbol": "BTC/USDT",
    "side": "LONG",
    "quantity": 0.01,
    "leverage": 5,
    "predicted_entry_price": 61800,
    "stop_loss": 61500,
    "take_profit": 62400,
    "win_probability": 0.67
  }'
```

### Xem danh sÃƒÆ’Ã‚Â¡ch lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh

```bash
curl http://127.0.0.1:8000/api/v1/orders/pending
curl http://127.0.0.1:8000/api/v1/orders/open
curl http://127.0.0.1:8000/api/v1/orders/closed
```

## 7) Build frontend production

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot/frontend
npm run build
npm run preview
```

## 8) BiÃƒÂ¡Ã‚ÂºÃ‚Â¿n mÃƒÆ’Ã‚Â´i trÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Âng chÃƒÆ’Ã‚Â­nh (`backend/.env`)

```env
APP_NAME=Binance Scalping Bot API
APP_ENV=development
HOST=127.0.0.1
PORT=8000
ALLOWED_ORIGINS=http://localhost:5173
SQLITE_DB_PATH=/Users/thang/Desktop/TEST/binance-scalping-bot/backend/backend_data/trading_bot.db
ML_MODEL_PATH=/Users/thang/Desktop/TEST/binance-scalping-bot/backend/backend_data/rf_model.joblib
LIQUID_ML_MODEL_PATH=/Users/thang/Desktop/TEST/binance-scalping-bot/backend/backend_data/liquid_rf_model.joblib
TRAINING_SYMBOLS=SOL/USDT,XRP/USDT,ADA/USDT,DOGE/USDT
ML_FEEDBACK_TRAIN_LIMIT=1200
ML_FEEDBACK_MAE_PENALTY_PCT=20
ML_FEEDBACK_FLIP_WIN_ON_DEEP_MAE=true
ML_FEEDBACK_RECOVERY_PENALTY_ENABLED=true
ML_FEEDBACK_RECOVERY_PENALTY_MAE_PCT=10
ML_FEEDBACK_RECOVERY_PENALTY_MAX_PNL_PCT=2
ML_FEEDBACK_RECOVERY_PENALTY_WEIGHT_FACTOR=0.35
ML_FEEDBACK_GOOD_SIGNAL_BOOST_ENABLED=true
ML_FEEDBACK_GOOD_SIGNAL_MIN_PNL_PCT=8
ML_FEEDBACK_GOOD_SIGNAL_MAX_MAE_PCT=4
ML_FEEDBACK_GOOD_SIGNAL_WEIGHT_MULTIPLIER=1.4
AUTO_TRAIN_ENABLED=true
AUTO_TRAIN_INTERVAL_MINUTES=240
AUTO_TRAIN_STARTUP_DELAY_SEC=30
AUTO_TRAIN_LIMIT=800
AUTO_TRAIN_HORIZON=4
AUTO_TRAIN_RR_RATIO=1.5
ML_USE_LIQUIDATION_FEATURES=true
LIQUID_ML_ENABLED=true
LIQUID_ML_MIN_WIN=0.68
LIQUID_ML_TOP_VOL_DAYS=1
LIQUID_ML_MAX_SYMBOLS=30
LIQUID_ML_TOUCH_TOLERANCE_PCT=0.004
LIQUID_ML_TRAIN_LIMIT=900
LIQUID_ML_TRAIN_HORIZON=16
LIQUID_ML_TRAIN_RR_RATIO=1.5
WS_PING_INTERVAL_SEC=1.0
MYSQL_ENABLED=false
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=
MYSQL_DATABASE=trading_bot
PAPER_TRADE_MIN_WIN=0.75
PAPER_TRADE_QUANTITY=0.01
PAPER_TRADE_ORDER_USDT=10
PAPER_TRADE_MARGIN_USDT=0
PAPER_TRADE_MAINT_MARGIN_RATE=0.02
PAPER_TRADE_LEVERAGE=5
PAPER_TRADE_MAJOR_SYMBOLS=BTC/USDT,ETH/USDT,BNB/USDT,SOL/USDT
PAPER_TRADE_MAJOR_DYNAMIC_ENABLED=true
PAPER_TRADE_MAJOR_DYNAMIC_REFRESH_SEC=180
PAPER_TRADE_MAJOR_DYNAMIC_LIMIT=8
PAPER_TRADE_MAJOR_DYNAMIC_CANDIDATES=30
PAPER_TRADE_MAJOR_DYNAMIC_CANDLE_LOOKBACK=24
PAPER_TRADE_MAJOR_LEVERAGE=10
PAPER_TRADE_MAJOR_MAX_RISK_PCT=20
PAPER_TRADE_POLL_INTERVAL_SEC=6
PAPER_TRADE_ENTRY_REQUIRE_FRESH_STREAM_PRICE=true
PAPER_TRADE_MIN_SL_PCT=0.008
PAPER_TRADE_MIN_SL_LOSS_PCT=5
PAPER_TRADE_SL_EXTRA_BUFFER_PCT=0.002
PAPER_TRADE_SL_ATR_MULTIPLIER=1.2
PAPER_TRADE_SL_ATR_TIMEFRAME=5m
PAPER_TRADE_SL_ATR_LIMIT=120
PAPER_TRADE_MAX_TP_PCT=15
PAPER_TRADE_MIN_RR=1.5
PAPER_TRADE_MAX_RISK_PCT=12
PAPER_TRADE_MAX_HOLD_MINUTES=120
PAPER_TRADE_DISABLE_SL=false
PAPER_TRADE_MOVE_SL_TO_ENTRY_PNL_PCT=5
PAPER_TRADE_MOVE_SL_LOCK_PNL_PCT=10
PAPER_TRADE_MOVE_SL_SCALE_BY_LEVERAGE=true
PAPER_TRADE_MOVE_SL_REFERENCE_LEVERAGE=5
PAPER_TRADE_INSTANT_SL_GUARD_ENABLED=true
PAPER_TRADE_INSTANT_SL_GUARD_MAX_HOLD_MINUTES=25
PAPER_TRADE_INSTANT_SL_GUARD_MIN_ABS_PNL_PCT=10
PAPER_TRADE_INSTANT_SL_GUARD_MIN_ABS_MAE_PCT=8
PAPER_TRADE_INSTANT_SL_GUARD_COOLDOWN_MINUTES=90
PAPER_TRADE_INSTANT_SL_GLOBAL_GUARD_ENABLED=true
PAPER_TRADE_INSTANT_SL_GLOBAL_THRESHOLD=3
PAPER_TRADE_INSTANT_SL_GLOBAL_WINDOW_MINUTES=20
PAPER_TRADE_INSTANT_SL_GLOBAL_COOLDOWN_MINUTES=60
PAPER_TRADE_ENTRY_HARD_BLOCK_HOURS_VN=20
PAPER_TRADE_BTC_FILTER_ENABLED=true
PAPER_TRADE_BTC_FILTER_TIMEFRAME=15m
PAPER_TRADE_BTC_FILTER_CACHE_SEC=20
PAPER_TRADE_BTC_FILTER_MIN_CONFIDENCE=0.55
PAPER_TRADE_BTC_FILTER_BLOCK_COUNTERTREND=true
PAPER_TRADE_BTC_FILTER_COUNTERTREND_MIN_WIN=0.77
PAPER_TRADE_BTC_TREND_HOUR_LOCK_ENABLED=true
PAPER_TRADE_BTC_TREND_HOUR_LOCK_MIN_CONFIDENCE=0.60
PAPER_TRADE_BTC_TREND_HOUR_LOCK_COUNTERTREND_HOURS=2
PAPER_TRADE_BTC_TREND_HOUR_LOCK_APPLY_NON_BTC_FOLLOW=true
PAPER_TRADE_BTC_SHOCK_PAUSE_ENABLED=true
PAPER_TRADE_BTC_SHOCK_THRESHOLD_PCT=1.2
PAPER_TRADE_BTC_SHOCK_COOLDOWN_MINUTES=30
PAPER_TRADE_BTC_SHOCK_UP_LONG_BLOCK_MINUTES=60
PAPER_TRADE_BTC_SHOCK_DOWN_SHORT_BLOCK_MINUTES=60
PAPER_TRADE_BTC_SHOCK_UP_REQUIRE_PULLBACK=true
PAPER_TRADE_BTC_SHOCK_PULLBACK_EMA_PERIOD=21
PAPER_TRADE_BTC_SHOCK_PULLBACK_TOLERANCE_PCT=0.0015
PAPER_TRADE_BTC_REVERSAL_PROFIT_EXIT_ENABLED=true
PAPER_TRADE_BTC_REVERSAL_THRESHOLD_PCT=0.8
PAPER_TRADE_BTC_REVERSAL_MIN_CONFIDENCE=0.55
PAPER_TRADE_BTC_PROFIT_LOCK_ENABLED=true
PAPER_TRADE_BTC_PROFIT_LOCK_MIN_CONFIDENCE=0.60
PAPER_TRADE_BTC_FOLLOW_MIN_CORR=0.45
PAPER_TRADE_BTC_FOLLOW_MIN_BETA=0.20
PAPER_TRADE_BTC_FOLLOW_LOOKBACK=120
PAPER_TRADE_BTC_FOLLOW_CACHE_SEC=300
```

### DÃƒÆ’Ã‚Â¹ng 2 file env riÃƒÆ’Ã‚Âªng cho macOS vÃƒÆ’Ã‚Â  Windows/Linux

- File Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ tÃƒÆ’Ã‚Â¡ch sÃƒÂ¡Ã‚ÂºÃ‚Âµn:
  - `backend/.mac.env`
  - `backend/.window.env`
- Script Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Å“ng bÃƒÂ¡Ã‚Â»Ã¢â€žÂ¢ tÃƒÂ¡Ã‚Â»Ã‚Â« `backend/.env`:
  - `scripts/sync_env_variants.sh`

Sau mÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i lÃƒÂ¡Ã‚ÂºÃ‚Â§n sÃƒÂ¡Ã‚Â»Ã‚Â­a `backend/.env`, chÃƒÂ¡Ã‚ÂºÃ‚Â¡y:

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot
./scripts/sync_env_variants.sh
```

Linux/WSL (source ÃƒÂ¡Ã‚Â»Ã…Â¸ `/home/thangnguyen/...`):

```bash
cd /home/thangnguyen/project/test-b/binance-scalping-bot
./scripts/sync_env_variants.sh
```

Ãƒâ€žÃ‚ÂÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¢i nhanh mÃƒÆ’Ã‚Â´i trÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Âng Ãƒâ€žÃ¢â‚¬Ëœang chÃƒÂ¡Ã‚ÂºÃ‚Â¡y:

```bash
# DÃƒÆ’Ã‚Â¹ng cÃƒÂ¡Ã‚ÂºÃ‚Â¥u hÃƒÆ’Ã‚Â¬nh macOS
cp /Users/thang/Desktop/TEST/binance-scalping-bot/backend/.mac.env /Users/thang/Desktop/TEST/binance-scalping-bot/backend/.env

# DÃƒÆ’Ã‚Â¹ng cÃƒÂ¡Ã‚ÂºÃ‚Â¥u hÃƒÆ’Ã‚Â¬nh Windows/Linux
cp /Users/thang/Desktop/TEST/binance-scalping-bot/backend/.window.env /Users/thang/Desktop/TEST/binance-scalping-bot/backend/.env
```

Linux/WSL:

```bash
# DÃƒÆ’Ã‚Â¹ng cÃƒÂ¡Ã‚ÂºÃ‚Â¥u hÃƒÆ’Ã‚Â¬nh macOS
cp /home/thangnguyen/project/test-b/binance-scalping-bot/backend/.mac.env /home/thangnguyen/project/test-b/binance-scalping-bot/backend/.env

# DÃƒÆ’Ã‚Â¹ng cÃƒÂ¡Ã‚ÂºÃ‚Â¥u hÃƒÆ’Ã‚Â¬nh Windows/Linux
cp /home/thangnguyen/project/test-b/binance-scalping-bot/backend/.window.env /home/thangnguyen/project/test-b/binance-scalping-bot/backend/.env
```

Sau khi copy env, restart backend:

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot
./scripts/backend_service.sh restart-force
```

Linux/WSL:

```bash
cd /home/thangnguyen/project/test-b/binance-scalping-bot
./scripts/backend_service.sh restart-force
```

Quy Ãƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºc trÃƒÆ’Ã‚Â¡nh conflict khi Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¢i mÃƒÆ’Ã‚Â¡y:
- KhÃƒÆ’Ã‚Â´ng xÃƒÆ’Ã‚Â³a dÃƒÆ’Ã‚Â²ng `MYSQL_USER` cÃƒÂ¡Ã‚Â»Ã‚Â§a mÃƒÆ’Ã‚Â¡y cÃƒÆ’Ã‚Â²n lÃƒÂ¡Ã‚ÂºÃ‚Â¡i.
- LuÃƒÆ’Ã‚Â´n giÃƒÂ¡Ã‚Â»Ã‚Â¯ 2 dÃƒÆ’Ã‚Â²ng vÃƒÆ’Ã‚Â  chÃƒÂ¡Ã‚Â»Ã¢â‚¬Â° comment/uncomment:
  - macOS:
    - `MYSQL_USER=root`
    - `# MYSQL_USER=navicat`
  - Windows/Linux:
    - `MYSQL_USER=navicat`
    - `# MYSQL_USER=root`

- `PAPER_TRADE_ORDER_USDT` lÃƒÆ’Ã‚Â  giÃƒÆ’Ã‚Â¡ trÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹ lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh theo USDT (notional, chÃƒâ€ Ã‚Â°a tÃƒÆ’Ã‚Â­nh margin).
- NÃƒÂ¡Ã‚ÂºÃ‚Â¿u khÃƒÆ’Ã‚Â´ng truyÃƒÂ¡Ã‚Â»Ã‚Ân `quantity` khi mÃƒÂ¡Ã‚Â»Ã…Â¸ lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh, backend sÃƒÂ¡Ã‚ÂºÃ‚Â½ tÃƒÂ¡Ã‚Â»Ã‚Â± tÃƒÆ’Ã‚Â­nh `quantity = PAPER_TRADE_ORDER_USDT / entry_price`.
- `PAPER_TRADE_MARGIN_USDT` lÃƒÆ’Ã‚Â  margin dÃƒÆ’Ã‚Â¹ng Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ tÃƒÆ’Ã‚Â­nh PnL% (ROI margin).  
: Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚ÂºÃ‚Â·t `0` Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ tÃƒÂ¡Ã‚Â»Ã‚Â± tÃƒÆ’Ã‚Â­nh theo cÃƒÆ’Ã‚Â´ng thÃƒÂ¡Ã‚Â»Ã‚Â©c `entry_price * quantity / leverage`.
- `PAPER_TRADE_MAINT_MARGIN_RATE` dÃƒÆ’Ã‚Â¹ng Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ Ãƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºc tÃƒÆ’Ã‚Â­nh `Signal Margin Ratio%` (kiÃƒÂ¡Ã‚Â»Ã†â€™u Binance `TÃƒÂ¡Ã‚Â»Ã¢â‚¬Â° lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡ kÃƒÆ’Ã‚Â½ quÃƒÂ¡Ã‚Â»Ã‚Â¹`) vÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºi cÃƒÆ’Ã‚Â´ng thÃƒÂ¡Ã‚Â»Ã‚Â©c xÃƒÂ¡Ã‚ÂºÃ‚Â¥p xÃƒÂ¡Ã‚Â»Ã¢â‚¬Â°:  
: `margin_ratio_pct ~= leverage * maint_margin_rate * 100`.
- `PAPER_TRADE_QUANTITY` chÃƒÂ¡Ã‚Â»Ã¢â‚¬Â° dÃƒÆ’Ã‚Â¹ng fallback khi khÃƒÆ’Ã‚Â´ng tÃƒÆ’Ã‚Â­nh Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c tÃƒÂ¡Ã‚Â»Ã‚Â« giÃƒÆ’Ã‚Â¡.
- `PAPER_TRADE_ENTRY_REQUIRE_FRESH_STREAM_PRICE=true` chÃƒÂ¡Ã‚Â»Ã¢â‚¬Â° cho mÃƒÂ¡Ã‚Â»Ã…Â¸ lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh mÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºi khi cÃƒÆ’Ã‚Â³ giÃƒÆ’Ã‚Â¡ WS cÃƒÆ’Ã‚Â²n fresh.  
: bÃƒÂ¡Ã‚ÂºÃ‚Â­t Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ trÃƒÆ’Ã‚Â¡nh mÃƒÂ¡Ã‚Â»Ã…Â¸ lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh bÃƒÂ¡Ã‚ÂºÃ‚Â±ng giÃƒÆ’Ã‚Â¡ REST fallback khi lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡ch sÃƒÆ’Ã‚Â n.
- `PAPER_TRADE_MIN_SL_PCT` + `PAPER_TRADE_SL_EXTRA_BUFFER_PCT` giÃƒÆ’Ã‚Âºp kÃƒÆ’Ã‚Â©o SL xa hÃƒâ€ Ã‚Â¡n Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ trÃƒÆ’Ã‚Â¡nh bÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹ quÃƒÆ’Ã‚Â©t quÃƒÆ’Ã‚Â¡ sÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºm.
- `PAPER_TRADE_SL_ATR_MULTIPLIER` dÃƒÆ’Ã‚Â¹ng ATR Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚ÂºÃ‚Â·t ngÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â¡ng SL tÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœi thiÃƒÂ¡Ã‚Â»Ã†â€™u theo biÃƒÂ¡Ã‚ÂºÃ‚Â¿n Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â€žÂ¢ng (0 = tÃƒÂ¡Ã‚ÂºÃ‚Â¯t ATR).
- `PAPER_TRADE_MAX_TP_PCT` giÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºi hÃƒÂ¡Ã‚ÂºÃ‚Â¡n TP tÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœi Ãƒâ€žÃ¢â‚¬Ëœa theo `% giÃƒÆ’Ã‚Â¡ vÃƒÆ’Ã‚Â o` (mÃƒÂ¡Ã‚ÂºÃ‚Â·c Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹nh 15%).  
: vÃƒÆ’Ã‚Â­ dÃƒÂ¡Ã‚Â»Ã‚Â¥ `15` nghÃƒâ€žÃ‚Â©a lÃƒÆ’Ã‚Â  TP khÃƒÆ’Ã‚Â´ng vÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£t quÃƒÆ’Ã‚Â¡ `entry +/- 15%`.
- `PAPER_TRADE_MOVE_SL_TO_ENTRY_PNL_PCT` lÃƒÆ’Ã‚Â  ngÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â¡ng kÃƒÆ’Ã‚Â­ch hoÃƒÂ¡Ã‚ÂºÃ‚Â¡t dÃƒÂ¡Ã‚Â»Ã‚Âi SL theo `%PnL margin` tÃƒÂ¡Ã‚ÂºÃ‚Â¡i `PAPER_TRADE_MOVE_SL_REFERENCE_LEVERAGE` (vÃƒÆ’Ã‚Â­ dÃƒÂ¡Ã‚Â»Ã‚Â¥ 5% tÃƒÂ¡Ã‚ÂºÃ‚Â¡i 5x).
- `PAPER_TRADE_MOVE_SL_LOCK_PNL_PCT` lÃƒÆ’Ã‚Â  mÃƒÂ¡Ã‚Â»Ã‚Â©c lÃƒÂ¡Ã‚Â»Ã‚Â£i nhuÃƒÂ¡Ã‚ÂºÃ‚Â­n giÃƒÂ¡Ã‚Â»Ã‚Â¯ lÃƒÂ¡Ã‚ÂºÃ‚Â¡i sau khi kÃƒÆ’Ã‚Â­ch hoÃƒÂ¡Ã‚ÂºÃ‚Â¡t (vÃƒÆ’Ã‚Â­ dÃƒÂ¡Ã‚Â»Ã‚Â¥ 10% ÃƒÂ¡Ã‚Â»Ã…Â¸ 5x ~ dÃƒÂ¡Ã‚Â»Ã‚Âi SL vÃƒÂ¡Ã‚Â»Ã‚Â mÃƒÂ¡Ã‚Â»Ã‚Â©c +2% giÃƒÆ’Ã‚Â¡ theo hÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºng cÃƒÆ’Ã‚Â³ lÃƒÂ¡Ã‚Â»Ã‚Â£i).
- `PAPER_TRADE_MOVE_SL_SCALE_BY_LEVERAGE=true` sÃƒÂ¡Ã‚ÂºÃ‚Â½ tÃƒÂ¡Ã‚Â»Ã‚Â± scale ngÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â¡ng theo leverage thÃƒÂ¡Ã‚Â»Ã‚Â±c tÃƒÂ¡Ã‚ÂºÃ‚Â¿ cÃƒÂ¡Ã‚Â»Ã‚Â§a lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh.  
: vÃƒÆ’Ã‚Â­ dÃƒÂ¡Ã‚Â»Ã‚Â¥ cÃƒÂ¡Ã‚ÂºÃ‚Â¥u hÃƒÆ’Ã‚Â¬nh `trigger=5`, `reference_leverage=5` thÃƒÆ’Ã‚Â¬ lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh 10x sÃƒÂ¡Ã‚ÂºÃ‚Â½ kÃƒÆ’Ã‚Â­ch hoÃƒÂ¡Ã‚ÂºÃ‚Â¡t ÃƒÂ¡Ã‚Â»Ã…Â¸ `10%` PnL margin.
- `PAPER_TRADE_INSTANT_SL_GUARD_ENABLED` khÃƒÆ’Ã‚Â³a tÃƒÆ’Ã‚Â¡i vÃƒÆ’Ã‚Â o lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh `symbol+side` nÃƒÂ¡Ã‚ÂºÃ‚Â¿u vÃƒÂ¡Ã‚Â»Ã‚Â«a bÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹ SL nhanh vÃƒÆ’Ã‚Â  sÃƒÆ’Ã‚Â¢u.
- `PAPER_TRADE_INSTANT_SL_GUARD_MAX_HOLD_MINUTES` xÃƒÆ’Ã‚Â¡c Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹nh thÃƒÂ¡Ã‚ÂºÃ‚Â¿ nÃƒÆ’Ã‚Â o lÃƒÆ’Ã‚Â  "SL nhanh".
- `PAPER_TRADE_INSTANT_SL_GUARD_MIN_ABS_PNL_PCT` / `PAPER_TRADE_INSTANT_SL_GUARD_MIN_ABS_MAE_PCT` lÃƒÆ’Ã‚Â  ngÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â¡ng phÃƒÂ¡Ã‚ÂºÃ‚Â¡t.
- `PAPER_TRADE_INSTANT_SL_GUARD_COOLDOWN_MINUTES` lÃƒÆ’Ã‚Â  thÃƒÂ¡Ã‚Â»Ã‚Âi gian khÃƒÆ’Ã‚Â³a `symbol+side` sau tÃƒÆ’Ã‚Â­n hiÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡u xÃƒÂ¡Ã‚ÂºÃ‚Â¥u.
- `PAPER_TRADE_INSTANT_SL_GLOBAL_*` lÃƒÆ’Ã‚Â  tÃƒÂ¡Ã‚ÂºÃ‚Â§ng bÃƒÂ¡Ã‚ÂºÃ‚Â£o vÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡ toÃƒÆ’Ã‚Â n cÃƒÂ¡Ã‚Â»Ã‚Â¥c: nÃƒÂ¡Ã‚ÂºÃ‚Â¿u SL nhanh/sÃƒÆ’Ã‚Â¢u dÃƒÂ¡Ã‚Â»Ã¢â‚¬Å“n dÃƒÂ¡Ã‚ÂºÃ‚Â­p thÃƒÆ’Ã‚Â¬ tÃƒÂ¡Ã‚ÂºÃ‚Â¡m dÃƒÂ¡Ã‚Â»Ã‚Â«ng mÃƒÂ¡Ã‚Â»Ã…Â¸ lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh mÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºi.
- `PAPER_TRADE_ENTRY_HARD_BLOCK_HOURS_VN` chÃƒÂ¡Ã‚ÂºÃ‚Â·n cÃƒÂ¡Ã‚Â»Ã‚Â©ng giÃƒÂ¡Ã‚Â»Ã‚Â mÃƒÂ¡Ã‚Â»Ã…Â¸ lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh theo giÃƒÂ¡Ã‚Â»Ã‚Â VN (khÃƒÆ’Ã‚Â´ng ÃƒÂ¡Ã‚ÂºÃ‚Â£nh hÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã…Â¸ng quÃƒÂ¡Ã‚ÂºÃ‚Â£n lÃƒÆ’Ã‚Â½ lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh Ãƒâ€žÃ¢â‚¬Ëœang mÃƒÂ¡Ã‚Â»Ã…Â¸).  
: hÃƒÂ¡Ã‚Â»Ã¢â‚¬â€ trÃƒÂ¡Ã‚Â»Ã‚Â£ `20`, `20,21`, `20-22`, `22-2`.
- `PAPER_TRADE_BTC_TREND_HOUR_LOCK_ENABLED` bÃƒÂ¡Ã‚ÂºÃ‚Â­t/tÃƒÂ¡Ã‚ÂºÃ‚Â¯t khÃƒÆ’Ã‚Â³a theo trend BTC trong mÃƒÂ¡Ã‚Â»Ã¢â€žÂ¢t khoÃƒÂ¡Ã‚ÂºÃ‚Â£ng giÃƒÂ¡Ã‚Â»Ã‚Â cÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœ Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹nh.
- `PAPER_TRADE_BTC_TREND_HOUR_LOCK_COUNTERTREND_HOURS` lÃƒÆ’Ã‚Â  sÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœ giÃƒÂ¡Ã‚Â»Ã‚Â khÃƒÆ’Ã‚Â³a chiÃƒÂ¡Ã‚Â»Ã‚Âu ngÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c trend.  
: vÃƒÆ’Ã‚Â­ dÃƒÂ¡Ã‚Â»Ã‚Â¥ BTC bullish thÃƒÆ’Ã‚Â¬ khÃƒÆ’Ã‚Â³a SHORT trong X giÃƒÂ¡Ã‚Â»Ã‚Â.
- `PAPER_TRADE_BTC_TREND_HOUR_LOCK_MIN_CONFIDENCE` lÃƒÆ’Ã‚Â  ngÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â¡ng confidence tÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœi thiÃƒÂ¡Ã‚Â»Ã†â€™u Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ bÃƒÂ¡Ã‚ÂºÃ‚Â¯t Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚ÂºÃ‚Â§u khÃƒÆ’Ã‚Â³a.
- `PAPER_TRADE_BTC_TREND_HOUR_LOCK_APPLY_NON_BTC_FOLLOW=true` sÃƒÂ¡Ã‚ÂºÃ‚Â½ ÃƒÆ’Ã‚Â¡p dÃƒÂ¡Ã‚Â»Ã‚Â¥ng khÃƒÆ’Ã‚Â³a cÃƒÂ¡Ã‚ÂºÃ‚Â£ coin khÃƒÆ’Ã‚Â´ng follow BTC.
- `PAPER_TRADE_BTC_SHOCK_THRESHOLD_PCT` lÃƒÆ’Ã‚Â  ngÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â¡ng sÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœc BTC theo `%` (dÃƒÂ¡Ã‚Â»Ã‚Â±a trÃƒÆ’Ã‚Âªn biÃƒÂ¡Ã‚ÂºÃ‚Â¿n Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â€žÂ¢ng close-to-close hoÃƒÂ¡Ã‚ÂºÃ‚Â·c range nÃƒÂ¡Ã‚ÂºÃ‚Â¿n).
- `PAPER_TRADE_BTC_SHOCK_COOLDOWN_MINUTES` lÃƒÆ’Ã‚Â  thÃƒÂ¡Ã‚Â»Ã‚Âi gian khÃƒÆ’Ã‚Â³a tÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœi thiÃƒÂ¡Ã‚Â»Ã†â€™u cho lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh cÃƒÆ’Ã‚Â¹ng chiÃƒÂ¡Ã‚Â»Ã‚Âu sau shock.
- `PAPER_TRADE_BTC_SHOCK_UP_LONG_BLOCK_MINUTES` khÃƒÆ’Ã‚Â³a riÃƒÆ’Ã‚Âªng lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh `LONG` sau shock tÃƒâ€žÃ†â€™ng mÃƒÂ¡Ã‚ÂºÃ‚Â¡nh cÃƒÂ¡Ã‚Â»Ã‚Â§a BTC.
- `PAPER_TRADE_BTC_SHOCK_DOWN_SHORT_BLOCK_MINUTES` khÃƒÆ’Ã‚Â³a riÃƒÆ’Ã‚Âªng lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh `SHORT` sau shock giÃƒÂ¡Ã‚ÂºÃ‚Â£m mÃƒÂ¡Ã‚ÂºÃ‚Â¡nh cÃƒÂ¡Ã‚Â»Ã‚Â§a BTC.
- `PAPER_TRADE_BTC_SHOCK_UP_REQUIRE_PULLBACK=true` chÃƒÂ¡Ã‚Â»Ã¢â‚¬Â° mÃƒÂ¡Ã‚Â»Ã…Â¸ lÃƒÂ¡Ã‚ÂºÃ‚Â¡i lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh cÃƒÆ’Ã‚Â¹ng chiÃƒÂ¡Ã‚Â»Ã‚Âu shock khi BTC pullback vÃƒÂ¡Ã‚Â»Ã‚Â EMA (UP: chÃƒÂ¡Ã‚Â»Ã‚Â hÃƒÂ¡Ã‚ÂºÃ‚Â¡ nhiÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡t Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ mÃƒÂ¡Ã‚Â»Ã…Â¸ LONG, DOWN: chÃƒÂ¡Ã‚Â»Ã‚Â hÃƒÂ¡Ã‚Â»Ã¢â‚¬Å“i lÃƒÆ’Ã‚Âªn Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ mÃƒÂ¡Ã‚Â»Ã…Â¸ SHORT).
- `PAPER_TRADE_BTC_SHOCK_PULLBACK_EMA_PERIOD` chÃƒÂ¡Ã‚Â»Ã‚Ân EMA dÃƒÆ’Ã‚Â¹ng xÃƒÆ’Ã‚Â¡c nhÃƒÂ¡Ã‚ÂºÃ‚Â­n pullback (`21` hoÃƒÂ¡Ã‚ÂºÃ‚Â·c `55`).
- `PAPER_TRADE_BTC_SHOCK_PULLBACK_TOLERANCE_PCT` lÃƒÆ’Ã‚Â  biÃƒÆ’Ã‚Âªn Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â€žÂ¢ cho phÃƒÆ’Ã‚Â©p quanh EMA Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ tÃƒÆ’Ã‚Â­nh lÃƒÆ’Ã‚Â  Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ pullback.
- `PAPER_TRADE_BTC_REVERSAL_PROFIT_EXIT_ENABLED=true` sÃƒÂ¡Ã‚ÂºÃ‚Â½ Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â³ng nhanh cÃƒÆ’Ã‚Â¡c lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh `LONG` Ãƒâ€žÃ¢â‚¬Ëœang lÃƒÆ’Ã‚Â£i khi BTC Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚ÂºÃ‚Â£o chiÃƒÂ¡Ã‚Â»Ã‚Âu giÃƒÂ¡Ã‚ÂºÃ‚Â£m mÃƒÂ¡Ã‚ÂºÃ‚Â¡nh.
- `PAPER_TRADE_BTC_REVERSAL_THRESHOLD_PCT` lÃƒÆ’Ã‚Â  ngÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â¡ng Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚ÂºÃ‚Â£o chiÃƒÂ¡Ã‚Â»Ã‚Âu mÃƒÂ¡Ã‚ÂºÃ‚Â¡nh theo `%` Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ kÃƒÆ’Ã‚Â­ch hoÃƒÂ¡Ã‚ÂºÃ‚Â¡t Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â³ng nhanh.
- `PAPER_TRADE_BTC_REVERSAL_MIN_CONFIDENCE` lÃƒÆ’Ã‚Â  Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â€žÂ¢ tin cÃƒÂ¡Ã‚ÂºÃ‚Â­y trend SHORT tÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœi thiÃƒÂ¡Ã‚Â»Ã†â€™u Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â³ng nhanh (nÃƒÂ¡Ã‚ÂºÃ‚Â¿u chÃƒâ€ Ã‚Â°a Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã‚Â§ thÃƒÆ’Ã‚Â¬ vÃƒÂ¡Ã‚ÂºÃ‚Â«n cÃƒÆ’Ã‚Â³ nhÃƒÆ’Ã‚Â¡nh fallback khi shock cÃƒÂ¡Ã‚Â»Ã‚Â±c mÃƒÂ¡Ã‚ÂºÃ‚Â¡nh).
- DB lÃƒâ€ Ã‚Â°u thÃƒÆ’Ã‚Âªm `mae_pct`/`mfe_pct` cho mÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i paper trade (theo % margin), dÃƒÆ’Ã‚Â¹ng Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â¡nh giÃƒÆ’Ã‚Â¡ quality tÃƒÆ’Ã‚Â­n hiÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡u.
- Khi train tÃƒÂ¡Ã‚Â»Ã‚Â« `ml_feedback`, nhÃƒÆ’Ã‚Â£n Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c Ãƒâ€ Ã‚Â°u tiÃƒÆ’Ã‚Âªn theo `close_reason`:
: `TP` => mÃƒÂ¡Ã‚ÂºÃ‚Â«u tÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœt (`WIN`), `SL` => mÃƒÂ¡Ã‚ÂºÃ‚Â«u xÃƒÂ¡Ã‚ÂºÃ‚Â¥u (`LOSS`).
- Khi train tÃƒÂ¡Ã‚Â»Ã‚Â« `ml_feedback`, nÃƒÂ¡Ã‚ÂºÃ‚Â¿u `ML_FEEDBACK_FLIP_WIN_ON_DEEP_MAE=true` vÃƒÆ’Ã‚Â  lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh WIN nhÃƒâ€ Ã‚Â°ng `mae_pct <= -ML_FEEDBACK_MAE_PENALTY_PCT`, sample sÃƒÂ¡Ã‚ÂºÃ‚Â½ bÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹ Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¢i nhÃƒÆ’Ã‚Â£n thÃƒÆ’Ã‚Â nh LOSS Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ phÃƒÂ¡Ã‚ÂºÃ‚Â¡t setup bÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹ ÃƒÆ’Ã‚Â¢m quÃƒÆ’Ã‚Â¡ sÃƒÆ’Ã‚Â¢u.
- VÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºi case "ÃƒÆ’Ã‚Â¢m sÃƒÆ’Ã‚Â¢u lÃƒÆ’Ã‚Â¢u, cuÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœi cÃƒÆ’Ã‚Â¹ng chÃƒÂ¡Ã‚Â»Ã¢â‚¬Â° hÃƒÂ¡Ã‚Â»Ã¢â‚¬Å“i nhÃƒÂ¡Ã‚ÂºÃ‚Â¹/breakeven", bÃƒÂ¡Ã‚ÂºÃ‚Â­t:
: `ML_FEEDBACK_RECOVERY_PENALTY_ENABLED=true`.
: Khi `mae_pct <= -ML_FEEDBACK_RECOVERY_PENALTY_MAE_PCT` vÃƒÆ’Ã‚Â  `pnl_pct <= ML_FEEDBACK_RECOVERY_PENALTY_MAX_PNL_PCT`,
: sample WIN vÃƒÂ¡Ã‚ÂºÃ‚Â«n giÃƒÂ¡Ã‚Â»Ã‚Â¯ nhÃƒÆ’Ã‚Â£n nhÃƒâ€ Ã‚Â°ng bÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹ giÃƒÂ¡Ã‚ÂºÃ‚Â£m trÃƒÂ¡Ã‚Â»Ã‚Âng sÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœ train theo `ML_FEEDBACK_RECOVERY_PENALTY_WEIGHT_FACTOR`.
- VÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºi case tÃƒÆ’Ã‚Â­n hiÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡u tÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœt (lÃƒÆ’Ã‚Â£i tÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœt vÃƒÆ’Ã‚Â  drawdown thÃƒÂ¡Ã‚ÂºÃ‚Â¥p), bÃƒÂ¡Ã‚ÂºÃ‚Â­t:
: `ML_FEEDBACK_GOOD_SIGNAL_BOOST_ENABLED=true`.
: Khi `pnl_pct >= ML_FEEDBACK_GOOD_SIGNAL_MIN_PNL_PCT` vÃƒÆ’Ã‚Â  `mae_pct >= -ML_FEEDBACK_GOOD_SIGNAL_MAX_MAE_PCT`,
: sample WIN sÃƒÂ¡Ã‚ÂºÃ‚Â½ Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c tÃƒâ€žÃ†â€™ng trÃƒÂ¡Ã‚Â»Ã‚Âng sÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœ train theo `ML_FEEDBACK_GOOD_SIGNAL_WEIGHT_MULTIPLIER`.
- CÃƒÆ’Ã‚Â³ thÃƒÂ¡Ã‚Â»Ã†â€™ cÃƒÂ¡Ã‚ÂºÃ‚Â¥u hÃƒÆ’Ã‚Â¬nh leverage riÃƒÆ’Ã‚Âªng cho coin lÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºn:
: `PAPER_TRADE_MAJOR_SYMBOLS`, `PAPER_TRADE_MAJOR_LEVERAGE`.
: VÃƒÆ’Ã‚Â­ dÃƒÂ¡Ã‚Â»Ã‚Â¥ Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚ÂºÃ‚Â·t `PAPER_TRADE_MAJOR_LEVERAGE=10` cho BTC/ETH/BNB/SOL.
: Risk gate riÃƒÆ’Ã‚Âªng dÃƒÆ’Ã‚Â¹ng `PAPER_TRADE_MAJOR_MAX_RISK_PCT` (nÃƒÆ’Ã‚Âªn >= `leverage * maint_margin_rate * 100`).
- CÃƒÆ’Ã‚Â³ thÃƒÂ¡Ã‚Â»Ã†â€™ bÃƒÂ¡Ã‚ÂºÃ‚Â­t major dynamic (khuyÃƒÂ¡Ã‚ÂºÃ‚Â¿n nghÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹) Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ khÃƒÆ’Ã‚Â´ng phÃƒÂ¡Ã‚Â»Ã‚Â¥ thuÃƒÂ¡Ã‚Â»Ã¢â€žÂ¢c list cÃƒÂ¡Ã‚Â»Ã‚Â©ng:
: `PAPER_TRADE_MAJOR_DYNAMIC_ENABLED=true`.
: Engine tÃƒÂ¡Ã‚Â»Ã‚Â± chÃƒÂ¡Ã‚Â»Ã‚Ân coin lÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºn tÃƒÂ¡Ã‚Â»Ã‚Â« **tÃƒÆ’Ã‚Â­n hiÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡u model hiÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡n tÃƒÂ¡Ã‚ÂºÃ‚Â¡i + hiÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡u quÃƒÂ¡Ã‚ÂºÃ‚Â£ DB + dÃƒÂ¡Ã‚Â»Ã‚Â¯ liÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡u nÃƒÂ¡Ã‚ÂºÃ‚Â¿n 5m**.
: Ãƒâ€žÃ‚ÂiÃƒÂ¡Ã‚Â»Ã‚Âu chÃƒÂ¡Ã‚Â»Ã¢â‚¬Â°nh bÃƒÂ¡Ã‚ÂºÃ‚Â±ng `PAPER_TRADE_MAJOR_DYNAMIC_REFRESH_SEC`, `PAPER_TRADE_MAJOR_DYNAMIC_LIMIT`,
: `PAPER_TRADE_MAJOR_DYNAMIC_CANDIDATES`, `PAPER_TRADE_MAJOR_DYNAMIC_CANDLE_LOOKBACK`.
- `PAPER_TRADE_MIN_SL_LOSS_PCT` = mÃƒÂ¡Ã‚Â»Ã‚Â©c lÃƒÂ¡Ã‚Â»Ã¢â‚¬â€ tÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœi thiÃƒÂ¡Ã‚Â»Ã†â€™u theo `% giÃƒÆ’Ã‚Â¡ trÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹ lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh (order_usdt)` khi chÃƒÂ¡Ã‚ÂºÃ‚Â¡m SL.  
: vÃƒÆ’Ã‚Â­ dÃƒÂ¡Ã‚Â»Ã‚Â¥ Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚ÂºÃ‚Â·t `5` thÃƒÆ’Ã‚Â¬ khoÃƒÂ¡Ã‚ÂºÃ‚Â£ng cÃƒÆ’Ã‚Â¡ch SL tÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœi thiÃƒÂ¡Ã‚Â»Ã†â€™u theo giÃƒÆ’Ã‚Â¡ sÃƒÂ¡Ã‚ÂºÃ‚Â½ lÃƒÆ’Ã‚Â  `5%`.
- `ML_USE_LIQUIDATION_FEATURES=true` bÃƒÂ¡Ã‚ÂºÃ‚Â­t thÃƒÆ’Ã‚Âªm nhÃƒÆ’Ã‚Â³m feature liquidation proxy (wick + volume spike trÃƒÆ’Ã‚Âªn nÃƒÂ¡Ã‚ÂºÃ‚Â¿n 5m) khi train ML.
- `LIQUID_ML_ENABLED=true` bÃƒÂ¡Ã‚ÂºÃ‚Â­t model riÃƒÆ’Ã‚Âªng cho liquid + EMA99 (15m/1h), chÃƒÂ¡Ã‚ÂºÃ‚Â¡y trÃƒÆ’Ã‚Âªn danh sÃƒÆ’Ã‚Â¡ch top volatility coin.
- Entry cÃƒÂ¡Ã‚Â»Ã‚Â§a model liquid neo theo EMA99 gÃƒÂ¡Ã‚ÂºÃ‚Â§n nhÃƒÂ¡Ã‚ÂºÃ‚Â¥t (15m hoÃƒÂ¡Ã‚ÂºÃ‚Â·c 1h), sau Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â³ vÃƒÂ¡Ã‚ÂºÃ‚Â«n Ãƒâ€žÃ¢â‚¬Ëœi qua normalize TP/SL vÃƒÆ’Ã‚Â  rule risk chung trÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºc khi mÃƒÂ¡Ã‚Â»Ã…Â¸ lÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡nh.
- Paper trade sÃƒÂ¡Ã‚ÂºÃ‚Â½ lÃƒâ€ Ã‚Â°u thÃƒÆ’Ã‚Âªm `entry_type` Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ phÃƒÆ’Ã‚Â¢n biÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡t:
: `MARKET` (mÃƒÂ¡Ã‚Â»Ã…Â¸ tay bÃƒÂ¡Ã‚ÂºÃ‚Â±ng nÃƒÆ’Ã‚Âºt Market Open) vÃƒÆ’Ã‚Â  `LIMIT` (auto khÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºp theo entry tÃƒÆ’Ã‚Â­n hiÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡u).
- MÃƒÆ’Ã‚Â n hÃƒÆ’Ã‚Â¬nh Paper Trade Stats cÃƒÆ’Ã‚Â³ thÃƒÆ’Ã‚Âªm `Market Win Rate` vÃƒÆ’Ã‚Â  `Limit Win Rate` Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ so sÃƒÆ’Ã‚Â¡nh hiÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡u quÃƒÂ¡Ã‚ÂºÃ‚Â£.
- CÃƒÆ’Ã‚Â³ bÃƒÂ¡Ã‚ÂºÃ‚Â£ng `Entry Type Breakdown` (MARKET/LIMIT): Closed, Win/Loss, Win Rate, Total/Avg PnL (USDT vÃƒÆ’Ã‚Â  %).

## 9) Auto-train Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹nh kÃƒÂ¡Ã‚Â»Ã‚Â³ (macOS + Linux/WSL)

- Auto-train chÃƒÂ¡Ã‚ÂºÃ‚Â¡y bÃƒÆ’Ã‚Âªn trong backend process nÃƒÆ’Ã‚Âªn dÃƒÆ’Ã‚Â¹ng Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c trÃƒÆ’Ã‚Âªn cÃƒÂ¡Ã‚ÂºÃ‚Â£ macOS, Linux vÃƒÆ’Ã‚Â  WSL.
- KhÃƒÆ’Ã‚Â´ng cÃƒÂ¡Ã‚ÂºÃ‚Â§n cron/launchd Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ train.
- MÃƒÂ¡Ã‚ÂºÃ‚Â·c Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹nh: `AUTO_TRAIN_ENABLED=true`, chÃƒÂ¡Ã‚ÂºÃ‚Â¡y mÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i `240` phÃƒÆ’Ã‚Âºt.

### KiÃƒÂ¡Ã‚Â»Ã†â€™m tra trÃƒÂ¡Ã‚ÂºÃ‚Â¡ng thÃƒÆ’Ã‚Â¡i auto-train

```bash
curl -s http://127.0.0.1:8000/api/v1/ml/status
```

CÃƒÆ’Ã‚Â¡c field mÃƒÂ¡Ã‚Â»Ã¢â‚¬Âºi:
- `training_in_progress`: Ãƒâ€žÃ¢â‚¬Ëœang cÃƒÆ’Ã‚Â³ lÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£t train chÃƒÂ¡Ã‚ÂºÃ‚Â¡y hay khÃƒÆ’Ã‚Â´ng.
- `auto_train_enabled`: bÃƒÂ¡Ã‚ÂºÃ‚Â­t/tÃƒÂ¡Ã‚ÂºÃ‚Â¯t auto-train.
- `auto_train_running`: scheduler cÃƒÆ’Ã‚Â³ Ãƒâ€žÃ¢â‚¬Ëœang chÃƒÂ¡Ã‚ÂºÃ‚Â¡y trong backend khÃƒÆ’Ã‚Â´ng.
- `auto_train_interval_minutes`: chu kÃƒÂ¡Ã‚Â»Ã‚Â³ train.
- `auto_train_next_run_at`: thÃƒÂ¡Ã‚Â»Ã‚Âi Ãƒâ€žÃ¢â‚¬ËœiÃƒÂ¡Ã‚Â»Ã†â€™m UTC dÃƒÂ¡Ã‚Â»Ã‚Â± kiÃƒÂ¡Ã‚ÂºÃ‚Â¿n chÃƒÂ¡Ã‚ÂºÃ‚Â¡y lÃƒÂ¡Ã‚ÂºÃ‚Â§n kÃƒÂ¡Ã‚ÂºÃ‚Â¿ tiÃƒÂ¡Ã‚ÂºÃ‚Â¿p.
- `auto_train_last_run_started_at`, `auto_train_last_run_finished_at`, `auto_train_last_result`.
- `last_train_trigger`, `last_train_started_at`, `last_train_finished_at`, `last_train_duration_sec`, `last_train_result`, `last_train_error`.
- `train_log_path`: path file log train JSONL.

### Log train

- File log:
  - `/Users/thang/Desktop/TEST/binance-scalping-bot/backend/.runtime/ml_train.log`
- MÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i lÃƒÂ¡Ã‚ÂºÃ‚Â§n train sÃƒÂ¡Ã‚ÂºÃ‚Â½ cÃƒÆ’Ã‚Â³ dÃƒÆ’Ã‚Â²ng `START` vÃƒÆ’Ã‚Â  `FINISH` (JSON), gÃƒÂ¡Ã‚Â»Ã¢â‚¬Å“m trigger (`manual`/`auto`), thÃƒÂ¡Ã‚Â»Ã‚Âi gian bÃƒÂ¡Ã‚ÂºÃ‚Â¯t Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚ÂºÃ‚Â§u/kÃƒÂ¡Ã‚ÂºÃ‚Â¿t thÃƒÆ’Ã‚Âºc, duration, result/error.
- Frontend cÃƒÆ’Ã‚Â³ card `ML Training Monitor` Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ xem trÃƒÂ¡Ã‚Â»Ã‚Â±c tiÃƒÂ¡Ã‚ÂºÃ‚Â¿p thÃƒÂ¡Ã‚Â»Ã‚Âi gian bÃƒÂ¡Ã‚ÂºÃ‚Â¯t Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚ÂºÃ‚Â§u/kÃƒÂ¡Ã‚ÂºÃ‚Â¿t thÃƒÆ’Ã‚Âºc (Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¢i sang giÃƒÂ¡Ã‚Â»Ã‚Â VN).

### BÃƒÂ¡Ã‚ÂºÃ‚Â­t/tÃƒÂ¡Ã‚ÂºÃ‚Â¯t nhanh

Trong `backend/.env`:

```env
AUTO_TRAIN_ENABLED=true
AUTO_TRAIN_INTERVAL_MINUTES=240
```

Sau khi sÃƒÂ¡Ã‚Â»Ã‚Â­a `.env`, restart backend:

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot
./scripts/backend_service.sh restart
```

## 10) Troubleshooting nhanh

- LÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i `pip: command not found`:
  - LuÃƒÆ’Ã‚Â´n dÃƒÆ’Ã‚Â¹ng pip trong venv:
  - `/Users/thang/Desktop/TEST/binance-scalping-bot/.venv/bin/pip ...`
- LÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i khÃƒÆ’Ã‚Â´ng bind Ãƒâ€žÃ¢â‚¬ËœÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Â£c cÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¢ng `8000`:
  - Ãƒâ€žÃ‚ÂÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¢i port backend, vÃƒÆ’Ã‚Â­ dÃƒÂ¡Ã‚Â»Ã‚Â¥ `--port 8001`
  - Ãƒâ€žÃ‚ÂÃƒÂ¡Ã‚Â»Ã¢â‚¬Å“ng thÃƒÂ¡Ã‚Â»Ã‚Âi sÃƒÂ¡Ã‚Â»Ã‚Â­a API base bÃƒÆ’Ã‚Âªn frontend nÃƒÂ¡Ã‚ÂºÃ‚Â¿u cÃƒÂ¡Ã‚ÂºÃ‚Â§n
- Train trÃƒÂ¡Ã‚ÂºÃ‚Â£ `trained: false`:
  - MÃƒÂ¡Ã‚Â»Ã¢â€žÂ¢t sÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœ symbol khÃƒÆ’Ã‚Â´ng Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã‚Â§ dÃƒÂ¡Ã‚Â»Ã‚Â¯ liÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡u/setup, thÃƒÂ¡Ã‚Â»Ã‚Â­ tÃƒâ€žÃ†â€™ng `limit` hoÃƒÂ¡Ã‚ÂºÃ‚Â·c Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¢i `TRAINING_SYMBOLS`
- NÃƒÂ¡Ã‚ÂºÃ‚Â¿u chÃƒâ€ Ã‚Â°a train model:
  - `GET /api/v1/signals/latest` vÃƒÂ¡Ã‚ÂºÃ‚Â«n chÃƒÂ¡Ã‚ÂºÃ‚Â¡y bÃƒÂ¡Ã‚ÂºÃ‚Â±ng fallback heuristic

## 11) Auto restart backend Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹nh kÃƒÂ¡Ã‚Â»Ã‚Â³ (giÃƒÂ¡Ã‚ÂºÃ‚Â£m RAM leak qua Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Âªm)

### QuÃƒÂ¡Ã‚ÂºÃ‚Â£n lÃƒÆ’Ã‚Â½ backend thÃƒÂ¡Ã‚Â»Ã‚Â§ cÃƒÆ’Ã‚Â´ng

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot
./scripts/backend_service.sh start
./scripts/backend_service.sh status
./scripts/backend_service.sh health
./scripts/backend_service.sh restart
./scripts/backend_service.sh restart-force
./scripts/backend_service.sh stop
./scripts/backend_service.sh stop-force
./scripts/backend_service.sh trim-log
```

- Script chÃƒÂ¡Ã‚ÂºÃ‚Â¡y backend ÃƒÂ¡Ã‚Â»Ã…Â¸ mode production-like (khÃƒÆ’Ã‚Â´ng `--reload`) Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ tiÃƒÂ¡Ã‚ÂºÃ‚Â¿t kiÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¡m RAM.
- Access log HTTP Ãƒâ€žÃ¢â‚¬ËœÃƒÆ’Ã‚Â£ tÃƒÂ¡Ã‚ÂºÃ‚Â¯t (`--no-access-log`) Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ log khÃƒÆ’Ã‚Â´ng phÃƒÆ’Ã‚Â¬nh nhanh.
- KhÃƒÆ’Ã‚Â´ng chÃƒÂ¡Ã‚ÂºÃ‚Â¡y song song cÃƒÂ¡Ã‚ÂºÃ‚Â£ `uvicorn ... --reload` vÃƒÆ’Ã‚Â  `backend_service.sh` ÃƒÂ¡Ã‚Â»Ã…Â¸ 2 tab; nÃƒÆ’Ã‚Âªn chÃƒÂ¡Ã‚Â»Ã‚Ân 1 cÃƒÆ’Ã‚Â¡ch.
- `restart` cÃƒÆ’Ã‚Â³ kiÃƒÂ¡Ã‚Â»Ã†â€™m tra `GET /api/v1/ml/status`:
  - NÃƒÂ¡Ã‚ÂºÃ‚Â¿u `training_in_progress=true` thÃƒÆ’Ã‚Â¬ **khÃƒÆ’Ã‚Â´ng restart** (trÃƒÆ’Ã‚Â¡nh cÃƒÂ¡Ã‚ÂºÃ‚Â¯t ngang train).
- NÃƒÂ¡Ã‚ÂºÃ‚Â¿u muÃƒÂ¡Ã‚Â»Ã¢â‚¬Ëœn restart ngay, dÃƒÆ’Ã‚Â¹ng `restart-force`.
- `stop-force`/`restart-force` sÃƒÂ¡Ã‚ÂºÃ‚Â½ kill process Ãƒâ€žÃ¢â‚¬Ëœang giÃƒÂ¡Ã‚Â»Ã‚Â¯ port `8000`.
- Log `backend.log` tÃƒÂ¡Ã‚Â»Ã‚Â± rotate khi start/restart:
  - `BACKEND_LOG_MAX_MB` (mÃƒÂ¡Ã‚ÂºÃ‚Â·c Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹nh `128`)
  - `BACKEND_LOG_KEEP_FILES` (mÃƒÂ¡Ã‚ÂºÃ‚Â·c Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹nh `5`)
- Log file:
  - `/Users/thang/Desktop/TEST/binance-scalping-bot/backend/.runtime/backend.log`

### CÃƒÆ’Ã‚Â i auto restart mÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i 2 giÃƒÂ¡Ã‚Â»Ã‚Â (macOS - launchd)

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot
./scripts/install_backend_launchd.sh install 7200
./scripts/install_backend_launchd.sh status
```

- LaunchAgent label: `com.thang.binance-scalping-bot.backend-restart`
- File plist:
  - `~/Library/LaunchAgents/com.thang.binance-scalping-bot.backend-restart.plist`
- Launchd gÃƒÂ¡Ã‚Â»Ã‚Âi `./scripts/backend_service.sh restart` nÃƒÆ’Ã‚Âªn cÃƒâ€¦Ã‚Â©ng tÃƒÂ¡Ã‚Â»Ã‚Â± bÃƒÂ¡Ã‚Â»Ã‚Â qua restart khi Ãƒâ€žÃ¢â‚¬Ëœang train.
- CÃƒÆ’Ã‚Â³ thÃƒÂ¡Ã‚Â»Ã†â€™ Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¢i interval: `./scripts/install_backend_launchd.sh install <seconds>`
- Log launchd:
  - `/Users/thang/Desktop/TEST/binance-scalping-bot/backend/.runtime/launchd.out.log`
  - `/Users/thang/Desktop/TEST/binance-scalping-bot/backend/.runtime/launchd.err.log`

### CÃƒÆ’Ã‚Â i auto restart mÃƒÂ¡Ã‚Â»Ã¢â‚¬â€i 2 giÃƒÂ¡Ã‚Â»Ã‚Â (Linux/WSL - cron)

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot
./scripts/install_backend_cron.sh install 2
./scripts/install_backend_cron.sh status
```

- Cron gÃƒÂ¡Ã‚Â»Ã‚Âi `./scripts/backend_service.sh restart` nÃƒÆ’Ã‚Âªn cÃƒâ€¦Ã‚Â©ng tÃƒÂ¡Ã‚Â»Ã‚Â± bÃƒÂ¡Ã‚Â»Ã‚Â qua restart khi Ãƒâ€žÃ¢â‚¬Ëœang train.
- CÃƒÆ’Ã‚Â³ thÃƒÂ¡Ã‚Â»Ã†â€™ Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¢i interval: `./scripts/install_backend_cron.sh install <hours>`
- Log cron:
  - `/Users/thang/Desktop/TEST/binance-scalping-bot/backend/.runtime/cron_restart.log`

### KiÃƒÂ¡Ã‚Â»Ã†â€™m tra trÃƒÂ¡Ã‚ÂºÃ‚Â¡ng thÃƒÆ’Ã‚Â¡i train (Ãƒâ€žÃ¢â‚¬ËœÃƒÂ¡Ã‚Â»Ã†â€™ biÃƒÂ¡Ã‚ÂºÃ‚Â¿t cÃƒÆ’Ã‚Â³ bÃƒÂ¡Ã‚Â»Ã¢â‚¬Â¹ skip restart hay khÃƒÆ’Ã‚Â´ng)

```bash
curl -s http://127.0.0.1:8000/api/v1/ml/status
```

- TrÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Âng `training_in_progress`:
  - `true`: Ãƒâ€žÃ¢â‚¬Ëœang train, `restart` sÃƒÂ¡Ã‚ÂºÃ‚Â½ skip.
  - `false`: restart chÃƒÂ¡Ã‚ÂºÃ‚Â¡y bÃƒÆ’Ã‚Â¬nh thÃƒâ€ Ã‚Â°ÃƒÂ¡Ã‚Â»Ã‚Âng.

### GÃƒÂ¡Ã‚Â»Ã‚Â¡ launchd

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot
./scripts/install_backend_launchd.sh uninstall
```

### GÃƒÂ¡Ã‚Â»Ã‚Â¡ cron (Linux/WSL)

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot
./scripts/install_backend_cron.sh uninstall
```
