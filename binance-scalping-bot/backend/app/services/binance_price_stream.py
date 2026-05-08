from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
import time

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
        self._last_update_monotonic: float = 0.0
        self._last_connect_error: str | None = None

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
        # Prefer all-bookTicker (fastest), fallback to markPrice/miniTicker.
        urls = [
            "wss://fstream.binance.com/ws/!bookTicker",
            "wss://fstream.binance.com/ws/!markPrice@arr@1s",
            "wss://fstream.binance.com/ws/!miniTicker@arr",
        ]
        backoff = 1.0
        url_index = 0
        while self._running:
            try:
                url = urls[url_index % len(urls)]
                async with websockets.connect(url, ping_interval=15, ping_timeout=15, close_timeout=5) as ws:
                    backoff = 1.0
                    url_index = 0
                    self._last_connect_error = None
                    while self._running:
                        try:
                            message = await asyncio.wait_for(ws.recv(), timeout=25.0)
                        except asyncio.TimeoutError:
                            # The socket can stay open while no messages arrive anymore.
                            # Force a reconnect so stream cache does not freeze indefinitely.
                            break
                        if not self._running:
                            break
                        await self._handle_message(message)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._last_connect_error = f"{type(exc).__name__}: {exc}"
                url_index += 1
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 20.0)

    async def _handle_message(self, raw: str) -> None:
        try:
            payload = json.loads(raw)
        except Exception:
            return
        if isinstance(payload, dict):
            rows = [payload]
        elif isinstance(payload, list):
            rows = payload
        else:
            return
        updates: dict[str, float] = {}
        stamps: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            s = row.get("s")
            if not s:
                continue
            px: float | None = None
            # bookTicker: use mid price of best bid/ask for fastest movement
            bid_raw = row.get("b")
            ask_raw = row.get("a")
            if bid_raw is not None and ask_raw is not None:
                try:
                    px = (float(bid_raw) + float(ask_raw)) / 2.0
                except Exception:
                    px = None
            # markPrice stream uses 'p'
            if px is None and row.get("p") is not None:
                try:
                    px = float(row.get("p"))
                except Exception:
                    px = None
            # miniTicker uses 'c'
            if px is None and row.get("c") is not None:
                try:
                    px = float(row.get("c"))
                except Exception:
                    px = None
            if px is None:
                continue
            key = _normalize_symbol(str(s))
            updates[key] = px
            event_ms = row.get("E")
            if event_ms is not None:
                try:
                    stamps[key] = datetime.fromtimestamp(float(event_ms) / 1000, tz=timezone.utc).isoformat()
                except Exception:
                    stamps[key] = datetime.now(timezone.utc).isoformat()
            else:
                stamps[key] = datetime.now(timezone.utc).isoformat()
        if not updates:
            return

        async with self._lock:
            for key, px in updates.items():
                self._prices[key] = px
                self._updated_at[key] = stamps.get(key, datetime.now(timezone.utc).isoformat())
            self._last_update_monotonic = time.monotonic()

    async def get_price(self, symbol: str) -> tuple[float | None, str | None]:
        key = _normalize_symbol(symbol)
        async with self._lock:
            return self._prices.get(key), self._updated_at.get(key)

    async def get_prices(self, symbols: list[str]) -> tuple[dict[str, float], str, dict[str, str]]:
        out: dict[str, float] = {}
        out_ts: dict[str, str] = {}
        timestamp = datetime.now(timezone.utc).isoformat()
        async with self._lock:
            for symbol in symbols:
                key = _normalize_symbol(symbol)
                px = self._prices.get(key)
                if px is not None:
                    out[symbol] = px
                stamp = self._updated_at.get(key)
                if stamp:
                    out_ts[symbol] = stamp
                    timestamp = stamp
        return out, timestamp, out_ts

    async def status(self, sample_symbols: list[str] | None = None) -> dict[str, Any]:
        sample = sample_symbols or ["BTC/USDT", "ETH/USDT"]
        prices, timestamp, timestamps = await self.get_prices(sample)
        async with self._lock:
            total_cached = len(self._prices)
            last_update_monotonic = self._last_update_monotonic
            last_connect_error = self._last_connect_error
        age_sec: float | None = None
        if last_update_monotonic > 0:
            age_sec = max(0.0, time.monotonic() - last_update_monotonic)
        return {
            "cached_symbols": total_cached,
            "sample_prices": prices,
            "last_timestamp": timestamp,
            "sample_timestamps": timestamps,
            "last_update_age_sec": age_sec,
            "running": bool(self._running),
            "task_alive": bool(self._task and not self._task.done()),
            "last_connect_error": last_connect_error,
        }
