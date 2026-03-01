from __future__ import annotations

import re
import threading
import time
from typing import Any

import ccxt


class BinanceRateLimitBanError(Exception):
    def __init__(self, message: str, ban_until_ms: int | None = None) -> None:
        super().__init__(message)
        self.ban_until_ms = ban_until_ms


class BinanceFuturesClient:
    _ban_until_ms: int = 0
    _cache: dict[str, tuple[float, Any]] = {}
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.exchange = None

    def _get_exchange(self) -> ccxt.binanceusdm:
        if self.exchange is None:
            self.exchange = ccxt.binanceusdm({"enableRateLimit": True})
        return self.exchange

    @classmethod
    def _set_ban_until(cls, ban_until_ms: int) -> None:
        with cls._lock:
            cls._ban_until_ms = max(cls._ban_until_ms, int(ban_until_ms))

    @classmethod
    def _get_ban_until(cls) -> int:
        with cls._lock:
            return cls._ban_until_ms

    @classmethod
    def _is_banned(cls) -> bool:
        return int(time.time() * 1000) < cls._get_ban_until()

    @classmethod
    def _cache_get(cls, key: str, ttl_sec: float, allow_stale: bool = False) -> Any | None:
        now = time.time()
        with cls._lock:
            item = cls._cache.get(key)
        if item is None:
            return None
        ts, payload = item
        if allow_stale or (now - ts) <= ttl_sec:
            return payload
        return None

    @classmethod
    def _cache_set(cls, key: str, payload: Any) -> None:
        with cls._lock:
            cls._cache[key] = (time.time(), payload)

    @staticmethod
    def _extract_ban_until_ms(message: str) -> int | None:
        m = re.search(r"banned until (\d+)", message)
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    def _handle_upstream_error(self, exc: Exception, fallback_ban_sec: int = 120) -> None:
        message = str(exc)
        lowered = message.lower()
        is_rate_ban = (
            "code\":-1003" in lowered
            or "i'm a teapot" in lowered
            or "too many requests" in lowered
            or "rate limit" in lowered
        )
        if not is_rate_ban:
            raise exc
        ban_until = self._extract_ban_until_ms(message)
        if ban_until is None:
            ban_until = int((time.time() + fallback_ban_sec) * 1000)
        self._set_ban_until(ban_until)
        raise BinanceRateLimitBanError(message, ban_until_ms=ban_until) from exc

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 1000) -> list[list[Any]]:
        key = f"ohlcv:{symbol}:{timeframe}:{limit}"
        ttl = 10.0 if timeframe in {"1m", "3m", "5m"} else 30.0
        cached = self._cache_get(key, ttl_sec=ttl)
        if cached is not None:
            return cached
        if self._is_banned():
            stale = self._cache_get(key, ttl_sec=ttl, allow_stale=True)
            if stale is not None:
                return stale
            raise BinanceRateLimitBanError("Binance REST is temporarily banned", self._get_ban_until())
        exchange = self._get_exchange()
        try:
            rows = exchange.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)
            self._cache_set(key, rows)
            return rows
        except Exception as exc:
            try:
                self._handle_upstream_error(exc)
            except BinanceRateLimitBanError:
                stale = self._cache_get(key, ttl_sec=ttl, allow_stale=True)
                if stale is not None:
                    return stale
                raise
            stale = self._cache_get(key, ttl_sec=ttl, allow_stale=True)
            if stale is not None:
                return stale
            raise

    def load_markets(self) -> dict[str, Any]:
        key = "markets:all"
        ttl = 600.0
        cached = self._cache_get(key, ttl_sec=ttl)
        if cached is not None:
            return cached
        if self._is_banned():
            stale = self._cache_get(key, ttl_sec=ttl, allow_stale=True)
            if stale is not None:
                return stale
            raise BinanceRateLimitBanError("Binance REST is temporarily banned", self._get_ban_until())
        exchange = self._get_exchange()
        try:
            payload = exchange.load_markets()
            self._cache_set(key, payload)
            return payload
        except Exception as exc:
            try:
                self._handle_upstream_error(exc)
            except BinanceRateLimitBanError:
                stale = self._cache_get(key, ttl_sec=ttl, allow_stale=True)
                if stale is not None:
                    return stale
                raise
            stale = self._cache_get(key, ttl_sec=ttl, allow_stale=True)
            if stale is not None:
                return stale
            raise

    def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        key = f"ticker:{symbol}"
        ttl = 2.0
        cached = self._cache_get(key, ttl_sec=ttl)
        if cached is not None:
            return cached
        if self._is_banned():
            stale = self._cache_get(key, ttl_sec=ttl, allow_stale=True)
            if stale is not None:
                return stale
            raise BinanceRateLimitBanError("Binance REST is temporarily banned", self._get_ban_until())
        exchange = self._get_exchange()
        try:
            payload = exchange.fetch_ticker(symbol=symbol)
            self._cache_set(key, payload)
            return payload
        except Exception as exc:
            try:
                self._handle_upstream_error(exc)
            except BinanceRateLimitBanError:
                stale = self._cache_get(key, ttl_sec=ttl, allow_stale=True)
                if stale is not None:
                    return stale
                raise
            stale = self._cache_get(key, ttl_sec=ttl, allow_stale=True)
            if stale is not None:
                return stale
            raise

    def fetch_tickers(self, symbols: list[str] | None = None) -> dict[str, Any]:
        key = f"tickers:{','.join(sorted(symbols))}" if symbols else "tickers:all"
        ttl = 8.0
        cached = self._cache_get(key, ttl_sec=ttl)
        if cached is not None:
            return cached
        if self._is_banned():
            stale = self._cache_get(key, ttl_sec=ttl, allow_stale=True)
            if stale is not None:
                return stale
            raise BinanceRateLimitBanError("Binance REST is temporarily banned", self._get_ban_until())
        exchange = self._get_exchange()
        try:
            if symbols:
                payload = exchange.fetch_tickers(symbols=symbols)
            else:
                payload = exchange.fetch_tickers()
            self._cache_set(key, payload)
            return payload
        except Exception as exc:
            try:
                self._handle_upstream_error(exc)
            except BinanceRateLimitBanError:
                stale = self._cache_get(key, ttl_sec=ttl, allow_stale=True)
                if stale is not None:
                    return stale
                raise
            stale = self._cache_get(key, ttl_sec=ttl, allow_stale=True)
            if stale is not None:
                return stale
            raise

    @classmethod
    def rest_status(cls) -> dict[str, Any]:
        ban_until_ms = cls._get_ban_until()
        banned = int(time.time() * 1000) < ban_until_ms
        return {
            "rest_banned": banned,
            "ban_until_ms": ban_until_ms if ban_until_ms > 0 else None,
            "cache_size": len(cls._cache),
        }
