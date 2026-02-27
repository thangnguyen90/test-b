from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings
from app.services.ml_predictor import MLPredictor
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
        self.predictor = MLPredictor(model_path=settings.ml_model_path)
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
                signal = self.predictor.predict(
                    symbol=str(row["symbol"]),
                    mark_price=float(row["mark_price"]),
                )
                row["signal_side"] = signal.side
                row["signal_win_probability"] = signal.win_probability
                row["signal_entry_price"] = signal.predicted_entry_price
                row["signal_take_profit"] = signal.take_profit
                row["signal_stop_loss"] = signal.stop_loss
                row["signal_order_type"] = self._signal_order_type(
                    side=signal.side,
                    mark_price=float(row["mark_price"]),
                    entry_price=signal.predicted_entry_price,
                )
            except Exception:
                row["signal_side"] = None
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
        ticker = self.client.fetch_ticker(symbol=symbol)
        mark_price = _safe_float(ticker.get("last")) or _safe_float(ticker.get("close")) or 0.0

        ml_signal = self.predictor.predict(symbol=symbol, mark_price=float(mark_price))
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
        }
        return self._set_cache(key, payload, ttl_sec=20)
