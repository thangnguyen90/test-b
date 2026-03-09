from __future__ import annotations

import re
import threading
import time
from typing import Any

import ccxt
import httpx


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

    @staticmethod
    def _to_binance_symbol(symbol: str) -> str:
        return str(symbol or "").replace(":USDT", "").replace("/", "").upper()

    def _fetch_public_json(
        self,
        *,
        path: str,
        params: dict[str, Any],
        cache_key: str,
        ttl_sec: float,
    ) -> Any:
        cached = self._cache_get(cache_key, ttl_sec=ttl_sec)
        if cached is not None:
            return cached

        url = f"https://fapi.binance.com{path}"
        try:
            response = httpx.get(url, params=params, timeout=httpx.Timeout(2.5, connect=2.0))
            response.raise_for_status()
            payload = response.json()
            self._cache_set(cache_key, payload)
            return payload
        except Exception:
            stale = self._cache_get(cache_key, ttl_sec=ttl_sec, allow_stale=True)
            if stale is not None:
                return stale
            return [] if path.endswith(("Ratio", "Hist", "fundingRate")) else {}

    def fetch_funding_rate_series(self, symbol: str, limit: int = 120) -> list[dict[str, Any]]:
        symbol_id = self._to_binance_symbol(symbol)
        safe_limit = max(1, min(1000, int(limit)))
        key = f"funding_rate:{symbol_id}:{safe_limit}"
        payload = self._fetch_public_json(
            path="/fapi/v1/fundingRate",
            params={"symbol": symbol_id, "limit": safe_limit},
            cache_key=key,
            ttl_sec=35.0,
        )
        return payload if isinstance(payload, list) else []

    def fetch_global_long_short_ratio(
        self,
        symbol: str,
        period: str = "5m",
        limit: int = 180,
    ) -> list[dict[str, Any]]:
        symbol_id = self._to_binance_symbol(symbol)
        safe_limit = max(1, min(500, int(limit)))
        key = f"long_short_ratio:{symbol_id}:{period}:{safe_limit}"
        payload = self._fetch_public_json(
            path="/futures/data/globalLongShortAccountRatio",
            params={"symbol": symbol_id, "period": period, "limit": safe_limit},
            cache_key=key,
            ttl_sec=20.0,
        )
        return payload if isinstance(payload, list) else []

    def fetch_open_interest_hist(
        self,
        symbol: str,
        period: str = "5m",
        limit: int = 180,
    ) -> list[dict[str, Any]]:
        symbol_id = self._to_binance_symbol(symbol)
        safe_limit = max(1, min(500, int(limit)))
        key = f"open_interest_hist:{symbol_id}:{period}:{safe_limit}"
        payload = self._fetch_public_json(
            path="/futures/data/openInterestHist",
            params={"symbol": symbol_id, "period": period, "limit": safe_limit},
            cache_key=key,
            ttl_sec=20.0,
        )
        return payload if isinstance(payload, list) else []

    @classmethod
    def rest_status(cls) -> dict[str, Any]:
        ban_until_ms = cls._get_ban_until()
        banned = int(time.time() * 1000) < ban_until_ms
        return {
            "rest_banned": banned,
            "ban_until_ms": ban_until_ms if ban_until_ms > 0 else None,
            "cache_size": len(cls._cache),
        }
