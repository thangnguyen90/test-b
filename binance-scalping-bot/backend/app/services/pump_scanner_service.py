from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from app.core.config import settings
from app.services.binance_client import BinanceFuturesClient

logger = logging.getLogger(__name__)

PUMP_HUNTER_HIGHLIGHT_TP_PCT = 10.0


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _norm(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return _clamp((value - low) / (high - low), 0.0, 1.0)


@dataclass
class CacheItem:
    expires_at: float
    payload: Any


class PumpScannerService:
    def __init__(self, client: BinanceFuturesClient | None = None) -> None:
        self.client = client or BinanceFuturesClient()
        self.cache: dict[str, CacheItem] = {}

    @staticmethod
    def _derive_trade_plan(row: dict[str, Any]) -> tuple[str, float, float, float]:
        signal_label = str(row.get("signal_label") or "").upper()
        stage = str(row.get("stage") or "").upper()
        is_post_sweep = signal_label in {"ENTER", "SWEEPED"} or stage in {"POST_SWEEP", "SWEEPED"}
        side = "SHORT" if is_post_sweep else "LONG"
        entry = float(row.get("mark_price") or 0.0)
        take_profit = float(
            (row.get("invalidation_price") if side == "SHORT" else row.get("est_liq_target_price")) or 0.0
        )
        stop_loss = float(
            (row.get("est_liq_target_high") if side == "SHORT" else row.get("invalidation_price")) or 0.0
        )
        return side, entry, take_profit, stop_loss

    @staticmethod
    def _post_webhook(request: Request) -> None:
        with urlopen(request, timeout=10) as response:
            response.read()

    @staticmethod
    def _format_number(value: float, digits: int = 6) -> str:
        text = f"{float(value):.{digits}f}"
        return text.rstrip("0").rstrip(".") if "." in text else text

    @staticmethod
    def _format_signed_pct(value: float) -> str:
        return f"{float(value):+,.2f}%"

    def _maybe_send_discord_alert(self, row: dict[str, Any]) -> None:
        if not settings.pump_hunter_discord_alert_enabled:
            return
        webhook_url = str(settings.pump_hunter_discord_webhook_url or "").strip()
        if not webhook_url:
            return
        score = float(row.get("effective_score") or row.get("pump_score") or 0.0)
        if score < float(settings.pump_hunter_discord_min_score):
            return
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            return
        signal_label = str(row.get("signal_label") or "WATCH").upper()
        alert_key = f"pump_discord:{symbol}:{signal_label}"
        if self._get_cached(alert_key) is not None:
            return
        side, entry, take_profit, stop_loss = self._derive_trade_plan(row)
        notes = row.get("notes") or []
        notes_text = " | ".join(str(item) for item in notes[:3]) if isinstance(notes, list) and notes else "-"
        leverage = 5
        margin_usdt = 20.0
        tp_pct = 0.0
        sl_pct = 0.0
        if entry > 0:
            if side == "LONG":
                tp_pct = ((take_profit - entry) / entry) * leverage * 100.0
                sl_pct = ((stop_loss - entry) / entry) * leverage * 100.0
            else:
                tp_pct = ((entry - take_profit) / entry) * leverage * 100.0
                sl_pct = ((entry - stop_loss) / entry) * leverage * 100.0
        is_high_tp = tp_pct >= PUMP_HUNTER_HIGHLIGHT_TP_PCT
        title_prefix = "HIGH_TP_10P " if is_high_tp else ""
        embed_color = 0xF1C40F if is_high_tp else (0xED4245 if side == "SHORT" else 0x57F287)
        fields: list[dict[str, Any]] = []
        if is_high_tp:
            fields.append(
                {
                    "name": "HIGH TP ALERT",
                    "value": (
                        f"Symbol {symbol}\n"
                        f"Estimated TP {self._format_signed_pct(tp_pct)}"
                    ),
                    "inline": False,
                }
            )
        embed = {
            "title": f"{title_prefix}PUMP_HUNTER {side} setup: {symbol}",
            "color": embed_color,
            "fields": fields + [
                {"name": "Signal", "value": f"{signal_label}/{str(row.get('stage') or '-').upper()}", "inline": True},
                {"name": "Side", "value": side, "inline": True},
                {"name": "Score", "value": f"{score:.1f}", "inline": True},
                {"name": "Entry", "value": self._format_number(entry, 8), "inline": True},
                {"name": "TP", "value": f"{self._format_number(take_profit, 8)} ({self._format_signed_pct(tp_pct)})", "inline": True},
                {"name": "SL", "value": f"{self._format_number(stop_loss, 8)} ({self._format_signed_pct(sl_pct)})", "inline": True},
                {"name": "Leverage", "value": f"{leverage}x", "inline": True},
                {"name": "Margin", "value": f"{margin_usdt:.2f}", "inline": True},
                {"name": "Market", "value": f"mark {self._format_number(float(row.get('mark_price') or 0.0), 8)} | RR {float(row.get('rr_ratio') or 0.0):.2f}", "inline": True},
                {
                    "name": "Pattern",
                    "value": (
                        f"Vol15m x{float(row.get('volume_ratio_15m') or 0.0):.2f}"
                        f" | Rejection {float(row.get('rejection_score') or 0.0):.1f}"
                        f" | Dist {self._format_signed_pct(float(row.get('est_liq_distance_pct') or 0.0))}"
                    ),
                    "inline": False,
                },
                {"name": "Notes", "value": notes_text, "inline": False},
            ],
        }
        payload = json.dumps(
            {
                "username": "Pump Hunter Bot",
                "allowed_mentions": {"parse": []},
                "content": (
                    f"HIGH TP >= 10% | {symbol} | {side} | TP {self._format_signed_pct(tp_pct)}"
                    if is_high_tp
                    else None
                ),
                "embeds": [embed],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            webhook_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "curl/8.7.1",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            self._post_webhook(request)
            self._set_cache(alert_key, True, ttl_sec=max(60, int(settings.pump_hunter_discord_alert_cooldown_sec)))
            logger.info(
                "Pump hunter Discord alert sent: symbol=%s signal=%s stage=%s score=%.1f",
                symbol,
                signal_label,
                str(row.get("stage") or "-").upper(),
                score,
            )
        except Exception:
            logger.exception(
                "Pump hunter Discord alert failed: symbol=%s signal=%s stage=%s score=%.1f",
                symbol,
                signal_label,
                str(row.get("stage") or "-").upper(),
                score,
            )

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

    @staticmethod
    def _to_frame(rows: list[list[float]]) -> pd.DataFrame:
        frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
        for column in ["open", "high", "low", "close", "volume"]:
            frame[column] = frame[column].astype(float)
        return frame.sort_values("timestamp").reset_index(drop=True)

    @staticmethod
    def _atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
        prev_close = frame["close"].shift(1)
        tr = pd.concat(
            [
                frame["high"] - frame["low"],
                (frame["high"] - prev_close).abs(),
                (frame["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    def _prepare_frame(self, symbol: str, timeframe: str, limit: int) -> pd.DataFrame:
        rows = self.client.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)
        frame = self._to_frame(rows)
        frame["ema13"] = frame["close"].ewm(span=13, adjust=False).mean()
        frame["ema25"] = frame["close"].ewm(span=25, adjust=False).mean()
        frame["ema99"] = frame["close"].ewm(span=99, adjust=False).mean()
        frame["atr14"] = self._atr(frame, 14)

        close_safe = frame["close"].replace(0, np.nan)
        open_safe = frame["open"].replace(0, np.nan)
        range_raw = frame["high"] - frame["low"]
        range_safe = range_raw.replace(0, np.nan)

        frame["range_pct"] = (range_raw / close_safe).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        frame["body_pct"] = ((frame["close"] - frame["open"]).abs() / open_safe).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        frame["body_dir"] = np.sign(frame["close"] - frame["open"])
        frame["upper_wick_pct"] = ((frame["high"] - np.maximum(frame["open"], frame["close"])) / close_safe).clip(lower=0).fillna(0.0)
        frame["lower_wick_pct"] = ((np.minimum(frame["open"], frame["close"]) - frame["low"]) / close_safe).clip(lower=0).fillna(0.0)
        frame["close_in_range"] = ((frame["close"] - frame["low"]) / range_safe).replace([np.inf, -np.inf], np.nan).fillna(0.5)
        frame["ret_1"] = frame["close"].pct_change(1).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        frame["ret_3"] = frame["close"].pct_change(3).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        frame["ret_12"] = frame["close"].pct_change(12).replace([np.inf, -np.inf], np.nan).fillna(0.0)

        frame["vol_ma20"] = frame["volume"].rolling(20, min_periods=5).mean()
        frame["vol_std20"] = frame["volume"].rolling(20, min_periods=5).std(ddof=0).replace(0, np.nan)
        frame["volume_ratio"] = (frame["volume"] / frame["vol_ma20"].replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        frame["volume_z"] = ((frame["volume"] - frame["vol_ma20"]) / frame["vol_std20"]).replace([np.inf, -np.inf], np.nan).fillna(0.0)

        frame["atr_pct"] = (frame["atr14"] / close_safe).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        frame["atr_slow"] = frame["atr14"].rolling(34, min_periods=10).mean().replace(0, np.nan)
        frame["expansion_ratio"] = (frame["atr14"] / frame["atr_slow"]).replace([np.inf, -np.inf], np.nan).fillna(1.0)

        frame["ema_stack_gap_pct"] = ((frame["ema13"] - frame["ema99"]) / close_safe).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        frame["ema13_slope_pct"] = frame["ema13"].pct_change(3).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        frame["ema25_slope_pct"] = frame["ema25"].pct_change(5).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        frame["above_ema_stack"] = (
            (frame["close"] > frame["ema13"])
            & (frame["ema13"] > frame["ema25"])
            & (frame["ema25"] > frame["ema99"])
        )

        frame["prev_high_20"] = frame["high"].rolling(20, min_periods=5).max().shift(1)
        frame["prev_high_55"] = frame["high"].rolling(55, min_periods=10).max().shift(1)
        frame["prev_low_20"] = frame["low"].rolling(20, min_periods=5).min().shift(1)
        frame["breakout_pct_20"] = ((frame["close"] - frame["prev_high_20"]) / frame["prev_high_20"]).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        frame["breakout_pct_55"] = ((frame["close"] - frame["prev_high_55"]) / frame["prev_high_55"]).replace([np.inf, -np.inf], np.nan).fillna(0.0)

        return frame

    @staticmethod
    def _estimate_target_zone(frame: pd.DataFrame, current_price: float, atr: float) -> tuple[float, float, float, float]:
        if frame.empty or current_price <= 0:
            return 0.0, 0.0, 0.0, 0.0

        view = frame.tail(72).copy()
        if view.empty:
            return 0.0, 0.0, 0.0, 0.0

        view["distance_pct"] = ((view["high"] - current_price) / current_price).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        view = view[view["distance_pct"] >= 0.003].copy()

        if not view.empty:
            rolling_high = view["high"].rolling(5, center=True, min_periods=1).max()
            view["is_local_high"] = view["high"] >= (rolling_high - 1e-12)
            view["recency_rank"] = np.linspace(0.72, 1.0, len(view))
            view["target_score"] = (
                (view["upper_wick_pct"].clip(lower=0.0) * 2.6)
                + (view["volume_z"].clip(lower=0.0) * 0.75)
                + (view["body_pct"].clip(lower=0.0) * 0.45)
                + (view["is_local_high"].astype(float) * 0.55)
            ) * view["recency_rank"] / (1.0 + (view["distance_pct"] * 14.0))

            best_idx = view["target_score"].idxmax()
            target_price = float(view.loc[best_idx, "high"])
            target_score = float(view.loc[best_idx, "target_score"])
        else:
            extension = max(atr * 1.6, current_price * 0.016)
            target_price = current_price + extension
            target_score = 0.35

        band = max(atr * 0.45, target_price * 0.003)
        zone_low = max(0.0, target_price - band)
        zone_high = max(zone_low, target_price + band)
        return target_price, zone_low, zone_high, target_score

    @staticmethod
    def _classify_stage(breakout_pct_20: float, distance_pct: float, volume_ratio_fast: float) -> str:
        if breakout_pct_20 < 0.0 and volume_ratio_fast >= 1.8:
            return "ARMING"
        if distance_pct <= 1.8:
            return "NEAR_SWEEP"
        if breakout_pct_20 >= 0.0:
            return "BREAKOUT"
        return "EXPANDING"

    @staticmethod
    def _detect_sweep_signal(
        frame: pd.DataFrame,
        *,
        zone_low: float,
        zone_high: float,
        current_price: float,
        atr: float,
    ) -> dict[str, Any]:
        recent = frame.tail(4).copy()
        if recent.empty or zone_low <= 0 or current_price <= 0:
            return {
                "swept_recently": False,
                "entry_ready": False,
                "rejection_score": 0.0,
                "signal_label": "WATCH",
                "signal_bonus": 0.0,
                "sweep_distance_pct": None,
            }

        tol_pct = max(0.0015, min(0.008, ((atr / current_price) * 0.75) if atr > 0 else 0.0025))
        sweep_floor = zone_low * (1.0 - tol_pct)
        zone_mid = zone_low if zone_high <= zone_low else (zone_low + zone_high) / 2.0
        touched = recent[recent["high"] >= sweep_floor]
        if touched.empty:
            return {
                "swept_recently": False,
                "entry_ready": False,
                "rejection_score": 0.0,
                "signal_label": "WATCH",
                "signal_bonus": 0.0,
                "sweep_distance_pct": None,
            }

        touch_idx = touched.index[-1]
        touch_row = recent.loc[touch_idx]
        latest = recent.iloc[-1]
        touch_pos = recent.index.get_loc(touch_idx)
        sweep_distance_pct = ((float(touch_row["high"]) - zone_low) / zone_low) * 100.0 if zone_low > 0 else None
        touch_upper_wick_pct = float(touch_row.get("upper_wick_pct") or 0.0)
        touch_body_pct = float(touch_row.get("body_pct") or 0.0)
        touch_volume_ratio = float(touch_row.get("volume_ratio") or 0.0)
        touch_close_in_range = float(touch_row.get("close_in_range") or 0.5)
        strong_rejection_wick = touch_upper_wick_pct >= max((touch_body_pct * 1.1), tol_pct * 0.8, 0.006)
        close_back_below_zone = float(latest["close"]) <= (zone_low * (1.0 - (tol_pct * 0.2)))
        bearish_after_touch = float(latest["close"]) < float(touch_row["close"])
        latest_is_red = float(latest["close"]) < float(latest["open"])
        failed_acceptance = float(touch_row["close"]) <= zone_mid and touch_close_in_range <= 0.42
        swept_recently = True
        entry_ready = (
            swept_recently
            and close_back_below_zone
            and (
                strong_rejection_wick
                or failed_acceptance
                or latest_is_red
                or bearish_after_touch
            )
        )

        rejection_score = (
            (_norm(touch_upper_wick_pct, 0.004, 0.03) * 45.0)
            + (_norm(touch_volume_ratio, 1.1, 3.0) * 20.0)
            + (_norm((zone_low - float(latest["close"])) / zone_low * 100.0 if zone_low > 0 else 0.0, 0.1, 2.5) * 20.0)
            + (_norm((0.55 - touch_close_in_range), 0.02, 0.35) * 15.0)
        )
        rejection_score = float(_clamp(rejection_score, 0.0, 100.0))

        if entry_ready:
            signal_label = "ENTER"
            signal_bonus = 18.0 + (_norm(rejection_score, 40.0, 90.0) * 8.0)
        elif swept_recently:
            signal_label = "SWEEPED"
            signal_bonus = 10.0 + (_norm(rejection_score, 25.0, 75.0) * 5.0)
        elif touch_pos >= max(1, len(recent) - 2):
            signal_label = "ARMING"
            signal_bonus = 4.0
        else:
            signal_label = "WATCH"
            signal_bonus = 0.0

        return {
            "swept_recently": swept_recently,
            "entry_ready": entry_ready,
            "rejection_score": rejection_score,
            "signal_label": signal_label,
            "signal_bonus": signal_bonus,
            "sweep_distance_pct": round(float(sweep_distance_pct), 2) if sweep_distance_pct is not None else None,
        }

    def _ranked_symbols(self, max_symbols: int) -> list[tuple[str, dict[str, Any]]]:
        cache_key = f"ranked_symbols:{max_symbols if max_symbols > 0 else 'all'}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        stale_cached = self._get_cached(cache_key, allow_stale=True)

        try:
            markets = self.client.load_markets()
            tickers = self.client.fetch_tickers()
        except Exception:
            if stale_cached is not None:
                return stale_cached
            return []
        ranked: list[tuple[str, float, dict[str, Any]]] = []
        for symbol, market in markets.items():
            if not market.get("active", True):
                continue
            if market.get("swap") is not True:
                continue
            if market.get("settle") != "USDT":
                continue
            ticker = tickers.get(symbol) if isinstance(tickers, dict) else None
            if not isinstance(ticker, dict):
                continue
            quote_volume = _safe_float(ticker.get("quoteVolume")) or 0.0
            change_pct = abs(_safe_float(ticker.get("percentage")) or 0.0)
            count = _safe_float(ticker.get("count")) or 0.0
            activity = quote_volume * (1.0 + min(change_pct, 35.0) / 18.0) + (count * 80.0)
            ranked.append((symbol, activity, ticker))

        ranked.sort(key=lambda item: item[1], reverse=True)
        selected = ranked if max_symbols <= 0 else ranked[:max_symbols]
        payload = [(symbol, ticker) for symbol, _activity, ticker in selected]
        return self._set_cache(cache_key, payload, ttl_sec=25)

    @staticmethod
    def _build_scan_payload(
        *,
        scanned: int,
        items: list[dict[str, Any]],
        min_score: float,
        max_symbols: int,
        limit: int,
        note: str,
    ) -> dict[str, Any]:
        safe_limit = max(1, min(limit, 100))
        return {
            "scanned": scanned,
            "count": min(len(items), safe_limit),
            "min_score": min_score,
            "max_symbols": max_symbols,
            "items": items[:safe_limit],
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "note": note,
        }

    def analyze_symbol(self, symbol: str, *, ticker: dict[str, Any] | None = None, include_candles: bool = False) -> dict[str, Any]:
        cache_key = f"pump_symbol:{symbol}:{1 if include_candles else 0}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        frame_15m = self._prepare_frame(symbol=symbol, timeframe="15m", limit=180)
        frame_5m = self._prepare_frame(symbol=symbol, timeframe="5m", limit=180)
        if frame_15m.empty or frame_5m.empty:
            raise ValueError(f"No candles for {symbol}")

        latest_15m = frame_15m.iloc[-1]
        latest_5m = frame_5m.iloc[-1]

        ticker = ticker or self.client.fetch_ticker(symbol)
        current_price = _safe_float(ticker.get("last")) or _safe_float(ticker.get("close")) or float(latest_15m["close"])
        if current_price <= 0:
            current_price = float(latest_15m["close"])

        atr = max(float(latest_15m.get("atr14") or 0.0), current_price * 0.003)
        target_price, zone_low, zone_high, zone_score = self._estimate_target_zone(frame_15m, current_price=current_price, atr=atr)
        distance_pct = ((target_price - current_price) / current_price) * 100 if current_price > 0 and target_price > 0 else 0.0

        invalidation_price = min(
            float(latest_15m.get("ema25") or current_price),
            float(latest_15m.get("prev_low_20") or current_price),
        )
        if invalidation_price <= 0:
            invalidation_price = max(current_price - atr, current_price * 0.985)

        risk_pct = max(0.0, ((current_price - invalidation_price) / current_price) * 100) if current_price > 0 else 0.0
        reward_pct = max(0.0, distance_pct)
        rr_ratio = (reward_pct / risk_pct) if risk_pct > 0 else 0.0

        vol_ratio_15m = float(latest_15m.get("volume_ratio") or 0.0)
        vol_ratio_5m = float(latest_5m.get("volume_ratio") or 0.0)
        vol_z_15m = float(latest_15m.get("volume_z") or 0.0)
        breakout_pct_20 = float(latest_15m.get("breakout_pct_20") or 0.0) * 100.0
        breakout_pct_55 = float(latest_15m.get("breakout_pct_55") or 0.0) * 100.0
        momentum_pct_3 = float(latest_15m.get("ret_3") or 0.0) * 100.0
        momentum_pct_12 = float(latest_15m.get("ret_12") or 0.0) * 100.0
        expansion_ratio = float(latest_15m.get("expansion_ratio") or 1.0)
        close_in_range = float(latest_15m.get("close_in_range") or 0.5)
        body_pct = float(latest_15m.get("body_pct") or 0.0) * 100.0
        range_pct_15m = float(latest_15m.get("range_pct") or 0.0) * 100.0
        atr_pct_15m = float(latest_15m.get("atr_pct") or 0.0) * 100.0
        upper_wick_pct_15m = float(latest_15m.get("upper_wick_pct") or 0.0) * 100.0
        stack_gap_pct = float(latest_15m.get("ema_stack_gap_pct") or 0.0) * 100.0
        above_ema_stack = bool(latest_15m.get("above_ema_stack"))

        pump_score = (
            (_norm(vol_ratio_15m, 1.1, 3.8) * 26.0)
            + (_norm(vol_ratio_5m, 1.1, 4.5) * 10.0)
            + (_norm(vol_z_15m, 0.3, 4.2) * 8.0)
            + (_norm(max(breakout_pct_20, 0.0), 0.1, 4.0) * 17.0)
            + (_norm(max(breakout_pct_55, 0.0), 0.2, 8.0) * 10.0)
            + (_norm(max(momentum_pct_3, 0.0), 0.2, 4.0) * 8.0)
            + (_norm(max(momentum_pct_12, 0.0), 0.5, 8.0) * 6.0)
            + (_norm(expansion_ratio, 0.95, 1.85) * 5.0)
            + (_norm(close_in_range, 0.55, 0.98) * 4.0)
            + (_norm(body_pct, 0.4, 3.6) * 3.0)
            + (_norm(max(stack_gap_pct, 0.0), 0.08, 2.5) * 3.0)
        )

        if above_ema_stack:
            pump_score += 6.0
        if distance_pct < 0.7:
            pump_score -= 5.5
        if distance_pct > 12.0:
            pump_score -= 4.0
        if reward_pct < risk_pct:
            pump_score -= 6.0

        pump_score = round(_clamp(pump_score, 0.0, 100.0), 2)
        stage = self._classify_stage(breakout_pct_20=breakout_pct_20, distance_pct=distance_pct, volume_ratio_fast=vol_ratio_5m)
        sweep_ctx = self._detect_sweep_signal(
            frame_15m,
            zone_low=zone_low,
            zone_high=zone_high,
            current_price=current_price,
            atr=atr,
        )
        if bool(sweep_ctx["entry_ready"]):
            stage = "POST_SWEEP"
        elif bool(sweep_ctx["swept_recently"]):
            stage = "SWEEPED"
        elif stage == "NEAR_SWEEP":
            sweep_ctx["signal_label"] = "ARMING"
            sweep_ctx["signal_bonus"] = max(float(sweep_ctx["signal_bonus"]), 4.0)

        effective_score = round(_clamp(pump_score + float(sweep_ctx["signal_bonus"]), 0.0, 100.0), 2)

        notes: list[str] = []
        if vol_ratio_15m >= 2.0:
            notes.append(f"15m volume spike x{vol_ratio_15m:.2f}")
        if vol_ratio_5m >= 2.3:
            notes.append(f"5m acceleration x{vol_ratio_5m:.2f}")
        if breakout_pct_20 > 0:
            notes.append(f"Breakout {breakout_pct_20:.2f}% above 20-candle high")
        if above_ema_stack:
            notes.append("EMA13 > EMA25 > EMA99")
        if zone_score >= 0.9:
            notes.append("Overhead wick cluster suggests short-liq sweep")
        if rr_ratio >= 1.5:
            notes.append(f"Reward/risk ~ {rr_ratio:.2f}")
        if bool(sweep_ctx["entry_ready"]):
            notes.append("Da quet len kill zone va bi tu choi xuong")
        elif bool(sweep_ctx["swept_recently"]):
            notes.append("Gia vua quet len vung thanh ly gan day")

        payload: dict[str, Any] = {
            "symbol": symbol,
            "mark_price": round(float(current_price), 6),
            "pump_score": pump_score,
            "effective_score": effective_score,
            "stage": stage,
            "signal_label": str(sweep_ctx["signal_label"]),
            "setup_quality": (
                "A"
                if pump_score >= 78
                else "B"
                if pump_score >= 68
                else "C"
                if pump_score >= 58
                else "WATCH"
            ),
            "volume_ratio_5m": round(vol_ratio_5m, 2),
            "volume_ratio_15m": round(vol_ratio_15m, 2),
            "volume_z_15m": round(vol_z_15m, 2),
            "breakout_pct_20": round(breakout_pct_20, 2),
            "breakout_pct_55": round(breakout_pct_55, 2),
            "momentum_pct_3": round(momentum_pct_3, 2),
            "momentum_pct_12": round(momentum_pct_12, 2),
            "expansion_ratio": round(expansion_ratio, 2),
            "close_in_range": round(close_in_range, 2),
            "body_pct": round(body_pct, 2),
            "range_pct_15m": round(range_pct_15m, 2),
            "atr_pct_15m": round(atr_pct_15m, 2),
            "upper_wick_pct_15m": round(upper_wick_pct_15m, 2),
            "ema_stack_gap_pct": round(stack_gap_pct, 2),
            "above_ema_stack": above_ema_stack,
            "est_liq_target_price": round(float(target_price), 6),
            "est_liq_target_low": round(float(zone_low), 6),
            "est_liq_target_high": round(float(zone_high), 6),
            "est_liq_distance_pct": round(float(distance_pct), 2),
            "est_liq_score": round(float(zone_score), 2),
            "invalidation_price": round(float(invalidation_price), 6),
            "risk_pct": round(float(risk_pct), 2),
            "reward_pct": round(float(reward_pct), 2),
            "rr_ratio": round(float(rr_ratio), 2),
            "swept_recently": bool(sweep_ctx["swept_recently"]),
            "entry_ready": bool(sweep_ctx["entry_ready"]),
            "rejection_score": round(float(sweep_ctx["rejection_score"]), 2),
            "sweep_distance_pct": sweep_ctx["sweep_distance_pct"],
            "notes": notes[:5],
            "ticker_quote_volume": round(_safe_float(ticker.get("quoteVolume")) or 0.0, 2),
            "ticker_change_pct_24h": round(_safe_float(ticker.get("percentage")) or 0.0, 2),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

        if include_candles:
            detail_candles = []
            for _, row in frame_15m.tail(72).iterrows():
                detail_candles.append(
                    {
                        "timestamp": int(pd.Timestamp(row["timestamp"]).timestamp() * 1000),
                        "open": round(float(row["open"]), 6),
                        "high": round(float(row["high"]), 6),
                        "low": round(float(row["low"]), 6),
                        "close": round(float(row["close"]), 6),
                        "volume": round(float(row["volume"]), 6),
                        "ema13": round(float(row["ema13"]), 6),
                        "ema25": round(float(row["ema25"]), 6),
                        "ema99": round(float(row["ema99"]), 6),
                    }
                )
            payload["candles"] = detail_candles

        return self._set_cache(cache_key, payload, ttl_sec=20 if include_candles else 30)

    def scan(self, *, max_symbols: int = 35, min_score: float = 58.0, limit: int = 18) -> dict[str, Any]:
        safe_max_symbols = 0 if int(max_symbols) <= 0 else max(10, min(max_symbols, 500))
        safe_limit = max(1, min(limit, 100))
        safe_min_score = _clamp(float(min_score), 0.0, 100.0)
        cache_key = f"pump_scan:{safe_max_symbols if safe_max_symbols > 0 else 'all'}:{safe_min_score:.2f}:{safe_limit}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        stale_cached = self._get_cached(cache_key, allow_stale=True)

        items: list[dict[str, Any]] = []
        try:
            ranked_symbols = self._ranked_symbols(max_symbols=safe_max_symbols)
        except Exception as exc:
            if stale_cached is not None:
                return stale_cached
            return self._build_scan_payload(
                scanned=0,
                items=[],
                min_score=safe_min_score,
                max_symbols=safe_max_symbols,
                limit=safe_limit,
                note=f"Pump hunter tam thoi khong lay duoc du lieu dau vao: {exc}",
            )

        if not ranked_symbols:
            if stale_cached is not None:
                return stale_cached
            return self._build_scan_payload(
                scanned=0,
                items=[],
                min_score=safe_min_score,
                max_symbols=safe_max_symbols,
                limit=safe_limit,
                note="Pump hunter tam thoi khong lay duoc symbol/ticker tu Binance REST. Thu quet lai sau.",
            )

        for symbol, ticker in ranked_symbols:
            try:
                row = self.analyze_symbol(symbol=symbol, ticker=ticker, include_candles=False)
            except Exception:
                continue
            if float(row.get("effective_score") or row.get("pump_score") or 0.0) < safe_min_score:
                continue
            if float(row.get("est_liq_target_price") or 0.0) <= float(row.get("mark_price") or 0.0):
                continue
            items.append(row)

        items.sort(
            key=lambda row: (
                float(row.get("effective_score") or row.get("pump_score") or 0.0),
                float(row.get("est_liq_score") or 0.0),
                float(row.get("volume_ratio_15m") or 0.0),
            ),
            reverse=True,
        )
        payload = self._build_scan_payload(
            scanned=len(ranked_symbols),
            items=items,
            min_score=safe_min_score,
            max_symbols=safe_max_symbols,
            limit=safe_limit,
            note=(
                "Heuristic pump-seed model: volume shock + breakout + EMA stack + overhead wick cluster."
                if safe_max_symbols > 0
                else "Full scan mode: quet toan bo futures USDT, co the cham hon va de gap REST rate-limit hon."
            ),
        )
        for row in payload.get("items", []):
            self._maybe_send_discord_alert(row)
        return self._set_cache(cache_key, payload, ttl_sec=18)
