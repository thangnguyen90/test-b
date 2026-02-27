# Binance Scalping Bot (Backend + Frontend)

## 1) Yêu cầu môi trường

- Python 3.11+ (đang dùng venv tại `.venv`)
- Node.js 18+
- npm
- Internet để train model (fetch dữ liệu Binance qua `ccxt`)

## 2) Cài dependencies

### Backend

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot/backend
cp .env.example .env
/Users/thang/Desktop/TEST/binance-scalping-bot/.venv/bin/pip install -r requirements.txt
```

### Frontend

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot/frontend
npm install
```

## 3) Chạy project

### Chạy Backend (terminal 1)

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot/backend
/Users/thang/Desktop/TEST/binance-scalping-bot/.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### Chạy Frontend (terminal 2)

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot/frontend
npm run dev
```

- Frontend: `http://127.0.0.1:5173`
- Backend API docs: `http://127.0.0.1:8000/docs`

## 4) Train model ML (RandomForest)

### Cách 1: curl

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ml/train \
  -H 'Content-Type: application/json' \
  -d '{"limit": 800, "horizon": 4, "rr_ratio": 1.5}'
```

### Cách 2: Swagger UI

- Mở `http://127.0.0.1:8000/docs`
- Chọn endpoint `POST /api/v1/ml/train`
- Bấm `Try it out` -> `Execute`

## 5) Kiểm tra trạng thái model

```bash
curl http://127.0.0.1:8000/api/v1/ml/status
```

Kỳ vọng:
- `is_loaded: true` sau khi train thành công
- `accuracy`, `roc_auc`, `trained_at` có giá trị

## 6) Command test nhanh API

### Health

```bash
curl http://127.0.0.1:8000/health
```

### Lấy signal mới nhất

```bash
curl "http://127.0.0.1:8000/api/v1/signals/latest?symbol=BTC/USDT&mark_price=62000"
```

### Lấy full symbol Binance Futures (USDT perpetual)

```bash
curl http://127.0.0.1:8000/api/v1/market/symbols
```

### Lấy giá thật theo symbol

```bash
curl "http://127.0.0.1:8000/api/v1/market/price?symbol=RAVE/USDT:USDT"
```

### Paper Trading (MySQL)

```bash
curl http://127.0.0.1:8000/api/v1/paper-trades/stats
curl http://127.0.0.1:8000/api/v1/paper-trades/open
curl "http://127.0.0.1:8000/api/v1/paper-trades/history?limit=100"
```

### Analytics tables

```bash
curl "http://127.0.0.1:8000/api/v1/analytics/top-volatility?days=1&limit=30"
curl "http://127.0.0.1:8000/api/v1/analytics/top-volatility?days=3&limit=30"
curl "http://127.0.0.1:8000/api/v1/analytics/top-volatility?days=5&limit=30"
curl "http://127.0.0.1:8000/api/v1/analytics/top-volatility?days=7&limit=30"
curl "http://127.0.0.1:8000/api/v1/analytics/liquidation-overview?limit=30"
```

### Tạo lệnh pending demo

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

### Xem danh sách lệnh

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

## 8) Biến môi trường chính (`backend/.env`)

```env
APP_NAME=Binance Scalping Bot API
APP_ENV=development
HOST=127.0.0.1
PORT=8000
ALLOWED_ORIGINS=http://localhost:5173
SQLITE_DB_PATH=/Users/thang/Desktop/TEST/binance-scalping-bot/backend/backend_data/trading_bot.db
ML_MODEL_PATH=/Users/thang/Desktop/TEST/binance-scalping-bot/backend/backend_data/rf_model.joblib
TRAINING_SYMBOLS=SOL/USDT,XRP/USDT,ADA/USDT,DOGE/USDT
ML_FEEDBACK_TRAIN_LIMIT=1200
WS_PING_INTERVAL_SEC=1.0
MYSQL_ENABLED=false
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=
MYSQL_DATABASE=trading_bot
PAPER_TRADE_MIN_WIN=0.75
PAPER_TRADE_QUANTITY=0.01
PAPER_TRADE_LEVERAGE=5
PAPER_TRADE_POLL_INTERVAL_SEC=6
PAPER_TRADE_MIN_SL_PCT=0.004
PAPER_TRADE_MIN_RR=1.5
PAPER_TRADE_MAX_RISK_PCT=12
PAPER_TRADE_MAX_HOLD_MINUTES=120
PAPER_TRADE_DISABLE_SL=false
PAPER_TRADE_MOVE_SL_TO_ENTRY_PNL_PCT=15
```

## 9) Troubleshooting nhanh

- Lỗi `pip: command not found`:
  - Luôn dùng pip trong venv:
  - `/Users/thang/Desktop/TEST/binance-scalping-bot/.venv/bin/pip ...`
- Lỗi không bind được cổng `8000`:
  - Đổi port backend, ví dụ `--port 8001`
  - Đồng thời sửa API base bên frontend nếu cần
- Train trả `trained: false`:
  - Một số symbol không đủ dữ liệu/setup, thử tăng `limit` hoặc đổi `TRAINING_SYMBOLS`
- Nếu chưa train model:
  - `GET /api/v1/signals/latest` vẫn chạy bằng fallback heuristic
