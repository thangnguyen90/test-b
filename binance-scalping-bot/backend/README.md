# Backend

Hướng dẫn đầy đủ command setup/run/train/test nằm tại:

- `/Users/thang/Desktop/TEST/binance-scalping-bot/README.md`

Quick run:

```bash
cd /Users/thang/Desktop/TEST/binance-scalping-bot/backend
cp .env.example .env
/Users/thang/Desktop/TEST/binance-scalping-bot/.venv/bin/pip install -r requirements.txt
/Users/thang/Desktop/TEST/binance-scalping-bot/.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```



cd /Users/thang/Desktop/TEST/binance-scalping-bot
bash scripts/backend_service.sh restart-force


cd /Users/thang/Desktop/TEST/binance-scalping-bot/frontend
pkill -f "vite --host 127.0.0.1 --port 5199" || true
npm run dev -- --host 127.0.0.1 --port 5199


http://127.0.0.1:5199/?view=paper
http://127.0.0.1:5199/?view=daily
http://127.0.0.1:5199/?view=ml-candles-signals
http://127.0.0.1:5199/?view=ml-candles-compare