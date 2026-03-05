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

### Train model riêng cho liquid + EMA99 (Top Volatility)

```bash
curl -X POST http://127.0.0.1:8000/api/v1/ml/liquid/train \
  -H 'Content-Type: application/json' \
  -d '{"limit": 900, "horizon": 16, "rr_ratio": 1.5, "top_vol_days": 1, "max_symbols": 30}'
```

## 5) Kiểm tra trạng thái model

```bash
curl http://127.0.0.1:8000/api/v1/ml/status
```

```bash
curl http://127.0.0.1:8000/api/v1/ml/liquid/status
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

- `history` hiện có thêm `close_reason` để biết lệnh đóng do `SL`, `TP`, `TIMEOUT_*` hay `MANUAL_*`.

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
PAPER_TRADE_BTC_FILTER_ENABLED=true
PAPER_TRADE_BTC_FILTER_TIMEFRAME=15m
PAPER_TRADE_BTC_FILTER_CACHE_SEC=20
PAPER_TRADE_BTC_FILTER_MIN_CONFIDENCE=0.55
PAPER_TRADE_BTC_FILTER_BLOCK_COUNTERTREND=true
PAPER_TRADE_BTC_FILTER_COUNTERTREND_MIN_WIN=0.77
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

- `PAPER_TRADE_ORDER_USDT` là giá trị lệnh theo USDT (notional, chưa tính margin).
- Nếu không truyền `quantity` khi mở lệnh, backend sẽ tự tính `quantity = PAPER_TRADE_ORDER_USDT / entry_price`.
- `PAPER_TRADE_MARGIN_USDT` là margin dùng để tính PnL% (ROI margin).  
: đặt `0` để tự tính theo công thức `entry_price * quantity / leverage`.
- `PAPER_TRADE_MAINT_MARGIN_RATE` dùng để ước tính `Signal Margin Ratio%` (kiểu Binance `Tỉ lệ ký quỹ`) với công thức xấp xỉ:  
: `margin_ratio_pct ~= leverage * maint_margin_rate * 100`.
- `PAPER_TRADE_QUANTITY` chỉ dùng fallback khi không tính được từ giá.
- `PAPER_TRADE_MIN_SL_PCT` + `PAPER_TRADE_SL_EXTRA_BUFFER_PCT` giúp kéo SL xa hơn để tránh bị quét quá sớm.
- `PAPER_TRADE_SL_ATR_MULTIPLIER` dùng ATR để đặt ngưỡng SL tối thiểu theo biến động (0 = tắt ATR).
- `PAPER_TRADE_MAX_TP_PCT` giới hạn TP tối đa theo `% giá vào` (mặc định 15%).  
: ví dụ `15` nghĩa là TP không vượt quá `entry +/- 15%`.
- `PAPER_TRADE_MOVE_SL_TO_ENTRY_PNL_PCT` là ngưỡng kích hoạt dời SL theo `%PnL margin` tại `PAPER_TRADE_MOVE_SL_REFERENCE_LEVERAGE` (ví dụ 5% tại 5x).
- `PAPER_TRADE_MOVE_SL_LOCK_PNL_PCT` là mức lợi nhuận giữ lại sau khi kích hoạt (ví dụ 10% ở 5x ~ dời SL về mức +2% giá theo hướng có lợi).
- `PAPER_TRADE_MOVE_SL_SCALE_BY_LEVERAGE=true` sẽ tự scale ngưỡng theo leverage thực tế của lệnh.  
: ví dụ cấu hình `trigger=5`, `reference_leverage=5` thì lệnh 10x sẽ kích hoạt ở `10%` PnL margin.
- `PAPER_TRADE_BTC_SHOCK_THRESHOLD_PCT` là ngưỡng sốc BTC theo `%` (dựa trên biến động close-to-close hoặc range nến).
- `PAPER_TRADE_BTC_SHOCK_COOLDOWN_MINUTES` là thời gian khóa tối thiểu cho lệnh cùng chiều sau shock.
- `PAPER_TRADE_BTC_SHOCK_UP_LONG_BLOCK_MINUTES` khóa riêng lệnh `LONG` sau shock tăng mạnh của BTC.
- `PAPER_TRADE_BTC_SHOCK_DOWN_SHORT_BLOCK_MINUTES` khóa riêng lệnh `SHORT` sau shock giảm mạnh của BTC.
- `PAPER_TRADE_BTC_SHOCK_UP_REQUIRE_PULLBACK=true` chỉ mở lại lệnh cùng chiều shock khi BTC pullback về EMA (UP: chờ hạ nhiệt để mở LONG, DOWN: chờ hồi lên để mở SHORT).
- `PAPER_TRADE_BTC_SHOCK_PULLBACK_EMA_PERIOD` chọn EMA dùng xác nhận pullback (`21` hoặc `55`).
- `PAPER_TRADE_BTC_SHOCK_PULLBACK_TOLERANCE_PCT` là biên độ cho phép quanh EMA để tính là đã pullback.
- `PAPER_TRADE_BTC_REVERSAL_PROFIT_EXIT_ENABLED=true` sẽ đóng nhanh các lệnh `LONG` đang lãi khi BTC đảo chiều giảm mạnh.
- `PAPER_TRADE_BTC_REVERSAL_THRESHOLD_PCT` là ngưỡng đảo chiều mạnh theo `%` để kích hoạt đóng nhanh.
- `PAPER_TRADE_BTC_REVERSAL_MIN_CONFIDENCE` là độ tin cậy trend SHORT tối thiểu để đóng nhanh (nếu chưa đủ thì vẫn có nhánh fallback khi shock cực mạnh).
- DB lưu thêm `mae_pct`/`mfe_pct` cho mỗi paper trade (theo % margin), dùng để đánh giá quality tín hiệu.
- Khi train từ `ml_feedback`, nhãn được ưu tiên theo `close_reason`:
: `TP` => mẫu tốt (`WIN`), `SL` => mẫu xấu (`LOSS`).
- Khi train từ `ml_feedback`, nếu `ML_FEEDBACK_FLIP_WIN_ON_DEEP_MAE=true` và lệnh WIN nhưng `mae_pct <= -ML_FEEDBACK_MAE_PENALTY_PCT`, sample sẽ bị đổi nhãn thành LOSS để phạt setup bị âm quá sâu.
- Với case "âm sâu lâu, cuối cùng chỉ hồi nhẹ/breakeven", bật:
: `ML_FEEDBACK_RECOVERY_PENALTY_ENABLED=true`.
: Khi `mae_pct <= -ML_FEEDBACK_RECOVERY_PENALTY_MAE_PCT` và `pnl_pct <= ML_FEEDBACK_RECOVERY_PENALTY_MAX_PNL_PCT`,
: sample WIN vẫn giữ nhãn nhưng bị giảm trọng số train theo `ML_FEEDBACK_RECOVERY_PENALTY_WEIGHT_FACTOR`.
- Với case tín hiệu tốt (lãi tốt và drawdown thấp), bật:
: `ML_FEEDBACK_GOOD_SIGNAL_BOOST_ENABLED=true`.
: Khi `pnl_pct >= ML_FEEDBACK_GOOD_SIGNAL_MIN_PNL_PCT` và `mae_pct >= -ML_FEEDBACK_GOOD_SIGNAL_MAX_MAE_PCT`,
: sample WIN sẽ được tăng trọng số train theo `ML_FEEDBACK_GOOD_SIGNAL_WEIGHT_MULTIPLIER`.
- Có thể cấu hình leverage riêng cho coin lớn:
: `PAPER_TRADE_MAJOR_SYMBOLS`, `PAPER_TRADE_MAJOR_LEVERAGE`.
: Ví dụ đặt `PAPER_TRADE_MAJOR_LEVERAGE=10` cho BTC/ETH/BNB/SOL.
: Risk gate riêng dùng `PAPER_TRADE_MAJOR_MAX_RISK_PCT` (nên >= `leverage * maint_margin_rate * 100`).
- Có thể bật major dynamic (khuyến nghị) để không phụ thuộc list cứng:
: `PAPER_TRADE_MAJOR_DYNAMIC_ENABLED=true`.
: Engine tự chọn coin lớn từ **tín hiệu model hiện tại + hiệu quả DB + dữ liệu nến 5m**.
: Điều chỉnh bằng `PAPER_TRADE_MAJOR_DYNAMIC_REFRESH_SEC`, `PAPER_TRADE_MAJOR_DYNAMIC_LIMIT`,
: `PAPER_TRADE_MAJOR_DYNAMIC_CANDIDATES`, `PAPER_TRADE_MAJOR_DYNAMIC_CANDLE_LOOKBACK`.
- `PAPER_TRADE_MIN_SL_LOSS_PCT` = mức lỗ tối thiểu theo `% giá trị lệnh (order_usdt)` khi chạm SL.  
: ví dụ đặt `5` thì khoảng cách SL tối thiểu theo giá sẽ là `5%`.
- `ML_USE_LIQUIDATION_FEATURES=true` bật thêm nhóm feature liquidation proxy (wick + volume spike trên nến 5m) khi train ML.
- `LIQUID_ML_ENABLED=true` bật model riêng cho liquid + EMA99 (15m/1h), chạy trên danh sách top volatility coin.
- Entry của model liquid neo theo EMA99 gần nhất (15m hoặc 1h), sau đó vẫn đi qua normalize TP/SL và rule risk chung trước khi mở lệnh.
- Paper trade sẽ lưu thêm `entry_type` để phân biệt:
: `MARKET` (mở tay bằng nút Market Open) và `LIMIT` (auto khớp theo entry tín hiệu).
- Màn hình Paper Trade Stats có thêm `Market Win Rate` và `Limit Win Rate` để so sánh hiệu quả.
- Có bảng `Entry Type Breakdown` (MARKET/LIMIT): Closed, Win/Loss, Win Rate, Total/Avg PnL (USDT và %).

## 9) Auto-train định kỳ (macOS + Linux/WSL)

- Auto-train chạy bên trong backend process nên dùng được trên cả macOS, Linux và WSL.
- Không cần cron/launchd để train.
- Mặc định: `AUTO_TRAIN_ENABLED=true`, chạy mỗi `240` phút.

### Kiểm tra trạng thái auto-train

```bash
curl -s http://127.0.0.1:8000/api/v1/ml/status
```

Các field mới:
- `training_in_progress`: đang có lượt train chạy hay không.
- `auto_train_enabled`: bật/tắt auto-train.
- `auto_train_running`: scheduler có đang chạy trong backend không.
- `auto_train_interval_minutes`: chu kỳ train.
- `auto_train_next_run_at`: thời điểm UTC dự kiến chạy lần kế tiếp.
- `auto_train_last_run_started_at`, `auto_train_last_run_finished_at`, `auto_train_last_result`.
- `last_train_trigger`, `last_train_started_at`, `last_train_finished_at`, `last_train_duration_sec`, `last_train_result`, `last_train_error`.
- `train_log_path`: path file log train JSONL.

### Log train

- File log:
  - `/Users/thang/Desktop/TEST/binance-scalping-bot/backend/.runtime/ml_train.log`
- Mỗi lần train sẽ có dòng `START` và `FINISH` (JSON), gồm trigger (`manual`/`auto`), thời gian bắt đầu/kết thúc, duration, result/error.
- Frontend có card `ML Training Monitor` để xem trực tiếp thời gian bắt đầu/kết thúc (đổi sang giờ VN).

### Bật/tắt nhanh

Trong `backend/.env`:

```env
AUTO_TRAIN_ENABLED=true
AUTO_TRAIN_INTERVAL_MINUTES=240
```

Sau khi sửa `.env`, restart backend:

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot
./scripts/backend_service.sh restart
```

