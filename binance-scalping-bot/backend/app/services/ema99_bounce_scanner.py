from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from app.core.config import settings
from app.services.binance_client import BinanceFuturesClient

logger = logging.getLogger(__name__)


@dataclass
class CacheItem:
    expires_at: float
    payload: dict[str, Any]


class Ema99BounceScannerService:
    def __init__(self, client: BinanceFuturesClient | None = None) -> None:
        self.client = client or BinanceFuturesClient()
        self._lock = threading.Lock()
        self._cache: dict[str, CacheItem] = {}
        self._alert_lock = threading.Lock()
        self._alert_registry: dict[str, float] = {}

    def _get_cached(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._cache.get(key)
        if item is None or time.time() > item.expires_at:
            return None
        return item.payload

    def _set_cached(self, key: str, payload: dict[str, Any], ttl_sec: int) -> dict[str, Any]:
        with self._lock:
            self._cache[key] = CacheItem(expires_at=time.time() + ttl_sec, payload=payload)
        return payload

    def _claim_alert_slot(self, key: str, *, ttl_sec: int) -> bool:
        now = time.time()
        with self._alert_lock:
            expired = [item_key for item_key, expires_at in self._alert_registry.items() if expires_at <= now]
            for item_key in expired:
                self._alert_registry.pop(item_key, None)
            if self._alert_registry.get(key, 0.0) > now:
                return False
            self._alert_registry[key] = now + ttl_sec
            return True

    def _binance_symbol_set(self) -> set[str]:
        cache_key = "ema99_binance_symbol_list"
        cached = self._get_cached(cache_key)
        if isinstance(cached, list):
            return {str(item).strip() for item in cached if str(item).strip()}
        try:
            markets = self.client.load_binance_markets()
            symbols: list[str] = []
            for market in markets.values():
                if not market.get("active", True):
                    continue
                if market.get("swap") is not True:
                    continue
                if market.get("settle") != "USDT":
                    continue
                symbol = str(market.get("symbol") or "").strip()
                if symbol:
                    symbols.append(symbol)
            symbols = sorted(set(symbols))
            if symbols:
                self._set_cached(cache_key, symbols, ttl_sec=600)
                return set(symbols)
        except Exception:
            pass
        return set()

    def _is_binance_symbol(self, symbol: str) -> bool:
        return str(symbol or "").strip() in self._binance_symbol_set()

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except Exception:
            return None

    @staticmethod
    def _to_frame(rows: list[list[Any]]) -> pd.DataFrame:
        frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
        for column in ["open", "high", "low", "close", "volume"]:
            frame[column] = frame[column].astype(float)
        return frame.sort_values("timestamp").reset_index(drop=True)

    @staticmethod
    def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
        delta = close.diff()
        gain = delta.clip(lower=0.0)
        loss = (-delta).clip(lower=0.0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0.0, np.nan)
        return (100.0 - (100.0 / (1.0 + rs))).fillna(50.0)

    @staticmethod
    def _macd_hist(close: pd.Series) -> pd.Series:
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        return (macd - signal).fillna(0.0)

    def _prepare_frame(self, rows: list[list[Any]]) -> pd.DataFrame:
        frame = self._to_frame(rows)
        frame["ema25"] = frame["close"].ewm(span=25, adjust=False).mean()
        frame["ema99"] = frame["close"].ewm(span=99, adjust=False).mean()
        frame["ema99_slope_3"] = frame["ema99"].pct_change(3).fillna(0.0)
        frame["rsi14"] = self._rsi(frame["close"], 14)
        frame["macd_hist"] = self._macd_hist(frame["close"])
        frame["vol_ma20"] = frame["volume"].rolling(20, min_periods=5).mean()
        frame["volume_ratio"] = (
            frame["volume"] / frame["vol_ma20"].replace(0.0, np.nan)
        ).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        candle_range = (frame["high"] - frame["low"]).replace(0.0, np.nan)
        frame["close_in_range"] = (
            (frame["close"] - frame["low"]) / candle_range
        ).replace([np.inf, -np.inf], np.nan).fillna(0.5)
        frame["body_pct"] = (
            (frame["close"] - frame["open"]).abs() / frame["open"].replace(0.0, np.nan)
        ).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        frame["ema99_gap_pct"] = (
            (frame["close"] - frame["ema99"]) / frame["ema99"].replace(0.0, np.nan) * 100.0
        ).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        frame["low_touch_gap_pct"] = (
            (frame["low"] - frame["ema99"]).abs() / frame["ema99"].replace(0.0, np.nan) * 100.0
        ).replace([np.inf, -np.inf], np.nan).fillna(999.0)
        frame["high_touch_gap_pct"] = (
            (frame["high"] - frame["ema99"]).abs() / frame["ema99"].replace(0.0, np.nan) * 100.0
        ).replace([np.inf, -np.inf], np.nan).fillna(999.0)
        return frame

    def _signal_masks(self, frame: pd.DataFrame, timeframe: str) -> tuple[pd.Series, pd.Series]:
        if timeframe == "4h":
            touch_tol_pct = 1.2
            max_gap_pct = 2.5
            body_min_pct = 0.35
            slope_bars = 4
            timeframe_bonus = 1.0
        else:
            touch_tol_pct = 0.8
            max_gap_pct = 1.2
            body_min_pct = 0.25
            slope_bars = 3
            timeframe_bonus = 0.0

        ema99_up = frame["ema99"] > frame["ema99"].shift(slope_bars)
        ema99_down = frame["ema99"] < frame["ema99"].shift(slope_bars)
        bullish_candle = frame["close"] > frame["open"]
        bearish_candle = frame["close"] < frame["open"]
        volume_ok = (frame["volume"] > frame["volume"].shift(1)) & (frame["volume_ratio"] >= 1.15)
        long_momentum_ok = (frame["rsi14"] > frame["rsi14"].shift(1)) & (frame["macd_hist"] > frame["macd_hist"].shift(1))
        short_momentum_ok = (frame["rsi14"] < frame["rsi14"].shift(1)) & (frame["macd_hist"] < frame["macd_hist"].shift(1))

        long_signal = (
            ((frame["ema25"] > frame["ema99"]) | ema99_up)
            & (frame["low_touch_gap_pct"] <= touch_tol_pct)
            & (frame["close"] > frame["ema99"])
            & bullish_candle
            & (frame["close_in_range"] >= 0.55)
            & volume_ok
            & long_momentum_ok
            & frame["ema99_gap_pct"].between(0.0, max_gap_pct)
            & ((frame["body_pct"] * 100.0) >= body_min_pct)
        )

        short_signal = (
            ((frame["ema25"] < frame["ema99"]) | ema99_down)
            & (frame["high_touch_gap_pct"] <= touch_tol_pct)
            & (frame["close"] < frame["ema99"])
            & bearish_candle
            & (frame["close_in_range"] <= 0.45)
            & volume_ok
            & short_momentum_ok
            & frame["ema99_gap_pct"].between(-max_gap_pct, 0.0)
            & ((frame["body_pct"] * 100.0) >= body_min_pct)
        )

        body_ok = (frame["body_pct"] * 100.0) >= body_min_pct
        frame["_ema99_bounce_long_score"] = (
            frame["volume_ratio"].clip(lower=0.0, upper=6.0) * 2.0
            + (touch_tol_pct - frame["low_touch_gap_pct"]).clip(lower=0.0) * 1.5
            + ((frame["rsi14"] - 50.0) / 10.0).clip(lower=0.0)
            + timeframe_bonus
        )
        frame["_ema99_bounce_short_score"] = (
            frame["volume_ratio"].clip(lower=0.0, upper=6.0) * 2.0
            + (touch_tol_pct - frame["high_touch_gap_pct"]).clip(lower=0.0) * 1.5
            + ((50.0 - frame["rsi14"]) / 10.0).clip(lower=0.0)
            + timeframe_bonus
        )
        return long_signal & body_ok, short_signal & body_ok

    @staticmethod
    def _max_gap_pct_for_timeframe(timeframe: str) -> float:
        return 2.5 if timeframe == "4h" else 1.2

    @staticmethod
    def _format_number(value: float | None, digits: int = 6) -> str:
        if value is None:
            return "-"
        if not np.isfinite(value):
            return "-"
        return f"{float(value):.{digits}f}"

    @staticmethod
    def _format_signed_pct(value: float | None, digits: int = 2) -> str:
        if value is None:
            return "-"
        if not np.isfinite(value):
            return "-"
        return f"{float(value):+.{digits}f}%"

    @staticmethod
    def _post_discord_request(request: Request) -> None:
        with urlopen(request, timeout=10) as response:
            response.read()

    @staticmethod
    def _signal_time_text(value: Any) -> str:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat()
        return str(value or "")

    @staticmethod
    def _probability_from_score(score: float) -> float:
        return max(0.05, min(1.0, float(score) / 10.0))

    def _derive_paper_trade_plan(self, item: dict[str, Any]) -> tuple[float, float, float] | None:
        timeframe = str(item.get("timeframe") or "").strip().lower()
        side = str(item.get("side") or "").strip().upper()
        entry = self._safe_float(item.get("mark_price")) or self._safe_float(item.get("close_price"))
        ema99 = self._safe_float(item.get("ema99"))
        if entry is None or ema99 is None or entry <= 0 or ema99 <= 0 or side not in {"LONG", "SHORT"}:
            return None

        is_4h = timeframe == "4h"
        tp_pct = float(settings.ema99_bounce_4h_tp_pct if is_4h else settings.ema99_bounce_1h_tp_pct)
        sl_pct = float(settings.ema99_bounce_4h_sl_pct if is_4h else settings.ema99_bounce_1h_sl_pct)
        ema_buffer_pct = 0.25 if is_4h else 0.15

        if side == "LONG":
            stop_from_pct = entry * (1.0 - (sl_pct / 100.0))
            stop_from_ema = ema99 * (1.0 - (ema_buffer_pct / 100.0))
            stop_loss = min(stop_from_pct, stop_from_ema)
            if stop_loss >= entry:
                stop_loss = stop_from_pct
            risk_pct = max(0.2, ((entry - stop_loss) / entry) * 100.0)
            target_pct = max(tp_pct, risk_pct * 1.8)
            take_profit = entry * (1.0 + (target_pct / 100.0))
            if take_profit <= entry or stop_loss >= entry:
                return None
            return float(entry), float(take_profit), float(stop_loss)

        stop_from_pct = entry * (1.0 + (sl_pct / 100.0))
        stop_from_ema = ema99 * (1.0 + (ema_buffer_pct / 100.0))
        stop_loss = max(stop_from_pct, stop_from_ema)
        if stop_loss <= entry:
            stop_loss = stop_from_pct
        risk_pct = max(0.2, ((stop_loss - entry) / entry) * 100.0)
        target_pct = max(tp_pct, risk_pct * 1.8)
        take_profit = entry * (1.0 - (target_pct / 100.0))
        if take_profit >= entry or stop_loss <= entry:
            return None
        return float(entry), float(take_profit), float(stop_loss)

    def _maybe_execute_paper_order(self, item: dict[str, Any]) -> None:
        if not settings.ema99_bounce_paper_trade_enabled:
            return
        if not bool(item.get("entry_ok")):
            return
        score = float(item.get("score") or 0.0)
        if score < float(settings.ema99_bounce_paper_min_score):
            return
        symbol = str(item.get("symbol") or "").strip()
        timeframe = str(item.get("timeframe") or "").strip()
        side = str(item.get("side") or "").strip().upper()
        signal_time_text = self._signal_time_text(item.get("signal_time"))
        if not symbol or not timeframe or side not in {"LONG", "SHORT"} or not signal_time_text:
            return
        if not self._is_binance_symbol(symbol):
            logger.info(
                "EMA99 bounce paper order skipped: symbol=%s timeframe=%s side=%s reason=not_listed_on_binance",
                symbol,
                timeframe,
                side,
            )
            return
        plan = self._derive_paper_trade_plan(item)
        if plan is None:
            return
        entry, take_profit, stop_loss = plan
        order_key = f"ema99_paper_order:{symbol}:{timeframe}:{side}:{signal_time_text}"
        cooldown_sec = max(900, int(settings.ema99_bounce_paper_signal_cooldown_sec))
        if not self._claim_alert_slot(order_key, ttl_sec=cooldown_sec):
            return
        try:
            from app.api.paper_trades import paper_trade_api
            from app.models.paper_trades import PaperMarketOpenRequest

            probability = self._probability_from_score(score)
            request = PaperMarketOpenRequest(
                symbol=symbol,
                side=side,
                signal_win_probability=probability,
                effective_win_probability=probability,
                repo_scope="main",
                entry_type="EMA99_BOUNCE",
                entry_price=entry,
                take_profit=take_profit,
                stop_loss=stop_loss,
                reference_win_symbol=symbol,
                entry_point_score=score,
                order_usdt=float(settings.ema99_bounce_paper_order_usdt),
                margin_usdt=float(settings.ema99_bounce_paper_margin_usdt),
                leverage=max(1, int(settings.ema99_bounce_paper_leverage)),
                entry_snapshot={
                    "source": "ema99_bounce_auto_paper",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "side": side,
                    "signal_type": f"EMA99_BOUNCE_{side}",
                    "signal_label": str(item.get("entry_status") or "").upper() or "ENTRY_OK",
                    "execution_mode": "AUTO_BG",
                    "signal_time": signal_time_text,
                    "ema99_score": score,
                    "ema25": float(item.get("ema25") or 0.0),
                    "ema99": float(item.get("ema99") or 0.0),
                    "volume_ratio": float(item.get("volume_ratio") or 0.0),
                    "touch_gap_pct": float(item.get("touch_gap_pct") or 0.0),
                    "ema99_gap_pct": float(item.get("ema99_gap_pct") or 0.0),
                    "mark_price": float(item.get("mark_price") or 0.0),
                    "close_price": float(item.get("close_price") or 0.0),
                    "entry_ok": bool(item.get("entry_ok")),
                    "entry_status": str(item.get("entry_status") or ""),
                    "planned_entry_price": entry,
                    "take_profit_price": take_profit,
                    "stop_loss_price": stop_loss,
                },
            )
            trade = asyncio.run(paper_trade_api.market_open(request))
            self._set_cached(order_key, {"trade_id": int(trade.id)}, ttl_sec=cooldown_sec)
            logger.info(
                "EMA99 bounce paper order opened: symbol=%s timeframe=%s side=%s trade_id=%s score=%.2f entry=%.8f tp=%.8f sl=%.8f",
                symbol,
                timeframe,
                side,
                int(trade.id),
                score,
                entry,
                take_profit,
                stop_loss,
            )
        except Exception as exc:
            self._alert_registry.pop(order_key, None)
            status_code = getattr(exc, "status_code", None)
            detail = getattr(exc, "detail", None)
            if status_code is not None:
                logger.info(
                    "EMA99 bounce paper order skipped: symbol=%s timeframe=%s side=%s status=%s reason=%s",
                    symbol,
                    timeframe,
                    side,
                    status_code,
                    detail or str(exc),
                )
                return
            logger.exception("EMA99 bounce paper order failed: symbol=%s timeframe=%s side=%s", symbol, timeframe, side)

    def _process_signal_side_effects(self, payload: dict[str, Any]) -> None:
        for item in list(payload.get("items") or []):
            self._send_discord_alert(item)
            self._maybe_execute_paper_order(item)

    def _send_discord_alert(self, item: dict[str, Any]) -> None:
        if not settings.ema99_bounce_discord_alert_enabled:
            return
        webhook_url = str(settings.ema99_bounce_discord_webhook_url or "").strip()
        if not webhook_url:
            return
        score = float(item.get("score") or 0.0)
        if score <= float(settings.ema99_bounce_discord_min_score):
            return
        symbol = str(item.get("symbol") or "").strip()
        timeframe = str(item.get("timeframe") or "").strip()
        side = str(item.get("side") or "").strip().upper()
        signal_time = item.get("signal_time")
        signal_time_text = (
            signal_time.astimezone(timezone.utc).isoformat()
            if isinstance(signal_time, datetime)
            else str(signal_time or "")
        )
        if not symbol or not timeframe or not side or not signal_time_text:
            return
        alert_key = f"ema99_bounce:{symbol}:{timeframe}:{side}:{signal_time_text}"
        cooldown_sec = max(3600, int(settings.ema99_bounce_discord_alert_cooldown_sec))
        if not self._claim_alert_slot(alert_key, ttl_sec=cooldown_sec):
            return

        close_price = self._safe_float(item.get("close_price"))
        ema99 = self._safe_float(item.get("ema99"))
        mark_price = self._safe_float(item.get("mark_price"))
        volume_ratio = self._safe_float(item.get("volume_ratio")) or 0.0
        gap_pct = self._safe_float(item.get("ema99_gap_pct")) or 0.0
        touch_gap_pct = self._safe_float(item.get("touch_gap_pct")) or 0.0
        rsi14 = self._safe_float(item.get("rsi14")) or 0.0
        entry_status = str(item.get("entry_status") or "-")
        signal_time_label = signal_time_text
        if isinstance(signal_time, datetime):
            signal_time_label = signal_time.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        color = 0xED4245 if side == "SHORT" else 0x57F287
        payload = json.dumps(
            {
                "username": "EMA99 Bounce Scanner",
                "allowed_mentions": {"parse": []},
                "embeds": [
                    {
                        "title": f"EMA99 {side} signal: {symbol}",
                        "color": color,
                        "fields": [
                            {"name": "Timeframe", "value": timeframe, "inline": True},
                            {"name": "Side", "value": side, "inline": True},
                            {"name": "Score", "value": f"{score:.2f}", "inline": True},
                            {"name": "Entry", "value": entry_status, "inline": True},
                            {"name": "Mark", "value": self._format_number(mark_price, 8), "inline": True},
                            {"name": "Close", "value": self._format_number(close_price, 8), "inline": True},
                            {"name": "EMA99", "value": self._format_number(ema99, 8), "inline": True},
                            {"name": "Gap", "value": self._format_signed_pct(gap_pct), "inline": True},
                            {"name": "Touch", "value": self._format_signed_pct(touch_gap_pct), "inline": True},
                            {"name": "Vol x", "value": f"{volume_ratio:.2f}x", "inline": True},
                            {"name": "RSI14", "value": f"{rsi14:.1f}", "inline": True},
                            {"name": "Signal Time", "value": signal_time_label, "inline": False},
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        request = Request(
            webhook_url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; Codex EMA99 Scanner/1.0)",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            self._post_discord_request(request)
            logger.info(
                "EMA99 bounce Discord alert sent: symbol=%s timeframe=%s side=%s score=%.2f signal_time=%s",
                symbol,
                timeframe,
                side,
                score,
                signal_time_text,
            )
        except Exception:
            logger.exception("EMA99 bounce Discord alert failed for %s %s %s", symbol, timeframe, side)

    def _load_candidate_symbols(self, max_symbols: int) -> list[tuple[str, float]]:
        markets = self.client.load_binance_markets()
        symbols: list[str] = []
        for market in markets.values():
            if not market.get("active", True):
                continue
            if market.get("swap") is not True:
                continue
            if market.get("settle") != "USDT":
                continue
            symbol = str(market.get("symbol") or "").strip()
            if symbol:
                symbols.append(symbol)

        tickers = self.client.fetch_binance_tickers()
        ranked: list[tuple[str, float]] = []
        for symbol in sorted(set(symbols)):
            ticker = tickers.get(symbol) if isinstance(tickers, dict) else None
            quote_volume = self._safe_float(ticker.get("quoteVolume")) if isinstance(ticker, dict) else None
            ranked.append((symbol, float(quote_volume or 0.0)))

        ranked.sort(key=lambda item: item[1], reverse=True)
        return ranked[:max_symbols]

    def scan_current_signals(
        self,
        *,
        max_symbols: int = 200,
        max_items: int = 12,
        timeframes: tuple[str, ...] = ("4h", "1h"),
        cache_ttl_sec: int = 10,
        send_alerts: bool = False,
    ) -> dict[str, Any]:
        cache_key = f"ema99_bounce:{max_symbols}:{max_items}:{','.join(timeframes)}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            if send_alerts:
                self._process_signal_side_effects(cached)
            return cached

        ranked_symbols = self._load_candidate_symbols(max_symbols=max_symbols)
        try:
            tickers = self.client.fetch_binance_tickers([symbol for symbol, _ in ranked_symbols])
        except Exception:
            tickers = {}
        items: list[dict[str, Any]] = []

        for symbol, _quote_volume in ranked_symbols:
            ticker = tickers.get(symbol) if isinstance(tickers, dict) else None
            if ticker is None:
                try:
                    ticker = self.client.fetch_binance_ticker(symbol)
                except Exception:
                    ticker = None
            mark_price = None
            if isinstance(ticker, dict):
                mark_price = (
                    self._safe_float(ticker.get("last"))
                    or self._safe_float(ticker.get("close"))
                    or self._safe_float(ticker.get("bid"))
                    or self._safe_float(ticker.get("ask"))
                )
            for timeframe in timeframes:
                try:
                    rows = self.client.fetch_binance_ohlcv(symbol=symbol, timeframe=timeframe, limit=220)
                    frame = self._prepare_frame(rows)
                    if len(frame) < 120:
                        continue
                    long_signal, short_signal = self._signal_masks(frame, timeframe)
                    idx = len(frame) - 2
                    if idx < 0:
                        continue
                    row = frame.iloc[idx]
                    if bool(long_signal.iloc[idx]):
                        gap_pct = float(row["ema99_gap_pct"])
                        items.append(
                            {
                                "symbol": symbol,
                                "timeframe": timeframe,
                                "side": "LONG",
                                "signal_time": row["timestamp"].to_pydatetime(),
                                "mark_price": mark_price,
                                "close_price": float(row["close"]),
                                "ema25": float(row["ema25"]),
                                "ema99": float(row["ema99"]),
                                "volume_ratio": float(row["volume_ratio"]),
                                "touch_gap_pct": float(row["low_touch_gap_pct"]),
                                "ema99_gap_pct": gap_pct,
                                "entry_ok": gap_pct <= self._max_gap_pct_for_timeframe(timeframe),
                                "entry_status": "ENTRY OK" if gap_pct <= self._max_gap_pct_for_timeframe(timeframe) else "TOO FAR",
                                "rsi14": float(row["rsi14"]),
                                "score": float(row["_ema99_bounce_long_score"]),
                            }
                        )
                    if bool(short_signal.iloc[idx]):
                        gap_pct = abs(float(row["ema99_gap_pct"]))
                        items.append(
                            {
                                "symbol": symbol,
                                "timeframe": timeframe,
                                "side": "SHORT",
                                "signal_time": row["timestamp"].to_pydatetime(),
                                "mark_price": mark_price,
                                "close_price": float(row["close"]),
                                "ema25": float(row["ema25"]),
                                "ema99": float(row["ema99"]),
                                "volume_ratio": float(row["volume_ratio"]),
                                "touch_gap_pct": float(row["high_touch_gap_pct"]),
                                "ema99_gap_pct": -gap_pct,
                                "entry_ok": gap_pct <= self._max_gap_pct_for_timeframe(timeframe),
                                "entry_status": "ENTRY OK" if gap_pct <= self._max_gap_pct_for_timeframe(timeframe) else "TOO FAR",
                                "rsi14": float(row["rsi14"]),
                                "score": float(row["_ema99_bounce_short_score"]),
                            }
                        )
                except Exception:
                    continue

        items.sort(key=lambda item: (item["score"], item["volume_ratio"]), reverse=True)
        payload = {
            "count": min(len(items), max_items),
            "scanned": len(ranked_symbols),
            "generated_at": datetime.now(timezone.utc),
            "items": items[:max_items],
        }
        if send_alerts:
            self._process_signal_side_effects(payload)
        return self._set_cached(cache_key, payload, ttl_sec=cache_ttl_sec)
