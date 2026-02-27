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
