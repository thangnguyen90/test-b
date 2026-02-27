from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.services.binance_client import BinanceFuturesClient


def _to_binance_symbol(symbol: str) -> str:
    # BTC/USDT or BTC/USDT:USDT -> BTCUSDT
    return symbol.replace(":USDT", "").replace("/", "").upper()


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


@dataclass
class CacheItem:
    expires_at: float
    payload: Any


class AnalyticsService:
    def __init__(self) -> None:
        self.client = BinanceFuturesClient()
        self.http = httpx.Client(timeout=10.0)
        self.cache: dict[str, CacheItem] = {}

    def _get_cached(self, key: str) -> Any | None:
        item = self.cache.get(key)
        if item is None:
            return None
        if time.time() > item.expires_at:
            return None
        return item.payload

    def _set_cache(self, key: str, payload: Any, ttl_sec: int) -> Any:
        self.cache[key] = CacheItem(expires_at=time.time() + ttl_sec, payload=payload)
        return payload

    def _load_usdt_swap_symbols(self) -> list[str]:
        cached = self._get_cached("symbols")
        if cached is not None:
            return cached

        markets = self.client.load_markets()
        symbols: list[str] = []
        for market in markets.values():
            if not market.get("active", True):
                continue
            if market.get("swap") is not True:
                continue
            if market.get("settle") != "USDT":
                continue
            symbol = market.get("symbol")
            if symbol:
                symbols.append(symbol)

        symbols = sorted(set(symbols))
        return self._set_cache("symbols", symbols, ttl_sec=600)

    def top_volatility(self, days: int, limit: int = 30) -> list[dict[str, Any]]:
        key = f"top_volatility:{days}:{limit}"
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        symbols = self._load_usdt_swap_symbols()
        # Use 24h ticker ranking to reduce heavy scans for multi-day calculations.
        tickers = self.client.fetch_tickers(symbols[:180])

        ranked: list[tuple[str, float]] = []
        for symbol in symbols:
            ticker = tickers.get(symbol) if isinstance(tickers, dict) else None
            if not ticker:
                continue
            base = _safe_float(ticker.get("percentage"))
            if base is None:
                base = abs(_safe_float(ticker.get("change")) or 0.0)
            ranked.append((symbol, abs(base)))

        ranked.sort(key=lambda x: x[1], reverse=True)
        candidates = [sym for sym, _ in ranked[:80]]

        items: list[dict[str, Any]] = []
        lookback = max(2, days + 1)
        for symbol in candidates:
            try:
                klines = self.client.fetch_ohlcv(symbol=symbol, timeframe="1d", limit=lookback)
                if len(klines) < 2:
                    continue
                first = float(klines[0][4])
                last = float(klines[-1][4])
                if first <= 0:
                    continue
                move_pct = ((last - first) / first) * 100
                items.append(
                    {
                        "symbol": symbol,
                        "move_pct": move_pct,
                        "abs_move_pct": abs(move_pct),
                        "from_price": first,
                        "to_price": last,
                        "days": days,
                    }
                )
            except Exception:
                continue

        items.sort(key=lambda row: row["abs_move_pct"], reverse=True)
        return self._set_cache(key, items[:limit], ttl_sec=120)

    def liquidation_overview(self, limit: int = 30) -> list[dict[str, Any]]:
        key = f"liquidation_overview:{limit}"
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        symbols = self._load_usdt_swap_symbols()
        tickers = self.client.fetch_tickers(symbols[:220])

        by_volume: list[tuple[str, float]] = []
        for symbol in symbols:
            ticker = tickers.get(symbol) if isinstance(tickers, dict) else None
            if not ticker:
                continue
            quote_vol = _safe_float(ticker.get("quoteVolume")) or 0.0
            by_volume.append((symbol, quote_vol))

        by_volume.sort(key=lambda x: x[1], reverse=True)
        candidates = [sym for sym, _ in by_volume[:80]]

        rows: list[dict[str, Any]] = []
        for symbol in candidates:
            try:
                symbol_id = _to_binance_symbol(symbol)
                premium = self.http.get(
                    "https://fapi.binance.com/fapi/v1/premiumIndex",
                    params={"symbol": symbol_id},
                ).json()
                oi = self.http.get(
                    "https://fapi.binance.com/fapi/v1/openInterest",
                    params={"symbol": symbol_id},
                ).json()
                ls = self.http.get(
                    "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
                    params={"symbol": symbol_id, "period": "5m", "limit": 1},
                ).json()

                mark_price = _safe_float(premium.get("markPrice"))
                funding_rate = _safe_float(premium.get("lastFundingRate"))
                open_interest_qty = _safe_float(oi.get("openInterest"))
                long_short_ratio = None
                if isinstance(ls, list) and ls:
                    long_short_ratio = _safe_float(ls[0].get("longShortRatio"))

                if mark_price is None or open_interest_qty is None or long_short_ratio is None:
                    continue

                oi_notional = open_interest_qty * mark_price
                # Estimated liquidation zone/value proxy.
                zone_bias = 0.02 if long_short_ratio >= 1 else -0.02
                liq_zone_price = mark_price * (1 + zone_bias)
                liq_zone_value = oi_notional * 0.012 * (1 + abs(long_short_ratio - 1) * 0.35)

                rows.append(
                    {
                        "symbol": symbol,
                        "mark_price": mark_price,
                        "funding_rate": funding_rate,
                        "long_short_ratio": long_short_ratio,
                        "open_interest_notional": oi_notional,
                        "est_liq_zone_price": liq_zone_price,
                        "est_liq_zone_value": liq_zone_value,
                    }
                )
            except Exception:
                continue

        rows.sort(key=lambda row: row["est_liq_zone_value"], reverse=True)
        return self._set_cache(key, rows[:limit], ttl_sec=60)