## 10) Troubleshooting nhanh

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

## 11) Auto restart backend định kỳ (giảm RAM leak qua đêm)

### Quản lý backend thủ công

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

- Script chạy backend ở mode production-like (không `--reload`) để tiết kiệm RAM.
- Access log HTTP đã tắt (`--no-access-log`) để log không phình nhanh.
- Không chạy song song cả `uvicorn ... --reload` và `backend_service.sh` ở 2 tab; nên chọn 1 cách.
- `restart` có kiểm tra `GET /api/v1/ml/status`:
  - Nếu `training_in_progress=true` thì **không restart** (tránh cắt ngang train).
- Nếu muốn restart ngay, dùng `restart-force`.
- `stop-force`/`restart-force` sẽ kill process đang giữ port `8000`.
- Log `backend.log` tự rotate khi start/restart:
  - `BACKEND_LOG_MAX_MB` (mặc định `128`)
  - `BACKEND_LOG_KEEP_FILES` (mặc định `5`)
- Log file:
  - `/Users/thang/Desktop/TEST/binance-scalping-bot/backend/.runtime/backend.log`

### Cài auto restart mỗi 2 giờ (macOS - launchd)

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot
./scripts/install_backend_launchd.sh install 7200
./scripts/install_backend_launchd.sh status
```

- LaunchAgent label: `com.thang.binance-scalping-bot.backend-restart`
- File plist:
  - `~/Library/LaunchAgents/com.thang.binance-scalping-bot.backend-restart.plist`
- Launchd gọi `./scripts/backend_service.sh restart` nên cũng tự bỏ qua restart khi đang train.
- Có thể đổi interval: `./scripts/install_backend_launchd.sh install <seconds>`
- Log launchd:
  - `/Users/thang/Desktop/TEST/binance-scalping-bot/backend/.runtime/launchd.out.log`
  - `/Users/thang/Desktop/TEST/binance-scalping-bot/backend/.runtime/launchd.err.log`

### Cài auto restart mỗi 2 giờ (Linux/WSL - cron)

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot
./scripts/install_backend_cron.sh install 2
./scripts/install_backend_cron.sh status
```

- Cron gọi `./scripts/backend_service.sh restart` nên cũng tự bỏ qua restart khi đang train.
- Có thể đổi interval: `./scripts/install_backend_cron.sh install <hours>`
- Log cron:
  - `/Users/thang/Desktop/TEST/binance-scalping-bot/backend/.runtime/cron_restart.log`

### Kiểm tra trạng thái train (để biết có bị skip restart hay không)

```bash
curl -s http://127.0.0.1:8000/api/v1/ml/status
```

- Trường `training_in_progress`:
  - `true`: đang train, `restart` sẽ skip.
  - `false`: restart chạy bình thường.

### Gỡ launchd

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot
./scripts/install_backend_launchd.sh uninstall
```

### Gỡ cron (Linux/WSL)

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot
./scripts/install_backend_cron.sh uninstall
```
