import json
import urllib.request
import urllib.error

payload = json.dumps({
    "symbol": "BTC/USDT:USDT",
    "side": "LONG",
    "quantity": 0.001,
    "mode": "test",
}).encode("utf-8")
req = urllib.request.Request(
    "http://127.0.0.1:8005/api/v1/binance-execution/ml-candles-bg/order",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=20) as resp:
        print(resp.status)
        print(resp.read().decode("utf-8"))
except urllib.error.HTTPError as exc:
    print(exc.code)
    print(exc.read().decode("utf-8"))
