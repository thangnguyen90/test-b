from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

import websockets


def _normalize_symbol(symbol: str) -> str:
    # BTC/USDT or BTC/USDT:USDT or BTCUSDT -> BTCUSDT
    return symbol.replace(":USDT", "").replace("/", "").upper().strip()


class BinancePriceStream:
    def __init__(self) -> None:
        self._prices: dict[str, float] = {}
        self._updated_at: dict[str, str] = {}
        self._lock = asyncio.Lock()
        self._task: asyncio.Task | None = None
        self._running = False

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        # Futures all mini-ticker stream: pushes approx 1s updates.
        url = "wss://fstream.binance.com/ws/!miniTicker@arr"
        backoff = 1.0
        while self._running:
            try:
                async with websockets.connect(url, ping_interval=15, ping_timeout=15, close_timeout=5) as ws:
                    backoff = 1.0
                    async for message in ws:
                        if not self._running:
                            break
                        await self._handle_message(message)
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 20.0)

    async def _handle_message(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except Exception:
            return
        if not isinstance(payload, list):
            return
        stamp = datetime.now(timezone.utc).isoformat()
        updates: dict[str, float] = {}
        for row in payload:
            if not isinstance(row, dict):
                continue
            s = row.get("s")
            c = row.get("c")
            if not s or c is None:
                continue
            try:
                px = float(c)
            except Exception:
                continue
            updates[_normalize_symbol(str(s))] = px
        if not updates:
            return

        async with self._lock:
            for key, px in updates.items():
                self._prices[key] = px
                self._updated_at[key] = stamp

    async def get_price(self, symbol: str) -> tuple[float | None, str | None]:
        key = _normalize_symbol(symbol)
        async with self._lock:
            return self._prices.get(key), self._updated_at.get(key)

    async def get_prices(self, symbols: list[str]) -> tuple[dict[str, float], str]:
        out: dict[str, float] = {}
        timestamp = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            for symbol in symbols:
                key = _normalize_symbol(symbol)
                px = self._prices.get(key)
                if px is not None:
                    out[symbol] = px
                stamp = self._updated_at.get(key)
                if stamp:
                    timestamp = stamp
        return out, timestamp
