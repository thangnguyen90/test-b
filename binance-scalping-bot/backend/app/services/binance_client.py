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
    _provider_order: tuple[str, ...] = ("binanceusdm", "okx", "bybit")
    _provider_labels: dict[str, str] = {
        "binanceusdm": "Binance",
        "okx": "OKX",
        "bybit": "Bybit",
    }
    _provider_ban_until_ms: dict[str, int] = {provider: 0 for provider in _provider_order}
    _cache: dict[str, tuple[float, Any]] = {}
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.exchanges: dict[str, Any] = {}

    def _get_exchange(self, provider: str) -> Any:
        exchange = self.exchanges.get(provider)
        if exchange is not None:
            return exchange
        exchange_cls = getattr(ccxt, provider)
        options: dict[str, Any] = {"enableRateLimit": True}
        if provider == "okx":
            options["options"] = {"defaultType": "swap"}
        elif provider == "bybit":
            options["options"] = {"defaultType": "swap"}
        exchange = exchange_cls(options)
        self.exchanges[provider] = exchange
        return exchange

    @classmethod
    def _set_provider_ban_until(cls, provider: str, ban_until_ms: int) -> None:
        with cls._lock:
            cls._provider_ban_until_ms[provider] = max(cls._provider_ban_until_ms.get(provider, 0), int(ban_until_ms))

    @classmethod
    def _get_provider_ban_until(cls, provider: str) -> int:
        with cls._lock:
            return int(cls._provider_ban_until_ms.get(provider, 0))

    @classmethod
    def _is_provider_banned(cls, provider: str) -> bool:
        return int(time.time() * 1000) < cls._get_provider_ban_until(provider)

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

    @staticmethod
    def _is_rate_limit_error(message: str) -> bool:
        lowered = message.lower()
        return (
            "code\":-1003" in lowered
            or "i'm a teapot" in lowered
            or "too many requests" in lowered
            or "rate limit" in lowered
            or "429" in lowered
        )

    def _handle_upstream_error(self, provider: str, exc: Exception, fallback_ban_sec: int = 120) -> Exception:
        message = str(exc)
        if not self._is_rate_limit_error(message):
            return exc
        ban_until = self._extract_ban_until_ms(message)
        if ban_until is None:
            ban_until = int((time.time() + fallback_ban_sec) * 1000)
        self._set_provider_ban_until(provider, ban_until)
        return BinanceRateLimitBanError(
            f"{self._provider_labels.get(provider, provider)} REST is temporarily banned: {message}",
            ban_until_ms=ban_until,
        )

    def _call_with_fallback(
        self,
        *,
        key: str,
        ttl_sec: float,
        operation: str,
        fetcher,
        fallback_ban_sec: int = 120,
    ) -> Any:
        cached = self._cache_get(key, ttl_sec=ttl_sec)
        if cached is not None:
            return cached

        stale = self._cache_get(key, ttl_sec=ttl_sec, allow_stale=True)
        errors: list[str] = []

        for provider in self._provider_order:
            if self._is_provider_banned(provider):
                errors.append(
                    f"{provider}:cooldown_until={self._get_provider_ban_until(provider)}"
                )
                continue

            exchange = self._get_exchange(provider)
            try:
                payload = fetcher(exchange, provider)
                self._cache_set(key, payload)
                return payload
            except Exception as exc:
                handled = self._handle_upstream_error(provider, exc, fallback_ban_sec=fallback_ban_sec)
                errors.append(f"{provider}:{handled}")
                continue

        if stale is not None:
            return stale
        joined = "; ".join(errors) if errors else "no providers available"
        raise RuntimeError(f"All market providers failed for {operation}: {joined}")

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 1000,
        since: int | None = None,
    ) -> list[list[Any]]:
        key = f"ohlcv:{symbol}:{timeframe}:{limit}:{since or 'latest'}"
        ttl = 10.0 if timeframe in {"1m", "3m", "5m"} else 30.0
        return self._call_with_fallback(
            key=key,
            ttl_sec=ttl,
            operation=f"fetch_ohlcv({symbol},{timeframe})",
            fetcher=lambda exchange, _provider: exchange.fetch_ohlcv(
                symbol=symbol,
                timeframe=timeframe,
                since=since,
                limit=limit,
            ),
            fallback_ban_sec=90,
        )

    def load_markets(self) -> dict[str, Any]:
        key = "markets:all"
        ttl = 600.0
        return self._call_with_fallback(
            key=key,
            ttl_sec=ttl,
            operation="load_markets",
            fetcher=lambda exchange, _provider: exchange.load_markets(),
            fallback_ban_sec=180,
        )

    def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        key = f"ticker:{symbol}"
        ttl = 2.0
        return self._call_with_fallback(
            key=key,
            ttl_sec=ttl,
            operation=f"fetch_ticker({symbol})",
            fetcher=lambda exchange, _provider: exchange.fetch_ticker(symbol=symbol),
            fallback_ban_sec=60,
        )

    def fetch_tickers(self, symbols: list[str] | None = None) -> dict[str, Any]:
        key = f"tickers:{','.join(sorted(symbols))}" if symbols else "tickers:all"
        ttl = 8.0
        return self._call_with_fallback(
            key=key,
            ttl_sec=ttl,
            operation=f"fetch_tickers(count={0 if symbols is None else len(symbols)})",
            fetcher=lambda exchange, _provider: exchange.fetch_tickers(symbols=symbols) if symbols else exchange.fetch_tickers(),
            fallback_ban_sec=90,
        )

    @classmethod
    def rest_status(cls) -> dict[str, Any]:
        provider_status = {}
        now_ms = int(time.time() * 1000)
        for provider in cls._provider_order:
            ban_until_ms = cls._get_provider_ban_until(provider)
            provider_status[provider] = {
                "label": cls._provider_labels.get(provider, provider),
                "rest_banned": now_ms < ban_until_ms,
                "ban_until_ms": ban_until_ms if ban_until_ms > 0 else None,
            }
        return {
            "rest_banned": any(item["rest_banned"] for item in provider_status.values()),
            "providers": provider_status,
            "provider_order": list(cls._provider_order),
            "cache_size": len(cls._cache),
        }
