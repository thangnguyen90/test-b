from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from app.services.binance_client import BinanceFuturesClient

_VN_TZ = timezone(timedelta(hours=7))


def _parse_dt(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(text, fmt)
            except Exception:
                continue
    return None


class CandlePatternAnalyzer:
    _TIMEFRAME_MS = {
        "5m": 5 * 60 * 1000,
        "1h": 60 * 60 * 1000,
    }

    def __init__(self, client: BinanceFuturesClient | None = None) -> None:
        self.client = client or BinanceFuturesClient()
        self._pattern_cache: dict[str, str] = {}
        self._btc_trend_cache: dict[str, str] = {}

    def current_symbol_pattern(self, symbol: str) -> str | None:
        return self._resolve_symbol_pattern(symbol=symbol, at_dt=None)

    def symbol_pattern_at(self, symbol: str, at_dt: object) -> str | None:
        dt = _parse_dt(at_dt)
        if dt is None:
            return None
        return self._resolve_symbol_pattern(symbol=symbol, at_dt=dt)

    def btc_trend_now(self) -> str | None:
        return self._resolve_btc_trend(at_dt=None)

    def btc_trend_at(self, at_dt: object) -> str | None:
        dt = _parse_dt(at_dt)
        if dt is None:
            return None
        return self._resolve_btc_trend(at_dt=dt)

    def _resolve_symbol_pattern(self, *, symbol: str, at_dt: datetime | None) -> str | None:
        safe_symbol = str(symbol or "").strip()
        if not safe_symbol:
            return None
        cache_key = f"pattern:{safe_symbol}:{self._cache_bucket(at_dt, timeframe='5m')}"
        cached = self._pattern_cache.get(cache_key)
        if cached is not None:
            return cached

        rows = self._fetch_window(symbol=safe_symbol, timeframe="5m", at_dt=at_dt, limit=24)
        if len(rows) < 2:
            return None

        target_ms = self._target_ms(at_dt)
        scoped = [row for row in rows if at_dt is None or int(row[0]) <= target_ms]
        if len(scoped) < 2:
            scoped = rows[-2:]
        pattern = self._classify_pattern(prev_row=scoped[-2], row=scoped[-1])
        self._pattern_cache[cache_key] = pattern
        return pattern

    def _resolve_btc_trend(self, *, at_dt: datetime | None) -> str | None:
        cache_key = f"btc:{self._cache_bucket(at_dt, timeframe='1h')}"
        cached = self._btc_trend_cache.get(cache_key)
        if cached is not None:
            return cached

        rows = self._fetch_window(symbol="BTC/USDT", timeframe="1h", at_dt=at_dt, limit=96)
        if not rows:
            return None

        target_ms = self._target_ms(at_dt)
        scoped = [row for row in rows if at_dt is None or int(row[0]) <= target_ms]
        if len(scoped) < 21:
            scoped = rows[-21:]
        if len(scoped) < 21:
            return None

        closes = pd.Series([float(row[4]) for row in scoped], dtype=float)
        ema8 = float(closes.ewm(span=8, adjust=False).mean().iloc[-1])
        ema13 = float(closes.ewm(span=13, adjust=False).mean().iloc[-1])
        ema21 = float(closes.ewm(span=21, adjust=False).mean().iloc[-1])
        close = float(closes.iloc[-1])

        if close >= ema21 and ema8 >= ema13 >= ema21:
            trend = "LONG"
        elif close <= ema21 and ema8 <= ema13 <= ema21:
            trend = "SHORT"
        else:
            trend = "NEUTRAL"
        self._btc_trend_cache[cache_key] = trend
        return trend

    def _fetch_window(
        self,
        *,
        symbol: str,
        timeframe: str,
        at_dt: datetime | None,
        limit: int,
    ) -> list[list[Any]]:
        safe_limit = max(8, int(limit))
        tf_ms = self._TIMEFRAME_MS[timeframe]
        if at_dt is None:
            return self.client.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=safe_limit)

        target_ms = self._target_ms(at_dt)
        since = max(0, target_ms - (safe_limit * tf_ms))
        return self.client.fetch_ohlcv(
            symbol=symbol,
            timeframe=timeframe,
            limit=safe_limit,
            since=since,
        )

    def _cache_bucket(self, at_dt: datetime | None, *, timeframe: str) -> str:
        tf_ms = self._TIMEFRAME_MS[timeframe]
        if at_dt is None:
            now_ms = int(datetime.now(tz=timezone.utc).timestamp() * 1000)
            return str(now_ms // tf_ms)
        return str(self._target_ms(at_dt) // tf_ms)

    @staticmethod
    def _target_ms(at_dt: datetime | None) -> int:
        if at_dt is None:
            return int(datetime.now(tz=timezone.utc).timestamp() * 1000)
        if at_dt.tzinfo is None:
            aware = at_dt.replace(tzinfo=_VN_TZ)
        else:
            aware = at_dt.astimezone(timezone.utc)
        return int(aware.timestamp() * 1000)

    @staticmethod
    def _classify_pattern(prev_row: list[Any], row: list[Any]) -> str:
        prev_open = float(prev_row[1])
        prev_high = float(prev_row[2])
        prev_low = float(prev_row[3])
        prev_close = float(prev_row[4])
        open_price = float(row[1])
        high_price = float(row[2])
        low_price = float(row[3])
        close_price = float(row[4])

        candle_range = max(high_price - low_price, 1e-12)
        prev_range = max(prev_high - prev_low, 1e-12)
        body = abs(close_price - open_price)
        prev_body = abs(prev_close - prev_open)
        body_ratio = body / candle_range
        upper_wick = max(0.0, high_price - max(open_price, close_price))
        lower_wick = max(0.0, min(open_price, close_price) - low_price)
        bullish = close_price > open_price
        bearish = close_price < open_price
        prev_bullish = prev_close > prev_open
        prev_bearish = prev_close < prev_open

        if body_ratio <= 0.1:
            return "DOJI"
        if bullish and prev_bearish and open_price <= prev_close and close_price >= prev_open:
            return "BULL_ENGULFING"
        if bearish and prev_bullish and open_price >= prev_close and close_price <= prev_open:
            return "BEAR_ENGULFING"
        if body_ratio <= 0.35 and lower_wick >= (body * 2.0) and upper_wick <= max(body * 0.75, candle_range * 0.12):
            return "HAMMER"
        if body_ratio <= 0.35 and upper_wick >= (body * 2.0) and lower_wick <= max(body * 0.75, candle_range * 0.12):
            return "SHOOTING_STAR"
        if high_price <= prev_high and low_price >= prev_low and prev_range > 0:
            return "INSIDE_BAR"
        if bullish and body_ratio >= 0.68:
            return "STRONG_BULL"
        if bearish and body_ratio >= 0.68:
            return "STRONG_BEAR"
        if bullish:
            return "BULL_BODY"
        if bearish:
            return "BEAR_BODY"
        return "RANGE"
