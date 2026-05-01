from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any
from urllib.request import Request, urlopen

import numpy as np
import pandas as pd

from app.core.config import settings
from app.services.binance_client import BinanceFuturesClient
from app.services.binance_futures_trade_service import BinanceApiError, BinanceFuturesTradeService

logger = logging.getLogger(__name__)

PUMP_HUNTER_HIGHLIGHT_TP_PCT = 10.0
PUMP_HUNTER_MIN_BINANCE_ORDER_TP_PCT = 20.0


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
        self.trade_client = BinanceFuturesTradeService()
        self.cache: dict[str, CacheItem] = {}
        self._alert_lock = threading.Lock()
        self._live_order_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._live_order_registry: dict[str, dict[str, Any]] = {}
        self._live_order_status: dict[str, Any] = {
            "state": "disabled",
            "mode": "DISABLED",
            "reason": "live_trade_disabled",
            "message": "Pump Hunter live trade is disabled",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def _binance_symbol_set(self) -> set[str]:
        cache_key = "binance_symbol_set"
        cached = self._get_cached(cache_key)
        if isinstance(cached, set):
            return cached
        stale_cached = self._get_cached(cache_key, allow_stale=True)
        try:
            markets = self.client.load_binance_markets()
            symbols: set[str] = set()
            for market in markets.values():
                if not market.get("active", True):
                    continue
                if market.get("swap") is not True:
                    continue
                if market.get("settle") != "USDT":
                    continue
                symbol = str(market.get("symbol") or "").strip()
                if symbol:
                    symbols.add(symbol)
            if symbols:
                return self._set_cache(cache_key, symbols, ttl_sec=600)
        except Exception:
            pass
        if isinstance(stale_cached, set):
            return stale_cached
        return set()

    def _is_binance_symbol(self, symbol: str) -> bool:
        return str(symbol or "").strip() in self._binance_symbol_set()

    @staticmethod
    def _derive_post_sweep_short_entry(
        *,
        current_price: float,
        invalidation_price: float,
        zone_low: float,
        zone_high: float,
    ) -> float:
        if current_price <= 0:
            return 0.0
        base_entry = max(current_price, invalidation_price if invalidation_price > 0 else current_price)
        rebound_cap = zone_low if zone_low > base_entry else 0.0
        if rebound_cap <= base_entry and zone_high > base_entry:
            rebound_cap = zone_high * 0.985
        if rebound_cap > base_entry:
            entry = base_entry + ((rebound_cap - base_entry) * 0.55)
        else:
            entry = base_entry * 1.0015
        if zone_high > 0:
            entry = min(entry, zone_high * 0.9975)
        return max(base_entry, entry)

    @classmethod
    def _compress_take_profit_if_needed(cls, *, side: str, entry: float, take_profit: float) -> float:
        if entry <= 0 or take_profit <= 0:
            return take_profit
        leverage = max(1, int(settings.pump_hunter_live_leverage or 5))
        tp_pct, _ = cls._calc_trade_pct_metrics(
            side=side,
            entry=entry,
            take_profit=take_profit,
            stop_loss=entry,
            leverage=leverage,
        )
        threshold_pct = float(settings.pump_hunter_tp_scale_down_threshold_pct)
        if tp_pct <= threshold_pct:
            return take_profit
        factor = _clamp(float(settings.pump_hunter_tp_scale_down_factor), 0.05, 1.0)
        if factor >= 0.999:
            return take_profit
        distance = abs(entry - take_profit)
        if distance <= 0:
            return take_profit
        if str(side).upper() == "SHORT":
            return entry - (distance * factor)
        return entry + (distance * factor)

    @staticmethod
    def _price_for_target_pnl_pct(*, side: str, entry: float, leverage: int, pnl_pct: float) -> float:
        if entry <= 0 or leverage <= 0 or pnl_pct <= 0:
            return entry
        move_ratio = pnl_pct / (float(leverage) * 100.0)
        side_text = str(side or "").upper()
        if side_text == "SHORT":
            return max(0.0, entry * (1.0 - move_ratio))
        return entry * (1.0 + move_ratio)

    @classmethod
    def _normalize_live_take_profit(cls, *, side: str, entry: float, take_profit: float, leverage: int) -> float:
        if entry <= 0 or take_profit <= 0 or leverage <= 0:
            return take_profit
        tp_pct, _ = cls._calc_trade_pct_metrics(
            side=side,
            entry=entry,
            take_profit=take_profit,
            stop_loss=entry,
            leverage=leverage,
        )
        if tp_pct < float(settings.pump_hunter_live_low_expected_pnl_threshold_pct):
            take_profit = cls._price_for_target_pnl_pct(
                side=side,
                entry=entry,
                leverage=leverage,
                pnl_pct=float(settings.pump_hunter_live_low_expected_pnl_target_tp_pct),
            )
        return cls._compress_take_profit_if_needed(side=side, entry=entry, take_profit=take_profit)

    @classmethod
    def _derive_trade_plan(cls, row: dict[str, Any]) -> tuple[str, float, float, float]:
        explicit_side = str(row.get("trade_side") or "").upper()
        if explicit_side in {"LONG", "SHORT"}:
            side = explicit_side
        else:
            signal_label = str(row.get("signal_label") or "").upper()
            stage = str(row.get("stage") or "").upper()
            is_post_sweep = signal_label in {"ENTER", "SWEEPED"} or stage in {"POST_SWEEP", "SWEEPED"}
            side = "SHORT" if is_post_sweep else "LONG"
        mark_price = float(row.get("mark_price") or 0.0)
        invalidation_price = float(row.get("invalidation_price") or 0.0)
        zone_low = float(row.get("est_liq_target_low") or 0.0)
        zone_high = float(row.get("est_liq_target_high") or 0.0)
        leverage = max(1, int(settings.pump_hunter_live_leverage or 5))
        if side == "SHORT":
            entry = cls._derive_post_sweep_short_entry(
                current_price=mark_price,
                invalidation_price=invalidation_price,
                zone_low=zone_low,
                zone_high=zone_high,
            )
            take_profit = invalidation_price
            stop_loss = zone_high
        else:
            entry = mark_price
            take_profit = float(row.get("est_liq_target_price") or 0.0)
            stop_loss = invalidation_price
        take_profit = cls._normalize_live_take_profit(
            side=side,
            entry=entry,
            take_profit=take_profit,
            leverage=leverage,
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

    @staticmethod
    def _calc_trade_pct_metrics(*, side: str, entry: float, take_profit: float, stop_loss: float, leverage: int) -> tuple[float, float]:
        tp_pct = 0.0
        sl_pct = 0.0
        if entry > 0:
            if str(side).upper() == "LONG":
                tp_pct = ((take_profit - entry) / entry) * leverage * 100.0
                sl_pct = ((stop_loss - entry) / entry) * leverage * 100.0
            else:
                tp_pct = ((entry - take_profit) / entry) * leverage * 100.0
                sl_pct = ((entry - stop_loss) / entry) * leverage * 100.0
        return float(tp_pct), float(sl_pct)

    @staticmethod
    def _live_order_tp_threshold_pct() -> float:
        configured_threshold = float(settings.pump_hunter_live_min_tp_pct)
        return max(configured_threshold, PUMP_HUNTER_MIN_BINANCE_ORDER_TP_PCT)

    def _validate_live_order_tp_requirement(
        self,
        row: dict[str, Any],
        *,
        leverage: int,
    ) -> tuple[str, float, float, float, float]:
        side, entry, take_profit, stop_loss = self._derive_trade_plan(row)
        if entry <= 0 or take_profit <= 0:
            raise ValueError("Cannot place Binance order without a valid entry and take-profit")
        tp_pct, _ = self._calc_trade_pct_metrics(
            side=side,
            entry=entry,
            take_profit=take_profit,
            stop_loss=stop_loss,
            leverage=leverage,
        )
        threshold_pct = self._live_order_tp_threshold_pct()
        if tp_pct <= threshold_pct:
            raise ValueError(
                f"Expected TP {tp_pct:.2f}% must be greater than {threshold_pct:.2f}% for Binance live orders"
            )
        return side, entry, take_profit, stop_loss, tp_pct

    @staticmethod
    def _extract_binance_error_code(payload: Any) -> int | None:
        if not isinstance(payload, dict):
            return None
        try:
            code = payload.get("code")
            return int(code) if code is not None else None
        except Exception:
            return None

    def _set_live_order_status(
        self,
        *,
        state: str,
        mode: str,
        reason: str,
        message: str,
        status_code: int | None = None,
        binance_code: int | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "state": str(state or "").lower() or "unknown",
            "mode": str(mode or "").upper() or "UNKNOWN",
            "reason": str(reason or "").lower() or "unknown",
            "message": str(message or "").strip() or "Unknown live status",
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if status_code is not None:
            payload["status_code"] = int(status_code)
        if binance_code is not None:
            payload["binance_code"] = int(binance_code)
        if meta:
            payload.update(meta)
        with self._status_lock:
            self._live_order_status = payload
        return dict(payload)

    def get_live_order_status(self) -> dict[str, Any]:
        with self._status_lock:
            return dict(self._live_order_status)

    def refresh_live_order_status(self, *, force: bool = False) -> dict[str, Any]:
        if not settings.pump_hunter_live_trade_enabled:
            return self._set_live_order_status(
                state="disabled",
                mode="DISABLED",
                reason="live_trade_disabled",
                message="Pump Hunter live trade is disabled",
            )
        if bool(settings.pump_hunter_live_order_test_mode):
            return self._set_live_order_status(
                state="ok",
                mode="TEST",
                reason="test_mode",
                message="Pump Hunter is running in Binance test-order mode",
            )
        if not str(settings.binance_api_key or "").strip() or not str(settings.binance_api_secret or "").strip():
            return self._set_live_order_status(
                state="error",
                mode="LIVE",
                reason="missing_credentials",
                message="BINANCE_API_KEY or BINANCE_API_SECRET is missing",
            )
        current = self.get_live_order_status()
        ttl_sec = max(30, int(settings.pump_hunter_live_status_cache_sec))
        if not force and current.get("mode") == "LIVE":
            updated_at = str(current.get("updated_at") or "").strip()
            if updated_at:
                try:
                    updated_ts = pd.Timestamp(updated_at).timestamp()
                    if (time.time() - updated_ts) <= ttl_sec:
                        return current
                except Exception:
                    pass
        try:
            account = self.trade_client.validate_private_access()
        except ValueError as exc:
            return self._set_live_order_status(
                state="error",
                mode="LIVE",
                reason="config_error",
                message=str(exc),
            )
        except BinanceApiError as exc:
            return self._set_live_order_status(
                state="error",
                mode="LIVE",
                reason="binance_auth_error",
                message=str(exc),
                status_code=exc.status_code,
                binance_code=self._extract_binance_error_code(exc.payload),
            )
        except Exception as exc:
            return self._set_live_order_status(
                state="error",
                mode="LIVE",
                reason="probe_failed",
                message=str(exc),
            )
        return self._set_live_order_status(
            state="ok",
            mode="LIVE",
            reason="ready",
            message="Binance futures private API reachable",
            meta=account,
        )

    def _maybe_execute_paper_order(self, row: dict[str, Any]) -> None:
        if not settings.pump_hunter_paper_trade_enabled:
            return
        score = float(row.get("effective_score") or row.get("pump_score") or 0.0)
        if score < float(settings.pump_hunter_paper_min_score):
            return
        symbol = str(row.get("symbol") or "").strip()
        if not symbol:
            return
        if not self._is_binance_symbol(symbol):
            logger.info("Pump hunter paper order skipped: symbol=%s reason=not_listed_on_binance", symbol)
            return
        side, entry, take_profit, stop_loss = self._derive_trade_plan(row)
        if entry <= 0 or take_profit <= 0 or stop_loss <= 0:
            logger.info("Pump hunter paper order skipped: symbol=%s side=%s reason=invalid_trade_plan", symbol, side)
            return
        order_key = f"pump_paper_order:{symbol}:{side}:touch"
        cooldown_sec = max(60, int(settings.pump_hunter_paper_signal_cooldown_sec))
        if not self._claim_alert_slot(order_key, ttl_sec=cooldown_sec):
            return
        try:
            from app.api.paper_trades import paper_trade_api
            from app.models.paper_trades import PaperMarketOpenRequest

            probability = _clamp(score / 100.0, 0.0, 1.0)
            request = PaperMarketOpenRequest(
                symbol=symbol,
                side=side,
                signal_win_probability=probability,
                effective_win_probability=probability,
                repo_scope="main",
                entry_type="PUMP_ENTRY_TOUCH",
                entry_price=entry,
                take_profit=take_profit,
                stop_loss=stop_loss,
                reference_win_symbol=symbol,
                entry_point_score=score,
                order_usdt=float(settings.paper_trade_order_usdt),
                margin_usdt=float(settings.paper_trade_margin_usdt),
                leverage=max(1, int(settings.pump_hunter_live_leverage or settings.paper_trade_leverage or 1)),
                entry_snapshot={
                    "source": str(row.get("source") or "pump_hunter_auto_paper"),
                    "symbol": symbol,
                    "side": side,
                    "stage": str(row.get("stage") or "").upper() or None,
                    "signal_label": str(row.get("signal_label") or "").upper() or None,
                    "signal_type": str(row.get("signal_type") or ("POST_SWEEP_SHORT" if side == "SHORT" else "LONG_BUILDUP_BREAKOUT")).upper(),
                    "execution_mode": str(row.get("execution_mode") or "AUTO_BG").upper(),
                    "setup_quality": str(row.get("setup_quality") or "").upper() or None,
                    "planned_entry_price": entry,
                    "market_entry_price": float(row.get("mark_price") or 0.0),
                    "take_profit_price": take_profit,
                    "stop_loss_price": stop_loss,
                    "effective_score": score,
                    "pump_score": float(row.get("pump_score") or score),
                    "volume_ratio_15m": float(row.get("volume_ratio_15m") or 0.0),
                    "volume_ratio_5m": float(row.get("volume_ratio_5m") or 0.0),
                    "rejection_score": float(row.get("rejection_score") or 0.0),
                    "rr_ratio": float(row.get("rr_ratio") or 0.0),
                    "est_liq_target_price": float(row.get("est_liq_target_price") or 0.0),
                    "est_liq_distance_pct": float(row.get("est_liq_distance_pct") or 0.0),
                    "above_ema_stack": bool(row.get("above_ema_stack")),
                    "long_buildup_early_active": bool(row.get("long_buildup_early_active")),
                    "kill_short_active": bool(row.get("kill_short_active")),
                    "swept_recently": bool(row.get("swept_recently")),
                    "entry_ready": bool(row.get("entry_ready")),
                },
            )
            trade = asyncio.run(paper_trade_api.market_open(request))
            self._set_cache(order_key, {"trade_id": int(trade.id)}, ttl_sec=cooldown_sec)
            logger.info(
                "Pump hunter paper order opened: symbol=%s side=%s trade_id=%s score=%.1f entry=%.8f tp=%.8f sl=%.8f",
                symbol,
                side,
                int(trade.id),
                score,
                entry,
                take_profit,
                stop_loss,
            )
        except Exception as exc:
            self._release_alert_slot(order_key)
            status_code = getattr(exc, "status_code", None)
            detail = getattr(exc, "detail", None)
            if status_code is not None:
                logger.info(
                    "Pump hunter paper order skipped: symbol=%s side=%s status=%s reason=%s",
                    symbol,
                    side,
                    status_code,
                    detail or str(exc),
                )
                return
            logger.exception("Pump hunter paper order failed: symbol=%s side=%s", symbol, side)

    def _attach_runtime_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload["paper_trade_enabled"] = bool(settings.pump_hunter_paper_trade_enabled)
        payload["live_order_status"] = self.get_live_order_status()
        return payload

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
        side, entry, take_profit, stop_loss = self._derive_trade_plan(row)
        notes = row.get("notes") or []
        notes_text = " | ".join(str(item) for item in notes[:3]) if isinstance(notes, list) and notes else "-"
        leverage = 5
        margin_usdt = 20.0
        tp_pct, sl_pct = self._calc_trade_pct_metrics(
            side=side,
            entry=entry,
            take_profit=take_profit,
            stop_loss=stop_loss,
            leverage=leverage,
        )
        is_high_tp = tp_pct >= PUMP_HUNTER_HIGHLIGHT_TP_PCT
        signal_label = str(row.get("signal_label") or "WATCH").upper()
        alert_scope = "high_tp" if is_high_tp else signal_label.lower()
        alert_key = f"pump_discord:{symbol}:{side}:{alert_scope}"
        cooldown_sec = max(60, int(settings.pump_hunter_discord_alert_cooldown_sec))
        if not self._claim_alert_slot(alert_key, ttl_sec=cooldown_sec):
            return
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
            self._set_cache(alert_key, True, ttl_sec=cooldown_sec)
            logger.info(
                "Pump hunter Discord alert sent: symbol=%s signal=%s stage=%s score=%.1f",
                symbol,
                signal_label,
                str(row.get("stage") or "-").upper(),
                score,
            )
        except Exception:
            self._release_alert_slot(alert_key)
            logger.exception(
                "Pump hunter Discord alert failed: symbol=%s signal=%s stage=%s score=%.1f",
                symbol,
                signal_label,
                str(row.get("stage") or "-").upper(),
                score,
            )

    @staticmethod
    def _calc_entry_distance_pct(*, mark_price: float, entry_price: float) -> float:
        if mark_price <= 0 or entry_price <= 0:
            return 999999.0
        return abs((mark_price - entry_price) / entry_price) * 100.0

    def _should_use_market_entry(self, row: dict[str, Any], *, entry: float) -> bool:
        volume_ratio_15m = float(row.get("volume_ratio_15m") or 0.0)
        if volume_ratio_15m < float(settings.pump_hunter_live_market_entry_volume_ratio_15m_threshold):
            return False
        if settings.pump_hunter_live_market_entry_require_above_ema_stack and not bool(row.get("above_ema_stack")):
            return False
        mark_price = float(row.get("mark_price") or 0.0)
        distance_pct = self._calc_entry_distance_pct(mark_price=mark_price, entry_price=entry)
        if distance_pct > float(settings.pump_hunter_live_market_entry_max_distance_pct):
            return False
        return True

    def _submit_binance_entry_order(
        self,
        row: dict[str, Any],
        *,
        test_mode: bool,
        order_usdt: float,
        leverage: int,
        margin_type: str,
    ) -> dict[str, Any]:
        symbol = str(row.get("symbol") or "").strip()
        side, entry, _take_profit, _stop_loss = self._derive_trade_plan(row)
        use_market = self._should_use_market_entry(row, entry=entry)
        if use_market:
            result = self.trade_client.place_market_order(
                symbol=symbol,
                side=side,
                order_usdt=order_usdt,
                leverage=leverage,
                margin_type=margin_type,
                test_mode=test_mode,
            )
        else:
            result = self.trade_client.place_limit_order(
                symbol=symbol,
                side=side,
                entry_price=entry,
                order_usdt=order_usdt,
                leverage=leverage,
                margin_type=margin_type,
                test_mode=test_mode,
            )
        result["entry_order_type"] = "MARKET" if use_market else "LIMIT"
        result["market_entry_distance_pct"] = self._calc_entry_distance_pct(
            mark_price=float(row.get("mark_price") or 0.0),
            entry_price=entry,
        )
        return result

    def _maybe_execute_live_order(self, row: dict[str, Any]) -> None:
        if not settings.pump_hunter_live_trade_enabled:
            return
        live_status = self.get_live_order_status()
        if str(live_status.get("reason") or "").lower() in {"binance_auth_error", "missing_credentials", "config_error"}:
            return
        score = float(row.get("effective_score") or row.get("pump_score") or 0.0)
        if score < float(settings.pump_hunter_live_min_score):
            return
        symbol = str(row.get("symbol") or "").strip()
        if symbol and not self._is_binance_symbol(symbol):
            logger.info("Pump hunter Binance order skipped: symbol=%s reason=not_listed_on_binance", symbol)
            return
        leverage = max(1, int(settings.pump_hunter_live_leverage))
        try:
            side, entry, take_profit, stop_loss, tp_pct = self._validate_live_order_tp_requirement(
                row,
                leverage=leverage,
            )
        except ValueError as exc:
            if symbol:
                logger.info("Pump hunter Binance order skipped: symbol=%s reason=%s", symbol, str(exc))
            return
        if not symbol:
            return
        order_key = f"pump_live_order:{symbol}:{side}:high_tp"
        cooldown_sec = max(60, int(settings.pump_hunter_live_signal_cooldown_sec))
        if not self._claim_alert_slot(order_key, ttl_sec=cooldown_sec):
            return
        try:
            result = self._submit_binance_entry_order(
                row,
                test_mode=bool(settings.pump_hunter_live_order_test_mode),
                order_usdt=float(settings.pump_hunter_live_order_usdt),
                leverage=leverage,
                margin_type=str(settings.pump_hunter_live_margin_type or "ISOLATED").upper(),
            )
            result = self._attach_signal_to_order_result(result, row)
            self._set_cache(order_key, result, ttl_sec=cooldown_sec)
            self._track_live_order(result)
            self._send_order_success_discord_alert(result)
            logger.info(
                "Pump hunter Binance order submitted: symbol=%s side=%s type=%s test_mode=%s qty=%s entry=%s",
                symbol,
                side,
                result.get("entry_order_type"),
                bool(settings.pump_hunter_live_order_test_mode),
                result.get("quantity"),
                result.get("entry_price"),
            )
            self._set_live_order_status(
                state="ok",
                mode="TEST" if bool(settings.pump_hunter_live_order_test_mode) else "LIVE",
                reason="order_submitted",
                message=f"Last Pump Hunter {'test' if bool(settings.pump_hunter_live_order_test_mode) else 'live'} order submitted successfully",
            )
        except BinanceApiError as exc:
            self._release_alert_slot(order_key)
            self._set_live_order_status(
                state="error",
                mode="LIVE",
                reason="binance_auth_error" if self._extract_binance_error_code(exc.payload) == -2015 else "binance_api_error",
                message=str(exc),
                status_code=exc.status_code,
                binance_code=self._extract_binance_error_code(exc.payload),
            )
            logger.exception("Pump hunter Binance order failed: symbol=%s side=%s", symbol, side)
        except Exception:
            self._release_alert_slot(order_key)
            logger.exception("Pump hunter Binance order failed: symbol=%s side=%s", symbol, side)

    def submit_binance_order(
        self,
        row: dict[str, Any],
        *,
        test_mode: bool = True,
        order_usdt: float | None = None,
        leverage: int | None = None,
        margin_type: str | None = None,
    ) -> dict[str, Any]:
        effective_leverage = max(1, int(leverage or settings.pump_hunter_live_leverage))
        self._validate_live_order_tp_requirement(
            row,
            leverage=effective_leverage,
        )
        result = self._submit_binance_entry_order(
            row,
            test_mode=bool(test_mode),
            order_usdt=float(order_usdt or settings.pump_hunter_live_order_usdt),
            leverage=effective_leverage,
            margin_type=str(margin_type or settings.pump_hunter_live_margin_type or "ISOLATED").upper(),
        )
        result = self._attach_signal_to_order_result(result, row)
        self._track_live_order(result)
        self._send_order_success_discord_alert(result)
        return result

    def _attach_signal_to_order_result(self, result: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
        side, entry, take_profit, stop_loss = self._derive_trade_plan(row)
        symbol = str(row.get("symbol") or "").strip()
        score = float(row.get("effective_score") or row.get("pump_score") or 0.0)
        entry_order_type = str(result.get("entry_order_type") or result.get("order_type") or "LIMIT").upper()
        signal_entry = float(result.get("entry_price") or entry or 0.0) if entry_order_type == "MARKET" else entry
        leverage = int(result.get("leverage") or settings.pump_hunter_live_leverage or 1)
        signal_tp = self._normalize_live_take_profit(
            side=side,
            entry=signal_entry,
            take_profit=take_profit,
            leverage=leverage,
        )
        result["signal"] = {
            "symbol": symbol,
            "side": side,
            "score": score,
            "signal_label": str(row.get("signal_label") or "").upper(),
            "stage": str(row.get("stage") or "").upper(),
            "entry": signal_entry,
            "tp": signal_tp,
            "sl": stop_loss,
            "entry_order_type": entry_order_type,
            "market_entry_distance_pct": float(result.get("market_entry_distance_pct") or 0.0),
        }
        return result

    def _send_order_success_discord_alert(self, result: dict[str, Any]) -> None:
        webhook_url = str(settings.pump_hunter_order_discord_webhook_url or "").strip()
        if not webhook_url:
            return
        signal = result.get("signal") if isinstance(result.get("signal"), dict) else {}
        symbol = str(result.get("symbol") or signal.get("symbol") or "").strip()
        side = str(signal.get("side") or result.get("side") or "").upper()
        test_mode = bool(result.get("test_mode"))
        mode_label = "TEST ORDER" if test_mode else "LIVE ORDER"
        score = float(signal.get("score") or 0.0)
        tp_pct, sl_pct = self._calc_trade_pct_metrics(
            side=side,
            entry=float(signal.get("entry") or 0.0),
            take_profit=float(signal.get("tp") or 0.0),
            stop_loss=float(signal.get("sl") or 0.0),
            leverage=int(result.get("leverage") or settings.pump_hunter_live_leverage or 1),
        )
        color = 0x3498DB if test_mode else 0x2ECC71
        payload = json.dumps(
            {
                "username": "Pump Hunter Executor",
                "allowed_mentions": {"parse": []},
                "embeds": [
                    {
                        "title": f"{mode_label} placed: {symbol}",
                        "color": color,
                        "fields": [
                            {"name": "Mode", "value": mode_label, "inline": True},
                            {"name": "Side", "value": side or "-", "inline": True},
                            {"name": "Score", "value": f"{score:.1f}", "inline": True},
                            {"name": "Signal", "value": f"{str(signal.get('signal_label') or '-').upper()}/{str(signal.get('stage') or '-').upper()}", "inline": True},
                            {"name": "Entry", "value": self._format_number(float(signal.get("entry") or result.get("entry_price") or 0.0), 8), "inline": True},
                            {"name": "Quantity", "value": str(result.get("quantity") or "-"), "inline": True},
                            {"name": "TP", "value": f"{self._format_number(float(signal.get('tp') or 0.0), 8)} ({self._format_signed_pct(tp_pct)})", "inline": True},
                            {"name": "SL", "value": f"{self._format_number(float(signal.get('sl') or 0.0), 8)} ({self._format_signed_pct(sl_pct)})", "inline": True},
                            {"name": "Margin", "value": f"{float(result.get('margin_usdt') or 0.0):.2f}", "inline": True},
                            {"name": "Notional", "value": f"{float(result.get('notional_usdt') or 0.0):.2f}", "inline": True},
                            {"name": "Leverage", "value": f"{int(result.get('leverage') or 0)}x", "inline": True},
                            {"name": "Margin Type", "value": str(result.get("margin_type") or "-"), "inline": True},
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
                "User-Agent": "curl/8.7.1",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            self._post_webhook(request)
        except Exception:
            logger.exception("Pump hunter order Discord alert failed: symbol=%s mode=%s", symbol, mode_label)

    def _send_order_cancel_discord_alert(self, tracked: dict[str, Any], cancel_resp: dict[str, Any]) -> None:
        webhook_url = str(settings.pump_hunter_order_discord_webhook_url or "").strip()
        if not webhook_url:
            return
        symbol = str(tracked.get("symbol") or "").strip()
        side = str(tracked.get("side") or "").upper()
        score = float(tracked.get("score") or 0.0)
        placed_at_text = str(tracked.get("placed_at_text") or "-")
        age_minutes = max(0.0, float(tracked.get("age_minutes") or 0.0))
        payload = json.dumps(
            {
                "username": "Pump Hunter Executor",
                "allowed_mentions": {"parse": []},
                "embeds": [
                    {
                        "title": f"LIVE ORDER canceled: {symbol}",
                        "color": 0xE67E22,
                        "fields": [
                            {"name": "Mode", "value": "AUTO CANCEL", "inline": True},
                            {"name": "Side", "value": side or "-", "inline": True},
                            {"name": "Score", "value": f"{score:.1f}", "inline": True},
                            {
                                "name": "Signal",
                                "value": f"{str(tracked.get('signal_label') or '-').upper()}/{str(tracked.get('stage') or '-').upper()}",
                                "inline": True,
                            },
                            {"name": "Entry", "value": self._format_number(float(tracked.get("entry_price") or 0.0), 8), "inline": True},
                            {"name": "Quantity", "value": str(tracked.get("quantity") or "-"), "inline": True},
                            {"name": "Age", "value": f"{age_minutes:.1f} min", "inline": True},
                            {"name": "Order ID", "value": str(tracked.get("order_id") or "-"), "inline": True},
                            {"name": "Client ID", "value": str(tracked.get("client_order_id") or "-"), "inline": True},
                            {"name": "Placed At", "value": placed_at_text, "inline": False},
                            {"name": "Cancel Status", "value": str(cancel_resp.get("status") or "CANCELED"), "inline": True},
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
                "User-Agent": "curl/8.7.1",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            self._post_webhook(request)
        except Exception:
            logger.exception("Pump hunter order cancel Discord alert failed: symbol=%s", symbol)

    def _send_tp_success_discord_alert(
        self,
        tracked: dict[str, Any],
        tp_result: dict[str, Any],
        *,
        moved_to_entry: bool = False,
        trigger_pnl_pct: float | None = None,
        mark_price: float | None = None,
    ) -> None:
        webhook_url = str(settings.pump_hunter_order_discord_webhook_url or "").strip()
        if not webhook_url:
            return
        symbol = str(tracked.get("symbol") or "").strip()
        side = str(tracked.get("side") or "").upper()
        score = float(tracked.get("score") or 0.0)
        mode_label = "TP MOVED TO ENTRY" if moved_to_entry else "TP ORDER"
        color = 0x16A085 if moved_to_entry else 0x1ABC9C
        fields: list[dict[str, Any]] = [
            {"name": "Mode", "value": mode_label, "inline": True},
            {"name": "Side", "value": side or "-", "inline": True},
            {"name": "Score", "value": f"{score:.1f}", "inline": True},
            {
                "name": "Signal",
                "value": f"{str(tracked.get('signal_label') or '-').upper()}/{str(tracked.get('stage') or '-').upper()}",
                "inline": True,
            },
            {"name": "Entry", "value": self._format_number(float(tracked.get("entry_price") or 0.0), 8), "inline": True},
            {"name": "TP", "value": self._format_number(float(tp_result.get("tp_price") or tracked.get("tp_price") or 0.0), 8), "inline": True},
            {"name": "Filled Qty", "value": str(tracked.get("filled_qty") or tracked.get("quantity") or "-"), "inline": True},
            {"name": "TP Order Qty", "value": str(tp_result.get("quantity") or "-"), "inline": True},
            {"name": "TP Order Side", "value": str(tp_result.get("side") or "-"), "inline": True},
        ]
        if trigger_pnl_pct is not None:
            fields.append({"name": "PnL Trigger", "value": self._format_signed_pct(trigger_pnl_pct), "inline": True})
        if mark_price is not None and mark_price > 0:
            fields.append({"name": "Mark", "value": self._format_number(mark_price, 8), "inline": True})
        payload = json.dumps(
            {
                "username": "Pump Hunter Executor",
                "allowed_mentions": {"parse": []},
                "embeds": [
                    {
                        "title": f"{mode_label}: {symbol}",
                        "color": color,
                        "fields": fields,
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
                "User-Agent": "curl/8.7.1",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            self._post_webhook(request)
        except Exception:
            logger.exception("Pump hunter TP Discord alert failed: symbol=%s moved=%s", symbol, moved_to_entry)

    def _send_sl_success_discord_alert(
        self,
        tracked: dict[str, Any],
        sl_result: dict[str, Any],
        *,
        moved_to_entry: bool = False,
        trigger_pnl_pct: float | None = None,
        mark_price: float | None = None,
    ) -> None:
        webhook_url = str(settings.pump_hunter_order_discord_webhook_url or "").strip()
        if not webhook_url:
            return
        symbol = str(tracked.get("symbol") or "").strip()
        side = str(tracked.get("side") or "").upper()
        score = float(tracked.get("score") or 0.0)
        mode_label = "SL MOVED TO ENTRY" if moved_to_entry else "SL ORDER"
        color = 0xF39C12 if moved_to_entry else 0xE74C3C
        fields: list[dict[str, Any]] = [
            {"name": "Mode", "value": mode_label, "inline": True},
            {"name": "Side", "value": side or "-", "inline": True},
            {"name": "Score", "value": f"{score:.1f}", "inline": True},
            {
                "name": "Signal",
                "value": f"{str(tracked.get('signal_label') or '-').upper()}/{str(tracked.get('stage') or '-').upper()}",
                "inline": True,
            },
            {"name": "Entry", "value": self._format_number(float(tracked.get("entry_price") or 0.0), 8), "inline": True},
            {"name": "SL", "value": self._format_number(float(sl_result.get("stop_price") or tracked.get("sl_price") or 0.0), 8), "inline": True},
        ]
        if trigger_pnl_pct is not None:
            fields.append({"name": "PnL Trigger", "value": self._format_signed_pct(trigger_pnl_pct), "inline": True})
        if mark_price is not None and mark_price > 0:
            fields.append({"name": "Mark", "value": self._format_number(mark_price, 8), "inline": True})
        payload = json.dumps(
            {
                "username": "Pump Hunter Executor",
                "allowed_mentions": {"parse": []},
                "embeds": [
                    {
                        "title": f"{mode_label}: {symbol}",
                        "color": color,
                        "fields": fields,
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
                "User-Agent": "curl/8.7.1",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            self._post_webhook(request)
        except Exception:
            logger.exception("Pump hunter SL Discord alert failed: symbol=%s moved=%s", symbol, moved_to_entry)

    @staticmethod
    def _build_live_order_registry_key(symbol: str, order_id: Any, client_order_id: str | None) -> str | None:
        symbol_text = str(symbol or "").strip()
        if not symbol_text:
            return None
        if order_id is not None and str(order_id).strip():
            return f"{symbol_text}:{int(order_id)}"
        if client_order_id and str(client_order_id).strip():
            return f"{symbol_text}:{str(client_order_id).strip()}"
        return None

    def _track_live_order(self, result: dict[str, Any]) -> None:
        if bool(result.get("test_mode")):
            return
        exchange = result.get("exchange_response") if isinstance(result.get("exchange_response"), dict) else {}
        symbol = str(result.get("symbol") or "").strip()
        order_id = exchange.get("orderId")
        client_order_id = exchange.get("clientOrderId") or exchange.get("origClientOrderId")
        key = self._build_live_order_registry_key(symbol, order_id, client_order_id)
        if not key:
            return
        signal = result.get("signal") if isinstance(result.get("signal"), dict) else {}
        placed_at_ts = time.time()
        transact_time = exchange.get("transactTime")
        try:
            if transact_time is not None:
                placed_at_ts = float(transact_time) / 1000.0
        except Exception:
            placed_at_ts = time.time()
        tracked = {
            "symbol": symbol,
            "order_id": int(order_id) if order_id is not None and str(order_id).strip() else None,
            "client_order_id": str(client_order_id).strip() if client_order_id else None,
            "side": str(signal.get("side") or result.get("side") or "").upper(),
            "entry_order_type": str(signal.get("entry_order_type") or result.get("entry_order_type") or result.get("order_type") or exchange.get("type") or "LIMIT").upper(),
            "score": float(signal.get("score") or 0.0),
            "signal_label": str(signal.get("signal_label") or "").upper(),
            "stage": str(signal.get("stage") or "").upper(),
            "entry_price": float(signal.get("entry") or result.get("entry_price") or 0.0),
            "tp_price": float(signal.get("tp") or 0.0),
            "sl_price": float(signal.get("sl") or 0.0),
            "quantity": str(result.get("quantity") or "-"),
            "leverage": int(result.get("leverage") or settings.pump_hunter_live_leverage or 1),
            "margin_type": str(result.get("margin_type") or settings.pump_hunter_live_margin_type or "ISOLATED").upper(),
            "placed_at_ts": placed_at_ts,
            "placed_at_text": time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime(placed_at_ts)),
            "entry_filled": False,
            "filled_qty": "0",
            "tp_order_placed": False,
            "tp_order_id": None,
            "tp_client_order_id": None,
            "tp_moved_to_entry": False,
            "sl_order_placed": False,
            "sl_order_id": None,
            "sl_client_order_id": None,
            "sl_moved_to_entry": False,
        }
        exchange_status = str(exchange.get("status") or "").upper()
        if exchange_status == "FILLED":
            tracked["entry_filled"] = True
            tracked["filled_qty"] = exchange.get("executedQty") or tracked.get("quantity") or "0"
            try:
                tracked["entry_price"] = float(exchange.get("avgPrice") or exchange.get("price") or tracked.get("entry_price") or 0.0)
            except Exception:
                pass
        with self._live_order_lock:
            self._live_order_registry[key] = tracked

    def list_live_orders(self) -> list[dict[str, Any]]:
        now_ts = time.time()
        with self._live_order_lock:
            snapshot = [(key, dict(value)) for key, value in self._live_order_registry.items()]

        items: list[dict[str, Any]] = []
        for key, tracked in snapshot:
            placed_at_ts = float(tracked.get("placed_at_ts") or now_ts)
            age_minutes = max(0.0, (now_ts - placed_at_ts) / 60.0)
            items.append(
                {
                    "key": key,
                    "symbol": str(tracked.get("symbol") or "").strip(),
                    "side": str(tracked.get("side") or "").upper(),
                    "score": float(tracked.get("score") or 0.0),
                    "signal_label": str(tracked.get("signal_label") or "").upper() or None,
                    "stage": str(tracked.get("stage") or "").upper() or None,
                    "entry_order_type": str(tracked.get("entry_order_type") or "").upper() or None,
                    "entry_price": _safe_float(tracked.get("entry_price")),
                    "tp_price": _safe_float(tracked.get("tp_price")),
                    "sl_price": _safe_float(tracked.get("sl_price")),
                    "quantity": str(tracked.get("quantity") or "") or None,
                    "filled_qty": str(tracked.get("filled_qty") or "") or None,
                    "leverage": int(tracked.get("leverage") or 0) or None,
                    "margin_type": str(tracked.get("margin_type") or "").upper() or None,
                    "placed_at_text": str(tracked.get("placed_at_text") or "") or None,
                    "age_minutes": age_minutes,
                    "entry_filled": bool(tracked.get("entry_filled")),
                    "tp_order_placed": bool(tracked.get("tp_order_placed")),
                    "tp_moved_to_entry": bool(tracked.get("tp_moved_to_entry")),
                    "sl_order_placed": bool(tracked.get("sl_order_placed")),
                    "sl_moved_to_entry": bool(tracked.get("sl_moved_to_entry")),
                }
            )

        items.sort(key=lambda item: float(item.get("age_minutes") or 0.0), reverse=True)
        return items

    @staticmethod
    def _register_child_order(tracked: dict[str, Any], prefix: str, result: dict[str, Any]) -> None:
        exchange = result.get("exchange_response") if isinstance(result.get("exchange_response"), dict) else {}
        order_id = exchange.get("orderId")
        client_order_id = exchange.get("clientOrderId") or exchange.get("origClientOrderId")
        tracked[f"{prefix}_order_placed"] = True
        tracked[f"{prefix}_order_id"] = int(order_id) if order_id is not None and str(order_id).strip() else None
        tracked[f"{prefix}_client_order_id"] = str(client_order_id).strip() if client_order_id else None

    @staticmethod
    def _calc_live_pnl_pct(*, side: str, entry_price: float, mark_price: float, leverage: int) -> float:
        if entry_price <= 0 or mark_price <= 0 or leverage <= 0:
            return 0.0
        side_text = str(side or "").upper()
        if side_text == "LONG":
            return ((mark_price - entry_price) / entry_price) * leverage * 100.0
        return ((entry_price - mark_price) / entry_price) * leverage * 100.0

    def _maybe_close_profitable_timeout_position(self, tracked: dict[str, Any], *, now_ts: float) -> bool:
        if bool(settings.pump_hunter_live_order_test_mode):
            return False
        if not bool(tracked.get("entry_filled")):
            return False
        timeout_hours = float(settings.pump_hunter_live_profit_timeout_hours)
        if timeout_hours <= 0:
            return False
        placed_at_ts = float(tracked.get("placed_at_ts") or now_ts)
        age_sec = max(0.0, now_ts - placed_at_ts)
        if age_sec < (timeout_hours * 3600.0):
            return False
        symbol = str(tracked.get("symbol") or "").strip()
        side = str(tracked.get("side") or "").upper()
        entry_price = float(tracked.get("entry_price") or 0.0)
        leverage = max(1, int(tracked.get("leverage") or settings.pump_hunter_live_leverage or 1))
        executed_qty = float(tracked.get("filled_qty") or 0.0)
        if not symbol or entry_price <= 0 or executed_qty <= 0:
            return False
        mark_price = float(self.trade_client.get_mark_price(symbol))
        pnl_pct = self._calc_live_pnl_pct(
            side=side,
            entry_price=entry_price,
            mark_price=mark_price,
            leverage=leverage,
        )
        if pnl_pct <= float(settings.pump_hunter_live_profit_timeout_min_pnl_pct):
            return False
        tp_status, _ = self._get_tracked_child_order_status(tracked, "tp")
        sl_status, _ = self._get_tracked_child_order_status(tracked, "sl")
        if tp_status == "FILLED" or sl_status == "FILLED":
            return False
        if tp_status in {"NEW", "PARTIALLY_FILLED"}:
            self._cancel_tracked_child_order(tracked, "tp")
        if sl_status in {"NEW", "PARTIALLY_FILLED"}:
            self._cancel_tracked_child_order(tracked, "sl")
        close_result = self.trade_client.place_close_position_market_order(
            symbol=symbol,
            position_side=side,
            quantity=executed_qty,
            test_mode=False,
        )
        tracked["age_minutes"] = age_sec / 60.0
        logger.info(
            "Pump hunter profitable timeout close: symbol=%s side=%s age_min=%.1f pnl_pct=%.2f mark=%s",
            symbol,
            side,
            tracked["age_minutes"],
            pnl_pct,
            mark_price,
        )
        return True

    def _maybe_place_sl_for_tracked_order(self, tracked: dict[str, Any]) -> bool:
        if not settings.pump_hunter_live_place_sl_on_fill_enabled:
            return False
        if bool(settings.pump_hunter_live_order_test_mode):
            return False
        if bool(tracked.get("sl_order_placed")):
            return True
        symbol = str(tracked.get("symbol") or "").strip()
        stop_price = float(tracked.get("sl_price") or 0.0)
        executed_qty = float(tracked.get("filled_qty") or 0.0)
        if not symbol or stop_price <= 0 or executed_qty <= 0:
            return False
        sl_result = self.trade_client.place_close_position_sl_order(
            symbol=symbol,
            position_side=str(tracked.get("side") or "").upper(),
            stop_price=stop_price,
            quantity=executed_qty,
            test_mode=False,
        )
        self._register_child_order(tracked, "sl", sl_result)
        self._send_sl_success_discord_alert(tracked, sl_result, moved_to_entry=False)
        logger.info(
            "Pump hunter SL order submitted: symbol=%s side=%s stop=%s",
            symbol,
            tracked.get("side"),
            sl_result.get("stop_price"),
        )
        return True

    def _get_tracked_child_order_status(self, tracked: dict[str, Any], prefix: str) -> tuple[str, dict[str, Any]]:
        if not bool(tracked.get(f"{prefix}_order_placed")):
            return "", {}
        symbol = str(tracked.get("symbol") or "").strip()
        order_id = tracked.get(f"{prefix}_order_id")
        client_order_id = tracked.get(f"{prefix}_client_order_id")
        if not symbol:
            return "", {}
        status_resp = self.trade_client.get_order_status(
            symbol=symbol,
            order_id=order_id,
            client_order_id=client_order_id,
        )
        return str(status_resp.get("status") or "").upper(), status_resp

    def _cancel_tracked_child_order(self, tracked: dict[str, Any], prefix: str) -> bool:
        if not bool(tracked.get(f"{prefix}_order_placed")):
            return False
        symbol = str(tracked.get("symbol") or "").strip()
        order_id = tracked.get(f"{prefix}_order_id")
        client_order_id = tracked.get(f"{prefix}_client_order_id")
        if not symbol:
            return False
        self.trade_client.cancel_order(
            symbol=symbol,
            order_id=order_id,
            client_order_id=client_order_id,
        )
        tracked[f"{prefix}_order_placed"] = False
        tracked[f"{prefix}_order_id"] = None
        tracked[f"{prefix}_client_order_id"] = None
        return True

    def _maybe_place_tp_for_tracked_order(self, tracked: dict[str, Any], status_resp: dict[str, Any]) -> bool:
        if not settings.pump_hunter_live_place_tp_on_fill_enabled:
            return False
        if bool(settings.pump_hunter_live_order_test_mode):
            return False
        if bool(tracked.get("tp_order_placed")):
            return True
        symbol = str(tracked.get("symbol") or "").strip()
        tp_price = float(tracked.get("tp_price") or 0.0)
        if not symbol or tp_price <= 0:
            return False
        executed_qty = float(status_resp.get("executedQty") or 0.0)
        if executed_qty <= 0:
            return False
        tp_result = self.trade_client.place_reduce_only_tp_order(
            symbol=symbol,
            position_side=str(tracked.get("side") or "").upper(),
            tp_price=tp_price,
            quantity=executed_qty,
            test_mode=False,
        )
        self._register_child_order(tracked, "tp", tp_result)
        tracked["filled_qty"] = tp_result.get("quantity") or str(executed_qty)
        self._send_tp_success_discord_alert(tracked, tp_result)
        logger.info(
            "Pump hunter TP order submitted: symbol=%s side=%s qty=%s tp=%s",
            symbol,
            tracked.get("side"),
            tp_result.get("quantity"),
            tp_result.get("tp_price"),
        )
        return True

    def _maybe_move_tp_to_entry_for_tracked_order(self, tracked: dict[str, Any]) -> bool:
        if not settings.pump_hunter_live_move_tp_to_entry_enabled:
            return False
        if bool(settings.pump_hunter_live_order_test_mode):
            return False
        if not bool(tracked.get("entry_filled")):
            return False
        if not bool(tracked.get("tp_order_placed")):
            return False
        if bool(tracked.get("tp_moved_to_entry")):
            return False
        symbol = str(tracked.get("symbol") or "").strip()
        side = str(tracked.get("side") or "").upper()
        entry_price = float(tracked.get("entry_price") or 0.0)
        leverage = max(1, int(tracked.get("leverage") or settings.pump_hunter_live_leverage or 1))
        executed_qty = float(tracked.get("filled_qty") or 0.0)
        if not symbol or entry_price <= 0 or executed_qty <= 0:
            return False
        mark_price = float(self.trade_client.get_mark_price(symbol))
        pnl_pct = self._calc_live_pnl_pct(
            side=side,
            entry_price=entry_price,
            mark_price=mark_price,
            leverage=leverage,
        )
        if pnl_pct > float(settings.pump_hunter_live_move_tp_to_entry_pnl_pct):
            return False
        self._cancel_tracked_child_order(tracked, "tp")
        tp_result = self.trade_client.place_reduce_only_tp_order(
            symbol=symbol,
            position_side=side,
            tp_price=entry_price,
            quantity=executed_qty,
            test_mode=False,
        )
        self._register_child_order(tracked, "tp", tp_result)
        tracked["tp_price"] = entry_price
        tracked["tp_moved_to_entry"] = True
        self._send_tp_success_discord_alert(
            tracked,
            tp_result,
            moved_to_entry=True,
            trigger_pnl_pct=pnl_pct,
            mark_price=mark_price,
        )
        logger.info(
            "Pump hunter TP moved to entry: symbol=%s side=%s entry=%s mark=%s pnl_pct=%.2f",
            symbol,
            side,
            entry_price,
            mark_price,
            pnl_pct,
        )
        return True

    def _maybe_move_sl_to_entry_for_tracked_order(self, tracked: dict[str, Any]) -> bool:
        if not settings.pump_hunter_live_move_sl_to_entry_enabled:
            return False
        if bool(settings.pump_hunter_live_order_test_mode):
            return False
        if not bool(tracked.get("entry_filled")):
            return False
        if not bool(tracked.get("sl_order_placed")):
            return False
        if bool(tracked.get("sl_moved_to_entry")):
            return False
        symbol = str(tracked.get("symbol") or "").strip()
        side = str(tracked.get("side") or "").upper()
        entry_price = float(tracked.get("entry_price") or 0.0)
        leverage = max(1, int(tracked.get("leverage") or settings.pump_hunter_live_leverage or 1))
        if not symbol or entry_price <= 0:
            return False
        mark_price = float(self.trade_client.get_mark_price(symbol))
        pnl_pct = self._calc_live_pnl_pct(
            side=side,
            entry_price=entry_price,
            mark_price=mark_price,
            leverage=leverage,
        )
        if pnl_pct < float(settings.pump_hunter_live_move_sl_to_entry_pnl_pct):
            return False
        executed_qty = float(tracked.get("filled_qty") or 0.0)
        if executed_qty <= 0:
            return False
        self._cancel_tracked_child_order(tracked, "sl")
        sl_result = self.trade_client.place_close_position_sl_order(
            symbol=symbol,
            position_side=side,
            stop_price=entry_price,
            quantity=executed_qty,
            test_mode=False,
        )
        self._register_child_order(tracked, "sl", sl_result)
        tracked["sl_price"] = entry_price
        tracked["sl_moved_to_entry"] = True
        self._send_sl_success_discord_alert(
            tracked,
            sl_result,
            moved_to_entry=True,
            trigger_pnl_pct=pnl_pct,
            mark_price=mark_price,
        )
        logger.info(
            "Pump hunter SL moved to entry: symbol=%s side=%s entry=%s mark=%s pnl_pct=%.2f",
            symbol,
            side,
            entry_price,
            mark_price,
            pnl_pct,
        )
        return True

    def cancel_stale_live_orders(self) -> dict[str, Any]:
        if (
            not settings.pump_hunter_live_cancel_unfilled_enabled
            and not settings.pump_hunter_live_move_tp_to_entry_enabled
            and not settings.pump_hunter_live_place_sl_on_fill_enabled
            and not settings.pump_hunter_live_move_sl_to_entry_enabled
            and not settings.pump_hunter_live_place_tp_on_fill_enabled
            and float(settings.pump_hunter_live_profit_timeout_hours) <= 0
        ):
            return {"tracked": 0, "due": 0, "canceled": 0, "closed": 0, "profit_closed": 0, "errors": 0, "tp_placed": 0, "tp_moved": 0, "sl_placed": 0, "sl_moved": 0}
        if bool(settings.pump_hunter_live_order_test_mode):
            return {"tracked": 0, "due": 0, "canceled": 0, "closed": 0, "profit_closed": 0, "errors": 0, "tp_placed": 0, "tp_moved": 0, "sl_placed": 0, "sl_moved": 0}
        timeout_sec = max(300, int(settings.pump_hunter_live_cancel_after_minutes) * 60)
        now_ts = time.time()
        with self._live_order_lock:
            snapshot = list(self._live_order_registry.items())
        due_items = [
            (key, tracked, (now_ts - float(tracked.get("placed_at_ts") or now_ts)) >= timeout_sec)
            for key, tracked in snapshot
        ]
        canceled = 0
        closed = 0
        profit_closed = 0
        tp_placed = 0
        tp_moved = 0
        sl_placed = 0
        sl_moved = 0
        errors = 0
        for key, tracked, is_due in due_items:
            symbol = str(tracked.get("symbol") or "").strip()
            order_id = tracked.get("order_id")
            client_order_id = tracked.get("client_order_id")
            try:
                if bool(tracked.get("entry_filled")):
                    tp_status, _ = self._get_tracked_child_order_status(tracked, "tp")
                    sl_status, _ = self._get_tracked_child_order_status(tracked, "sl")
                    if tp_status == "FILLED":
                        if sl_status in {"NEW", "PARTIALLY_FILLED"}:
                            self._cancel_tracked_child_order(tracked, "sl")
                        with self._live_order_lock:
                            self._live_order_registry.pop(key, None)
                        closed += 1
                        continue
                    if sl_status == "FILLED":
                        if tp_status in {"NEW", "PARTIALLY_FILLED"}:
                            self._cancel_tracked_child_order(tracked, "tp")
                        with self._live_order_lock:
                            self._live_order_registry.pop(key, None)
                        closed += 1
                        continue
                    if not bool(tracked.get("tp_order_placed")) and self._maybe_place_tp_for_tracked_order(
                        tracked,
                        {"executedQty": tracked.get("filled_qty") or tracked.get("quantity") or 0.0},
                    ):
                        tp_placed += 1
                    if self._maybe_move_tp_to_entry_for_tracked_order(tracked):
                        tp_moved += 1
                    if not bool(tracked.get("sl_order_placed")) and self._maybe_place_sl_for_tracked_order(tracked):
                        sl_placed += 1
                    if self._maybe_move_sl_to_entry_for_tracked_order(tracked):
                        sl_moved += 1
                    if self._maybe_close_profitable_timeout_position(tracked, now_ts=now_ts):
                        with self._live_order_lock:
                            self._live_order_registry.pop(key, None)
                        profit_closed += 1
                        closed += 1
                        continue
                    if tp_status in {"CANCELED", "EXPIRED", "REJECTED"} and sl_status in {"CANCELED", "EXPIRED", "REJECTED"}:
                        with self._live_order_lock:
                            self._live_order_registry.pop(key, None)
                        closed += 1
                    continue
                status_resp = self.trade_client.get_order_status(
                    symbol=symbol,
                    order_id=order_id,
                    client_order_id=client_order_id,
                )
                status = str(status_resp.get("status") or "").upper()
                if status == "FILLED":
                    tracked["entry_filled"] = True
                    tracked["filled_qty"] = status_resp.get("executedQty") or tracked.get("quantity") or "0"
                    if self._maybe_place_tp_for_tracked_order(tracked, status_resp):
                        tp_placed += 1
                    if self._maybe_move_tp_to_entry_for_tracked_order(tracked):
                        tp_moved += 1
                    if self._maybe_place_sl_for_tracked_order(tracked):
                        sl_placed += 1
                    if self._maybe_move_sl_to_entry_for_tracked_order(tracked):
                        sl_moved += 1
                    continue
                if status in {"CANCELED", "EXPIRED", "REJECTED"}:
                    with self._live_order_lock:
                        self._live_order_registry.pop(key, None)
                    closed += 1
                    continue
                if is_due and status in {"NEW", "PARTIALLY_FILLED"}:
                    tracked["age_minutes"] = (now_ts - float(tracked.get("placed_at_ts") or now_ts)) / 60.0
                    cancel_resp = self.trade_client.cancel_order(
                        symbol=symbol,
                        order_id=order_id,
                        client_order_id=client_order_id,
                    )
                    if status == "PARTIALLY_FILLED":
                        tracked["entry_filled"] = True
                        tracked["filled_qty"] = status_resp.get("executedQty") or tracked.get("quantity") or "0"
                        if self._maybe_place_tp_for_tracked_order(tracked, status_resp):
                            tp_placed += 1
                        if self._maybe_move_tp_to_entry_for_tracked_order(tracked):
                            tp_moved += 1
                        if self._maybe_place_sl_for_tracked_order(tracked):
                            sl_placed += 1
                    else:
                        with self._live_order_lock:
                            self._live_order_registry.pop(key, None)
                        closed += 1
                    canceled += 1
                    self._send_order_cancel_discord_alert(tracked, cancel_resp)
                    logger.info(
                        "Pump hunter stale Binance order canceled: symbol=%s side=%s order_id=%s age_min=%.1f",
                        symbol,
                        tracked.get("side"),
                        order_id,
                        tracked.get("age_minutes"),
                    )
            except Exception:
                errors += 1
                logger.exception(
                    "Pump hunter stale Binance order check/cancel failed: symbol=%s order_id=%s client_order_id=%s",
                    symbol,
                    order_id,
                    client_order_id,
                )
        return {
            "tracked": len(snapshot),
            "due": sum(1 for _, _, is_due in due_items if is_due),
            "canceled": canceled,
            "closed": closed,
            "profit_closed": profit_closed,
            "tp_placed": tp_placed,
            "tp_moved": tp_moved,
            "sl_placed": sl_placed,
            "sl_moved": sl_moved,
            "errors": errors,
        }

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

    def _claim_alert_slot(self, key: str, ttl_sec: int) -> bool:
        with self._alert_lock:
            if self._get_cached(key) is not None:
                return False
            self._set_cache(key, "__pending__", ttl_sec=max(15, int(ttl_sec)))
            return True

    def _release_alert_slot(self, key: str) -> None:
        with self._alert_lock:
            self.cache.pop(key, None)

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
        rows = self.client.fetch_binance_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)
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

    @staticmethod
    def _detect_long_buildup_signal(
        *,
        breakout_pct_20: float,
        breakout_pct_55: float,
        vol_ratio_15m: float,
        vol_ratio_5m: float,
        vol_z_15m: float,
        momentum_pct_3: float,
        momentum_pct_12: float,
        expansion_ratio: float,
        close_in_range: float,
        body_pct: float,
        range_pct_15m: float,
        atr_pct_15m: float,
        stack_gap_pct: float,
        distance_pct: float,
        ema13_gap_pct: float,
        ema25_gap_pct: float,
        ema99_gap_pct: float,
        above_ema_stack: bool,
    ) -> dict[str, Any]:
        inactive = {
            "active": False,
            "source": None,
            "stage": None,
            "signal_label": None,
            "execution_mode": None,
            "signal_type": None,
            "score": 0.0,
            "signal_bonus": 0.0,
            "long_buildup_early_active": False,
            "ma99_reclaimed": False,
            "ma99_pressing": False,
            "reclaimed_ema13_25": False,
        }

        reclaimed_ema13_25 = ema13_gap_pct >= -0.22 and ema25_gap_pct >= -0.22
        ma99_pressing = -0.85 <= ema99_gap_pct <= 0.85
        ma99_reclaimed = ema99_gap_pct >= -0.12
        holding_rebound_zone = reclaimed_ema13_25 and momentum_pct_12 >= -1.5 and close_in_range >= 0.38
        volume_flowing = vol_ratio_15m >= 0.9 or vol_ratio_5m >= 1.1 or vol_z_15m >= -0.1
        breakout_pressure = (
            breakout_pct_20 >= -0.8
            and breakout_pct_55 >= -2.5
            and momentum_pct_12 >= -1.5
            and close_in_range >= 0.42
            and range_pct_15m <= 6.5
            and atr_pct_15m <= 3.8
            and distance_pct >= 0.35
        )
        pre_breakout_ready = holding_rebound_zone and ma99_pressing and volume_flowing and breakout_pressure
        breakout_confirmed = (
            pre_breakout_ready
            and ma99_reclaimed
            and breakout_pct_20 >= -0.15
            and close_in_range >= 0.55
            and body_pct >= 0.1
            and (vol_ratio_15m >= 1.0 or vol_ratio_5m >= 1.35 or momentum_pct_3 >= 0.45)
        )

        if not pre_breakout_ready and not breakout_confirmed:
            return inactive

        ma99_pressure_score = max(0.0, 1.0 - (min(abs(ema99_gap_pct), 1.4) / 1.4))
        long_score = (
            50.0
            + (_norm(vol_ratio_15m, 0.8, 3.2) * 10.0)
            + (_norm(vol_ratio_5m, 1.0, 3.2) * 8.0)
            + (_norm(vol_z_15m, -0.2, 3.8) * 4.0)
            + (_norm(breakout_pct_20, -1.0, 2.0) * 8.0)
            + (_norm(breakout_pct_55, -2.5, 5.0) * 4.0)
            + (_norm(momentum_pct_3, -0.4, 4.5) * 6.0)
            + (_norm(momentum_pct_12, -1.5, 8.0) * 6.0)
            + (_norm(close_in_range, 0.38, 0.98) * 7.0)
            + (_norm(body_pct, 0.08, 2.8) * 3.0)
            + (ma99_pressure_score * 6.0)
            + (_norm(max(stack_gap_pct, 0.0), 0.0, 2.2) * 3.0)
        )
        if reclaimed_ema13_25:
            long_score += 5.0
        if above_ema_stack:
            long_score += 3.0
        if breakout_confirmed:
            long_score += 6.0
        long_score = round(_clamp(long_score, 0.0, 100.0), 2)

        if breakout_confirmed:
            return {
                "active": True,
                "source": "pump_hunter_long_buildup_test",
                "stage": "BREAKOUT",
                "signal_label": "ARMING",
                "execution_mode": "CONFIRMED",
                "signal_type": "LONG_BUILDUP_BREAKOUT",
                "score": long_score,
                "signal_bonus": max(0.0, round(long_score - 58.0, 2)),
                "long_buildup_early_active": False,
                "ma99_reclaimed": ma99_reclaimed,
                "ma99_pressing": ma99_pressing,
                "reclaimed_ema13_25": reclaimed_ema13_25,
            }

        signal_label = "ARMING" if (vol_ratio_5m >= 1.35 or vol_ratio_15m >= 1.1 or vol_z_15m >= 0.15 or momentum_pct_3 >= 0.35) else "WATCH"
        return {
            "active": True,
            "source": "pump_hunter_long_buildup_pre_breakout_test",
            "stage": "PRE_PUMP",
            "signal_label": signal_label,
            "execution_mode": "SCOUT",
            "signal_type": "LONG_BUILDUP_PRE_BREAKOUT",
            "score": long_score,
            "signal_bonus": max(0.0, round(long_score - 54.0, 2)),
            "long_buildup_early_active": signal_label == "WATCH" or ema99_gap_pct < 0,
            "ma99_reclaimed": ma99_reclaimed,
            "ma99_pressing": ma99_pressing,
            "reclaimed_ema13_25": reclaimed_ema13_25,
        }

    def _ranked_symbols(self, max_symbols: int) -> list[tuple[str, dict[str, Any]]]:
        cache_key = f"ranked_symbols:{max_symbols if max_symbols > 0 else 'all'}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached
        stale_cached = self._get_cached(cache_key, allow_stale=True)

        try:
            markets = self.client.load_binance_markets()
            tickers = self.client.fetch_binance_tickers()
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

    def _process_scan_alerts(self, payload: dict[str, Any]) -> None:
        self.refresh_live_order_status()
        for row in payload.get("items", []):
            self._maybe_execute_paper_order(row)
            self._maybe_send_discord_alert(row)
            self._maybe_execute_live_order(row)

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

        ticker = ticker or self.client.fetch_binance_ticker(symbol)
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
        ema13_gap_pct = (((current_price - float(latest_15m.get("ema13") or current_price)) / float(latest_15m.get("ema13") or current_price)) * 100.0) if float(latest_15m.get("ema13") or 0.0) > 0 else 0.0
        ema25_gap_pct = (((current_price - float(latest_15m.get("ema25") or current_price)) / float(latest_15m.get("ema25") or current_price)) * 100.0) if float(latest_15m.get("ema25") or 0.0) > 0 else 0.0
        ema99_gap_pct = (((current_price - float(latest_15m.get("ema99") or current_price)) / float(latest_15m.get("ema99") or current_price)) * 100.0) if float(latest_15m.get("ema99") or 0.0) > 0 else 0.0
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
        base_stage = self._classify_stage(breakout_pct_20=breakout_pct_20, distance_pct=distance_pct, volume_ratio_fast=vol_ratio_5m)
        sweep_ctx = self._detect_sweep_signal(
            frame_15m,
            zone_low=zone_low,
            zone_high=zone_high,
            current_price=current_price,
            atr=atr,
        )
        long_ctx = self._detect_long_buildup_signal(
            breakout_pct_20=breakout_pct_20,
            breakout_pct_55=breakout_pct_55,
            vol_ratio_15m=vol_ratio_15m,
            vol_ratio_5m=vol_ratio_5m,
            vol_z_15m=vol_z_15m,
            momentum_pct_3=momentum_pct_3,
            momentum_pct_12=momentum_pct_12,
            expansion_ratio=expansion_ratio,
            close_in_range=close_in_range,
            body_pct=body_pct,
            range_pct_15m=range_pct_15m,
            atr_pct_15m=atr_pct_15m,
            stack_gap_pct=stack_gap_pct,
            distance_pct=distance_pct,
            ema13_gap_pct=ema13_gap_pct,
            ema25_gap_pct=ema25_gap_pct,
            ema99_gap_pct=ema99_gap_pct,
            above_ema_stack=above_ema_stack,
        )
        if bool(long_ctx.get("active")):
            target_price = max(float(target_price), current_price * 1.01)
            distance_pct = ((target_price - current_price) / current_price) * 100 if current_price > 0 else distance_pct
            reward_pct = max(0.0, distance_pct)
            rr_ratio = (reward_pct / risk_pct) if risk_pct > 0 else 0.0

        trade_side = "LONG"
        source = long_ctx.get("source") if bool(long_ctx.get("active")) else "pump_hunter_generic"
        stage = str(long_ctx.get("stage") or base_stage)
        signal_label = str(long_ctx.get("signal_label") or "WATCH")
        execution_mode = str(long_ctx.get("execution_mode") or "")
        signal_type = str(long_ctx.get("signal_type") or "")
        selected_bonus = float(long_ctx.get("signal_bonus") or 0.0)

        short_effective_score = round(_clamp(pump_score + float(sweep_ctx["signal_bonus"]), 0.0, 100.0), 2)
        long_effective_score = round(float(long_ctx.get("score") or 0.0), 2)

        if bool(sweep_ctx["entry_ready"]) and not (
            bool(long_ctx.get("active"))
            and long_effective_score >= short_effective_score
        ):
            trade_side = "SHORT"
            source = "pump_hunter_post_sweep_test"
            stage = "POST_SWEEP"
            signal_label = "ENTER"
            execution_mode = "WAIT_TOUCH"
            signal_type = "POST_SWEEP_SHORT"
            selected_bonus = float(sweep_ctx["signal_bonus"])
        elif bool(long_ctx.get("active")):
            trade_side = "LONG"
            source = str(long_ctx.get("source") or "pump_hunter_long_buildup_test")
            stage = str(long_ctx.get("stage") or base_stage)
            signal_label = str(long_ctx.get("signal_label") or "WATCH")
            execution_mode = str(long_ctx.get("execution_mode") or "")
            signal_type = str(long_ctx.get("signal_type") or "")
            selected_bonus = float(long_ctx.get("signal_bonus") or 0.0)
        elif bool(sweep_ctx["swept_recently"]):
            trade_side = "SHORT"
            source = "pump_hunter_post_sweep_test"
            stage = "SWEEPED"
            signal_label = "SWEEPED"
            execution_mode = "WAIT_TOUCH"
            signal_type = "POST_SWEEP_SHORT"
            selected_bonus = float(sweep_ctx["signal_bonus"])
        elif base_stage == "NEAR_SWEEP":
            signal_label = "ARMING"
            selected_bonus = max(selected_bonus, 4.0)

        effective_score = round(_clamp(pump_score + selected_bonus, 0.0, 100.0), 2)
        if trade_side == "LONG" and bool(long_ctx.get("active")):
            effective_score = long_effective_score

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
        if trade_side == "LONG" and source == "pump_hunter_long_buildup_test":
            notes.append("Long buildup breakout duoc xac nhan")
            notes.append("Reclaimed EMA13/EMA25")
            notes.append("MA99 reclaimed")
        elif trade_side == "LONG" and source == "pump_hunter_long_buildup_pre_breakout_test":
            notes.append("Long buildup pre-breakout dang duoc gom")
            notes.append("Reclaimed EMA13/EMA25")
            if bool(long_ctx.get("ma99_reclaimed")):
                notes.append("MA99 reclaimed")
            elif bool(long_ctx.get("ma99_pressing")):
                notes.append(f"Pressing MA99 ({abs(ema99_gap_pct):.2f}% away)")
        if trade_side == "SHORT" and bool(sweep_ctx["entry_ready"]):
            notes.append("Da quet len kill zone va bi tu choi xuong")
        elif trade_side == "SHORT" and bool(sweep_ctx["swept_recently"]):
            notes.append("Gia vua quet len vung thanh ly gan day")

        suggested_entry_price = current_price
        if trade_side == "SHORT":
            suggested_entry_price = self._derive_post_sweep_short_entry(
                current_price=current_price,
                invalidation_price=invalidation_price,
                zone_low=zone_low,
                zone_high=zone_high,
            )

        payload: dict[str, Any] = {
            "symbol": symbol,
            "mark_price": round(float(current_price), 6),
            "suggested_entry_price": round(float(suggested_entry_price), 6),
            "pump_score": pump_score,
            "effective_score": effective_score,
            "trade_side": trade_side,
            "source": source,
            "stage": stage,
            "signal_label": signal_label,
            "execution_mode": execution_mode,
            "signal_type": signal_type,
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
            "ema13_gap_pct": round(ema13_gap_pct, 2),
            "ema25_gap_pct": round(ema25_gap_pct, 2),
            "ema99_gap_pct": round(ema99_gap_pct, 2),
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
            "long_buildup_early_active": bool(long_ctx.get("long_buildup_early_active")),
            "kill_short_active": trade_side == "SHORT" and bool(sweep_ctx["entry_ready"]),
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

    def scan(
        self,
        *,
        max_symbols: int = 35,
        min_score: float = 58.0,
        limit: int = 18,
        send_alerts: bool = False,
    ) -> dict[str, Any]:
        safe_max_symbols = 0 if int(max_symbols) <= 0 else max(10, min(max_symbols, 500))
        safe_limit = max(1, min(limit, 100))
        safe_min_score = _clamp(float(min_score), 0.0, 100.0)
        cache_key = f"pump_scan:{safe_max_symbols if safe_max_symbols > 0 else 'all'}:{safe_min_score:.2f}:{safe_limit}"
        cached = self._get_cached(cache_key)
        if cached is not None:
            payload = self._attach_runtime_status(cached)
            if send_alerts:
                self._process_scan_alerts(payload)
            return payload
        stale_cached = self._get_cached(cache_key, allow_stale=True)

        items: list[dict[str, Any]] = []
        try:
            ranked_symbols = self._ranked_symbols(max_symbols=safe_max_symbols)
        except Exception as exc:
            if stale_cached is not None:
                return self._attach_runtime_status(stale_cached)
            return self._attach_runtime_status(self._build_scan_payload(
                scanned=0,
                items=[],
                min_score=safe_min_score,
                max_symbols=safe_max_symbols,
                limit=safe_limit,
                note=f"Pump hunter tam thoi khong lay duoc du lieu dau vao: {exc}",
            ))

        if not ranked_symbols:
            if stale_cached is not None:
                return self._attach_runtime_status(stale_cached)
            return self._attach_runtime_status(self._build_scan_payload(
                scanned=0,
                items=[],
                min_score=safe_min_score,
                max_symbols=safe_max_symbols,
                limit=safe_limit,
                note="Pump hunter tam thoi khong lay duoc symbol/ticker tu Binance REST. Thu quet lai sau.",
            ))

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
        if send_alerts:
            self._process_scan_alerts(payload)
        payload = self._attach_runtime_status(payload)
        return self._set_cache(cache_key, payload, ttl_sec=18)
