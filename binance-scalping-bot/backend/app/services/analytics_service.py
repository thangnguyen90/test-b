from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.services.liquidation_ml_predictor import LiquidationMLPredictor
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
    def __init__(
        self,
        client: BinanceFuturesClient | None = None,
        liquid_predictor: LiquidationMLPredictor | None = None,
    ) -> None:
        self.client = client or BinanceFuturesClient()
        self.liquid_predictor = liquid_predictor or LiquidationMLPredictor(
            model_path=settings.liquid_ml_model_path,
            touch_tolerance_pct=settings.liquid_ml_touch_tolerance_pct,
            short_zone_min_score=settings.liquid_ml_short_zone_min_score,
            short_zone_touch_multiplier=settings.liquid_ml_short_zone_touch_multiplier,
            rr_ratio=settings.liquid_ml_train_rr_ratio,
        )
        self.http = httpx.Client(timeout=httpx.Timeout(2.5, connect=2.0))
        self.cache: dict[str, CacheItem] = {}

    @staticmethod
    def _signal_order_type(side: str, mark_price: float, entry_price: float) -> str:
        if mark_price <= 0 or entry_price <= 0:
            return "LIMIT"
        dist_pct = abs(entry_price - mark_price) / mark_price
        # Near current mark -> market execution, otherwise treat as limit setup.
        if dist_pct <= 0.0012:
            return "MARKET"
        return "LIMIT"

    def _get_cached(self, key: str, allow_stale: bool = False) -> Any | None:
        item = self.cache.get(key)
        if item is None:
            return None
        if not allow_stale and time.time() > item.expires_at:
            return None
        return item.payload

    def _set_cache(self, key: str, payload: Any, ttl_sec: int) -> Any:
        self.cache[key] = CacheItem(expires_at=time.time() + ttl_sec, payload=payload)
        return payload

    def _fetch_tickers_chunked(self, symbols: list[str], chunk_size: int = 120) -> dict[str, Any]:
        if not symbols:
            return {}
        out: dict[str, Any] = {}
        for i in range(0, len(symbols), chunk_size):
            chunk = symbols[i : i + chunk_size]
            try:
                payload = self.client.fetch_tickers(chunk)
                if isinstance(payload, dict):
                    out.update(payload)
            except Exception:
                continue
        return out

    def _resolve_liq_signal(self, symbol: str, mark_price: float) -> dict[str, Any] | None:
        if mark_price <= 0:
            return None
        cache_key = f"liq_signal:{symbol}"
        cached = self._get_cached(cache_key)
        if isinstance(cached, dict):
            cached_mark = _safe_float(cached.get("mark_price")) or 0.0
            # Refresh only if mark moved enough; otherwise reuse cached LIQ signal.
            if cached_mark > 0 and abs(mark_price - cached_mark) / cached_mark <= 0.002:
                return cached

        signal = self.liquid_predictor.predict(symbol=symbol, mark_price=mark_price)
        payload = {
            "symbol": symbol,
            "side": signal.side,
            "source": "LIQ_LS_FUND",
            "win_probability": signal.win_probability,
            "entry_price": signal.predicted_entry_price,
            "take_profit": signal.take_profit,
            "stop_loss": signal.stop_loss,
            "mark_price": mark_price,
        }
        self._set_cache(cache_key, payload, ttl_sec=120)
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

    @staticmethod
    def _funding_cycle_label(minutes_to_funding: float | None) -> str:
        if minutes_to_funding is None or minutes_to_funding < 0:
            return "UNKNOWN"
        if minutes_to_funding <= 60:
            return "PRE_1H"
        if minutes_to_funding <= 180:
            return "PRE_3H"
        if minutes_to_funding <= 480:
            return "MID_CYCLE"
        return "FAR"

    @staticmethod
    def _estimate_funding_interval_hours(rows: list[dict[str, Any]]) -> float | None:
        if not rows:
            return None
        points: list[int] = []
        for item in rows:
            try:
                ts = int(item.get("fundingTime") or item.get("time") or 0)
            except Exception:
                ts = 0
            if ts > 0:
                points.append(ts)
        if len(points) < 2:
            return None
        points = sorted(set(points))
        if len(points) < 2:
            return None
        diff_ms = points[-1] - points[-2]
        if diff_ms <= 0:
            return None
        hours = diff_ms / 3_600_000
        if hours <= 0:
            return None
        return float(round(hours, 2))

    def _funding_retrace_entry(
        self,
        *,
        side: str,
        base_entry: float,
        mark_price: float,
        abs_move_pct: float,
        funding_rate: float,
        minutes_to_funding: float | None,
    ) -> tuple[float, float, str]:
        if base_entry <= 0 or mark_price <= 0:
            return base_entry, 0.0, "MODEL_BASE"
        side_key = str(side or "").upper()
        if side_key not in {"LONG", "SHORT"}:
            return base_entry, 0.0, "MODEL_BASE"

        near_funding = minutes_to_funding is not None and minutes_to_funding <= 180
        funding_hot = abs(float(funding_rate)) >= 0.00035
        volatile = abs_move_pct >= 6.0
        if not (near_funding or funding_hot or volatile):
            dist_pct = abs(base_entry - mark_price) / mark_price * 100.0
            return base_entry, dist_pct, "MODEL_BASE"

        move_component = self._clamp((abs_move_pct / 100.0) * 0.14, 0.003, 0.026)
        funding_component = self._clamp(abs(float(funding_rate)) / 0.0012, 0.0, 1.8) * 0.0022
        timing_component = 0.0
        if minutes_to_funding is not None:
            if minutes_to_funding <= 60:
                timing_component = 0.004
            elif minutes_to_funding <= 180:
                timing_component = 0.002
        retrace_pct = self._clamp(0.003 + move_component + funding_component + timing_component, 0.004, 0.04)

        if side_key == "LONG":
            suggested_entry = min(float(base_entry), float(mark_price) * (1.0 - retrace_pct))
        else:
            suggested_entry = max(float(base_entry), float(mark_price) * (1.0 + retrace_pct))

        dist_pct = abs(suggested_entry - mark_price) / mark_price * 100.0
        return float(suggested_entry), float(dist_pct), "FUNDING_RETRACE"

    def top_volatility(self, days: int, limit: int = 30) -> list[dict[str, Any]]:
        key = f"top_volatility:{days}:{limit}"
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        try:
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
            top_rows = items[:limit]

            now_ms = int(time.time() * 1000)
            for row in top_rows:
                symbol = str(row.get("symbol") or "")
                if not symbol:
                    continue
                symbol_id = _to_binance_symbol(symbol)
                ticker = tickers.get(symbol) if isinstance(tickers, dict) else None
                ticker_mark = _safe_float(ticker.get("last")) if isinstance(ticker, dict) else None
                if ticker_mark is None and isinstance(ticker, dict):
                    ticker_mark = _safe_float(ticker.get("close"))

                mark_price = float(ticker_mark or row.get("to_price") or 0.0)
                funding_rate = 0.0
                next_funding_time_ms: int | None = None
                minutes_to_funding: float | None = None
                funding_interval_hours: float | None = None
                try:
                    premium = self.http.get(
                        "https://fapi.binance.com/fapi/v1/premiumIndex",
                        params={"symbol": symbol_id},
                    ).json()
                    premium_mark = _safe_float(premium.get("markPrice"))
                    if premium_mark is not None and premium_mark > 0:
                        mark_price = float(premium_mark)
                    funding_rate = float(_safe_float(premium.get("lastFundingRate")) or 0.0)
                    raw_next_funding = premium.get("nextFundingTime")
                    if raw_next_funding is not None:
                        next_funding_time_ms = int(raw_next_funding)
                        if next_funding_time_ms > 0:
                            minutes_to_funding = (next_funding_time_ms - now_ms) / 60000.0
                except Exception:
                    pass

                try:
                    funding_series = self.client.fetch_funding_rate_series(symbol=symbol, limit=4)
                    funding_interval_hours = self._estimate_funding_interval_hours(funding_series)
                except Exception:
                    funding_interval_hours = None

                funding_cycle = self._funding_cycle_label(minutes_to_funding)

                signal_side: str | None = None
                signal_win_probability: float | None = None
                signal_entry_price: float | None = None
                signal_take_profit: float | None = None
                signal_stop_loss: float | None = None
                suggested_entry_price: float | None = None
                suggested_take_profit: float | None = None
                suggested_stop_loss: float | None = None
                entry_dist_pct: float | None = None
                entry_strategy = "MODEL_BASE"
                signal_order_type: str | None = None
                if mark_price > 0:
                    try:
                        signal = self._resolve_liq_signal(symbol=symbol, mark_price=mark_price)
                        if signal is not None:
                            signal_side = str(signal.get("side") or "").upper() or None
                            signal_win_probability = _safe_float(signal.get("win_probability"))
                            signal_entry_price = _safe_float(signal.get("entry_price"))
                            signal_take_profit = _safe_float(signal.get("take_profit"))
                            signal_stop_loss = _safe_float(signal.get("stop_loss"))
                            if signal_side in {"LONG", "SHORT"} and signal_entry_price and signal_entry_price > 0:
                                suggested_entry_price, entry_dist_pct, entry_strategy = self._funding_retrace_entry(
                                    side=signal_side,
                                    base_entry=float(signal_entry_price),
                                    mark_price=float(mark_price),
                                    abs_move_pct=float(row.get("abs_move_pct") or 0.0),
                                    funding_rate=float(funding_rate),
                                    minutes_to_funding=minutes_to_funding,
                                )
                                tp_dist = abs(float((signal_take_profit or signal_entry_price)) - float(signal_entry_price))
                                sl_dist = abs(float(signal_entry_price) - float((signal_stop_loss or signal_entry_price)))
                                if signal_side == "LONG":
                                    suggested_take_profit = suggested_entry_price + tp_dist
                                    suggested_stop_loss = max(1e-9, suggested_entry_price - sl_dist)
                                else:
                                    suggested_take_profit = max(1e-9, suggested_entry_price - tp_dist)
                                    suggested_stop_loss = suggested_entry_price + sl_dist
                                signal_order_type = self._signal_order_type(
                                    side=signal_side,
                                    mark_price=float(mark_price),
                                    entry_price=float(suggested_entry_price),
                                )
                    except Exception:
                        pass

                row.update(
                    {
                        "mark_price": mark_price,
                        "funding_rate": float(funding_rate),
                        "funding_interval_hours": funding_interval_hours,
                        "next_funding_time_ms": next_funding_time_ms,
                        "minutes_to_funding": minutes_to_funding,
                        "funding_cycle": funding_cycle,
                        "signal_side": signal_side,
                        "signal_win_probability": signal_win_probability,
                        "signal_entry_price": signal_entry_price,
                        "signal_take_profit": signal_take_profit,
                        "signal_stop_loss": signal_stop_loss,
                        "suggested_entry_price": suggested_entry_price,
                        "suggested_take_profit": suggested_take_profit,
                        "suggested_stop_loss": suggested_stop_loss,
                        "entry_dist_pct": entry_dist_pct,
                        "entry_strategy": entry_strategy,
                        "signal_order_type": signal_order_type,
                    }
                )

            return self._set_cache(key, top_rows, ttl_sec=120)
        except Exception:
            stale = self._get_cached(key, allow_stale=True)
            if stale is not None:
                return stale
            return []

    def liquidation_overview(
        self,
        page: int = 1,
        page_size: int = 30,
        full_symbols: bool = True,
    ) -> dict[str, Any]:
        safe_page = max(1, page)
        safe_page_size = max(10, min(page_size, 100))
        key = f"liquidation_overview:{safe_page}:{safe_page_size}:{1 if full_symbols else 0}"
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        try:
            symbols = self._load_usdt_swap_symbols()
            # Use the all-tickers endpoint once to avoid many chunked REST calls.
            try:
                tickers = self.client.fetch_tickers()
            except Exception:
                tickers = self._fetch_tickers_chunked(symbols, chunk_size=120)

            by_volume: list[tuple[str, float]] = []
            for symbol in symbols:
                ticker = tickers.get(symbol) if isinstance(tickers, dict) else None
                if not ticker:
                    continue
                quote_vol = _safe_float(ticker.get("quoteVolume")) or 0.0
                by_volume.append((symbol, quote_vol))

            by_volume.sort(key=lambda x: x[1], reverse=True)
            ranked = [sym for sym, _ in by_volume]
            if not full_symbols:
                ranked = ranked[:80]
            total_symbols = len(ranked)
            start = (safe_page - 1) * safe_page_size
            end = start + safe_page_size
            candidates = ranked[start:end]

            rows: list[dict[str, Any]] = []
            for symbol in candidates:
                try:
                    symbol_id = _to_binance_symbol(symbol)
                    premium = self.http.get(
                        "https://fapi.binance.com/fapi/v1/premiumIndex",
                        params={"symbol": symbol_id},
                    ).json()

                    mark_price = _safe_float(premium.get("markPrice"))
                    funding_rate = _safe_float(premium.get("lastFundingRate"))
                    ticker = tickers.get(symbol) if isinstance(tickers, dict) else None
                    quote_vol = _safe_float(ticker.get("quoteVolume")) if isinstance(ticker, dict) else None

                    open_interest_qty: float | None = None
                    long_short_ratio: float | None = None
                    try:
                        oi = self.http.get(
                            "https://fapi.binance.com/fapi/v1/openInterest",
                            params={"symbol": symbol_id},
                        ).json()
                        open_interest_qty = _safe_float(oi.get("openInterest"))
                    except Exception:
                        open_interest_qty = None
                    try:
                        ls = self.http.get(
                            "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
                            params={"symbol": symbol_id, "period": "5m", "limit": 1},
                        ).json()
                        if isinstance(ls, list) and ls:
                            long_short_ratio = _safe_float(ls[0].get("longShortRatio"))
                    except Exception:
                        long_short_ratio = None

                    if mark_price is None:
                        continue
                    if long_short_ratio is None:
                        long_short_ratio = 1.0
                    if open_interest_qty is None:
                        est_notional = max(0.0, float(quote_vol or 0.0) * 0.22)
                        open_interest_qty = (est_notional / mark_price) if mark_price > 0 else 0.0

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
            top_rows = rows

            for row in top_rows:
                try:
                    symbol = str(row["symbol"])
                    mark = float(row["mark_price"])
                    liq_signal = self._resolve_liq_signal(symbol=symbol, mark_price=mark)
                    if liq_signal is not None:
                        row["signal_side"] = liq_signal["side"]
                        row["signal_source"] = liq_signal["source"]
                        row["signal_win_probability"] = liq_signal["win_probability"]
                        row["signal_entry_price"] = liq_signal["entry_price"]
                        row["signal_take_profit"] = liq_signal["take_profit"]
                        row["signal_stop_loss"] = liq_signal["stop_loss"]
                    else:
                        signal = self.liquid_predictor.predict(
                            symbol=symbol,
                            mark_price=mark,
                        )
                        row["signal_side"] = signal.side
                        row["signal_source"] = "LIQ_LS_FUND"
                        row["signal_win_probability"] = signal.win_probability
                        row["signal_entry_price"] = signal.predicted_entry_price
                        row["signal_take_profit"] = signal.take_profit
                        row["signal_stop_loss"] = signal.stop_loss
                    row["signal_order_type"] = self._signal_order_type(
                        side=str(row["signal_side"]),
                        mark_price=float(row["mark_price"]),
                        entry_price=float(row["signal_entry_price"]),
                    )
                except Exception:
                    row["signal_side"] = None
                    row["signal_source"] = None
                    row["signal_win_probability"] = None
                    row["signal_entry_price"] = None
                    row["signal_take_profit"] = None
                    row["signal_stop_loss"] = None
                    row["signal_order_type"] = None

            payload = {
                "page": safe_page,
                "page_size": safe_page_size,
                "total_symbols": total_symbols,
                "count": len(top_rows),
                "items": top_rows,
            }
            return self._set_cache(key, payload, ttl_sec=60)
        except Exception:
            stale = self._get_cached(key, allow_stale=True)
            if stale is not None:
                return stale
            return {
                "page": safe_page,
                "page_size": safe_page_size,
                "total_symbols": 0,
                "count": 0,
                "items": [],
            }

    def funding_arbitrage_setups(
        self,
        min_rate: float,
        max_minutes: int,
    ) -> list[dict[str, Any]]:
        key = f"funding_arbitrage:{min_rate}:{max_minutes}"
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        try:
            symbols = self._load_usdt_swap_symbols()
            now_ms = int(time.time() * 1000)
            
            try:
                tickers = self.client.fetch_tickers()
            except Exception:
                tickers = self._fetch_tickers_chunked(symbols, chunk_size=120)

            # Get premium index data for funding rates and times
            premium_data = self.http.get("https://fapi.binance.com/fapi/v1/premiumIndex").json()
            if not isinstance(premium_data, list):
                return []

            premium_map = {item.get("symbol"): item for item in premium_data}
            
            setups: list[dict[str, Any]] = []
            for symbol in symbols:
                symbol_id = _to_binance_symbol(symbol)
                premium = premium_map.get(symbol_id)
                if not premium:
                    continue

                funding_rate = _safe_float(premium.get("lastFundingRate")) or 0.0
                if abs(funding_rate) < min_rate:
                    continue

                raw_next_funding = premium.get("nextFundingTime")
                if not raw_next_funding:
                    continue
                
                next_funding_time_ms = int(raw_next_funding)
                if next_funding_time_ms <= 0:
                    continue

                minutes_to_funding = (next_funding_time_ms - now_ms) / 60000.0
                if minutes_to_funding < 0 or minutes_to_funding > max_minutes:
                    continue

                mark_price = _safe_float(premium.get("markPrice"))
                if not mark_price or mark_price <= 0:
                    ticker = tickers.get(symbol) if isinstance(tickers, dict) else None
                    mark_price = _safe_float(ticker.get("last")) if isinstance(ticker, dict) else None
                    if not mark_price:
                        continue
                
                side = "SHORT" if funding_rate > 0 else "LONG"

                setups.append(
                    {
                        "symbol": symbol,
                        "side": side,
                        "funding_rate": funding_rate,
                        "minutes_to_funding": float(minutes_to_funding),
                        "mark_price": float(mark_price),
                    }
                )

            # Sort by highest absolute funding rate
            setups.sort(key=lambda x: abs(float(x["funding_rate"])), reverse=True)
            return self._set_cache(key, setups, ttl_sec=30)
            
        except Exception:
            return []

    @staticmethod
    def _ema(values: list[float], period: int) -> float:
        if not values:
            return 0.0
        alpha = 2 / (period + 1)
        ema = float(values[0])
        for value in values[1:]:
            ema = (float(value) * alpha) + (ema * (1 - alpha))
        return ema

    @staticmethod
    def _rsi(values: list[float], period: int = 14) -> float:
        if len(values) < period + 1:
            return 50.0
        gains: list[float] = []
        losses: list[float] = []
        for i in range(1, len(values)):
            delta = float(values[i]) - float(values[i - 1])
            if delta >= 0:
                gains.append(delta)
                losses.append(0.0)
            else:
                gains.append(0.0)
                losses.append(abs(delta))
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    def _trend_from_ohlcv(self, rows: list[list[float]]) -> tuple[float, dict[str, float]]:
        closes = [float(row[4]) for row in rows if len(row) >= 5]
        if len(closes) < 60:
            return 0.0, {"rsi": 50.0, "ema_fast": 0.0, "ema_slow": 0.0, "slope_pct": 0.0}

        ema_fast = self._ema(closes[-90:], 21)
        ema_slow = self._ema(closes[-160:], 55)
        rsi = self._rsi(closes, 14)

        tail = closes[-6:]
        slope_pct = ((tail[-1] - tail[0]) / max(1e-12, tail[0])) * 100
        cross_component = self._clamp((ema_fast - ema_slow) / max(1e-12, ema_slow) * 22, -1.0, 1.0)
        slope_component = self._clamp(slope_pct / 1.2, -1.0, 1.0)
        rsi_component = self._clamp((rsi - 50.0) / 20.0, -1.0, 1.0)
        last = closes[-1]
        low20 = min(closes[-20:])
        high20 = max(closes[-20:])
        pos = (last - low20) / max(1e-12, (high20 - low20))
        breakout_component = self._clamp((pos - 0.5) * 2.0, -1.0, 1.0)

        technical_score = (
            cross_component * 0.42
            + slope_component * 0.24
            + rsi_component * 0.18
            + breakout_component * 0.16
        )
        return technical_score, {
            "rsi": rsi,
            "ema_fast": ema_fast,
            "ema_slow": ema_slow,
            "slope_pct": slope_pct,
        }

    def btc_trend_forecast(self) -> dict[str, Any]:
        key = "btc_trend_forecast"
        cached = self._get_cached(key)
        if cached is not None:
            return cached

        symbol = "BTC/USDT"
        try:
            ticker = self.client.fetch_ticker(symbol=symbol)
            mark_price = _safe_float(ticker.get("last")) or _safe_float(ticker.get("close")) or 0.0

            ml_signal = self.liquid_predictor.predict(symbol=symbol, mark_price=float(mark_price))
            ml_direction = 1.0 if ml_signal.side == "LONG" else -1.0
            ml_bias = ml_direction * ((ml_signal.win_probability * 2.0) - 1.0)

            frames = [
                ("15m", 220, 0.55),
                ("1h", 220, 0.60),
                ("4h", 220, 0.68),
                ("1d", 260, 0.76),
            ]
            items: list[dict[str, Any]] = []
            for tf, limit, tech_weight in frames:
                rows = self.client.fetch_ohlcv(symbol=symbol, timeframe=tf, limit=limit)
                tech_score, details = self._trend_from_ohlcv(rows)
                ml_weight = 1.0 - tech_weight
                blended = (tech_score * tech_weight) + (ml_bias * ml_weight)
                prob_up = self._clamp(0.5 + (blended * 0.5), 0.01, 0.99)
                confidence = self._clamp(0.45 + (abs(blended) * 0.5), 0.45, 0.98)

                if blended > 0.08:
                    trend = "BULLISH"
                    action = "LONG"
                elif blended < -0.08:
                    trend = "BEARISH"
                    action = "SHORT"
                else:
                    trend = "SIDEWAYS"
                    action = "WAIT"

                items.append(
                    {
                        "timeframe": tf,
                        "trend": trend,
                        "action": action,
                        "confidence": confidence,
                        "prob_up": prob_up,
                        "prob_down": 1.0 - prob_up,
                        "technical_score": tech_score,
                        "ml_score": ml_bias,
                        "blended_score": blended,
                        "rsi": details["rsi"],
                        "slope_pct": details["slope_pct"],
                    }
                )

            payload = {
                "symbol": symbol,
                "mark_price": mark_price,
                "ml_side": ml_signal.side,
                "ml_win_probability": ml_signal.win_probability,
                "items": items,
                "status": "live",
            }
            return self._set_cache(key, payload, ttl_sec=20)
        except Exception as exc:
            stale = self._get_cached(key, allow_stale=True)
            if stale is not None:
                return {
                    **stale,
                    "status": "degraded",
                    "error": str(exc),
                }
            return {
                "symbol": symbol,
                "mark_price": 0.0,
                "ml_side": "LONG",
                "ml_win_probability": 0.5,
                "items": [],
                "status": "error",
                "error": str(exc),
            }
