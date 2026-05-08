from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
import math
import xml.etree.ElementTree as ET

import httpx

from fastapi import APIRouter, HTTPException, Query

from app.core.config import settings
from app.deps import get_paper_trade_runtime
from app.models.event_windows import (
    MarketEventWindow,
    MarketEventWindowCreateRequest,
    MarketEventImportResponse,
    MarketEventWindowListResponse,
    MarketEventWindowUpdateRequest,
)
from app.models.paper_trades import (
    PaperManualCloseRequest,
    PaperTradeDailySummary,
    PaperTradeDailySummaryResponse,
    PaperTradeEma99BounceSignalsResponse,
    PaperTradeEntryHourCell,
    PaperTradeEntryHourMatrixResponse,
    PaperTradeEntryHourRow,
    PaperTradeEntryHourSummary,
    PaperTradeEntryHourTypeOption,
    PaperTradeHourlySideStats,
    PaperTradeHourlyWindow,
    PaperTradeHourlyWindowResponse,
    PaperMarketOpenRequest,
    PaperTrade,
    PaperTradeListResponse,
    PaperTradePatternStatsItem,
    PaperTradePatternStatsResponse,
    PaperTradePatternBackfillResponse,
    PaperTradeStats,
    PaperTradeStatsResponse,
)
from app.services.binance_client import BinanceFuturesClient
from app.services.candle_pattern_analyzer import CandlePatternAnalyzer
from app.services.data_pipeline import DataPipeline
from app.services.ema99_bounce_scanner import Ema99BounceScannerService
from app.services.mysql_trade_repo import MySQLTradeRepository
from app.services.pattern_performance import PatternPerformanceResolver
from app.services.risk_manager import (
    calc_atr_from_ohlcv,
    calc_estimated_margin_ratio_pct,
    calc_min_sl_pct_from_loss,
    calc_margin_usdt,
    calc_quantity_from_order_usdt,
    normalize_tp_sl,
)


def _parse_dt(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value)
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


class PaperTradeAPI:
    def __init__(self) -> None:
        self.router = APIRouter(prefix="/api/v1/paper-trades", tags=["paper-trades"])
        self.repo: MySQLTradeRepository | None = None
        self.candle_repo: MySQLTradeRepository | None = None
        self.price_stream = None
        self.major_symbol_resolver = None
        self.btc_follow_resolver = None
        self.market_client = BinanceFuturesClient()
        self.pattern_analyzer = CandlePatternAnalyzer(client=self.market_client)
        self.ema99_bounce_scanner = Ema99BounceScannerService(client=self.market_client)
        self.data_pipeline = DataPipeline()

        self.router.add_api_route("/open", self.get_open, methods=["GET"], response_model=PaperTradeListResponse)
        self.router.add_api_route("/history", self.get_history, methods=["GET"], response_model=PaperTradeListResponse)
        self.router.add_api_route("/stats", self.get_stats, methods=["GET"], response_model=PaperTradeStatsResponse)
        self.router.add_api_route("/ema99-bounce-signals", self.get_ema99_bounce_signals, methods=["GET"], response_model=PaperTradeEma99BounceSignalsResponse)
        self.router.add_api_route("/daily", self.get_daily_summary, methods=["GET"], response_model=PaperTradeDailySummaryResponse)
        self.router.add_api_route("/entry-hour-matrix", self.get_entry_hour_matrix, methods=["GET"], response_model=PaperTradeEntryHourMatrixResponse)
        self.router.add_api_route("/hourly-windows", self.get_hourly_windows, methods=["GET"], response_model=PaperTradeHourlyWindowResponse)
        self.router.add_api_route("/pattern-stats", self.get_pattern_stats, methods=["GET"], response_model=PaperTradePatternStatsResponse)
        self.router.add_api_route("/pattern-stats/backfill", self.backfill_pattern_stats, methods=["POST"], response_model=PaperTradePatternBackfillResponse)
        self.router.add_api_route("/event-windows", self.list_event_windows, methods=["GET"], response_model=MarketEventWindowListResponse)
        self.router.add_api_route("/event-windows", self.create_event_window, methods=["POST"], response_model=MarketEventWindow)
        self.router.add_api_route("/event-windows/import", self.import_event_windows, methods=["POST"], response_model=MarketEventImportResponse)
        self.router.add_api_route("/event-windows/{event_id}", self.update_event_window, methods=["PATCH"], response_model=MarketEventWindow)
        self.router.add_api_route("/event-windows/{event_id}", self.disable_event_window, methods=["DELETE"], response_model=MarketEventWindow)
        self.router.add_api_route("/market-open", self.market_open, methods=["POST"], response_model=PaperTrade)
        self.router.add_api_route("/close/{trade_id}", self.manual_close, methods=["POST"], response_model=PaperTrade)

    def bind_repo(self, repo: MySQLTradeRepository | None) -> None:
        self.repo = repo

    def bind_candle_repo(self, repo: MySQLTradeRepository | None) -> None:
        self.candle_repo = repo

    def bind_price_stream(self, price_stream: object | None) -> None:
        self.price_stream = price_stream

    def bind_major_symbol_resolver(self, resolver: object | None) -> None:
        self.major_symbol_resolver = resolver if callable(resolver) else None

    def bind_btc_follow_resolver(self, resolver: object | None) -> None:
        self.btc_follow_resolver = resolver if callable(resolver) else None

    @staticmethod
    def _normalize_symbol_key(symbol: str) -> str:
        return str(symbol or "").upper().strip().replace(":USDT", "")

    @staticmethod
    def _is_ml_basic_entry_type(value: object) -> bool:
        return str(value or "").strip().upper() == "LIMIT"

    @staticmethod
    def _uses_isolated_entry_scope(entry_type: str | None) -> bool:
        return str(entry_type or "").strip().upper() == "EMA99_BOUNCE"

    def _is_major_symbol(self, symbol: str) -> bool:
        if callable(self.major_symbol_resolver):
            try:
                return bool(self.major_symbol_resolver(symbol))
            except Exception:
                pass
        major_set = {self._normalize_symbol_key(item) for item in settings.paper_trade_major_symbols if item}
        return self._normalize_symbol_key(symbol) in major_set

    def _resolve_default_leverage(self, symbol: str) -> int:
        if self._is_major_symbol(symbol):
            return max(1, int(settings.paper_trade_major_leverage))
        return max(1, int(settings.paper_trade_leverage))

    def _pattern_performance_resolver(self) -> PatternPerformanceResolver:
        return PatternPerformanceResolver(
            analyzer=self.pattern_analyzer,
            repos=[self.repo, self.candle_repo],
            lookback=settings.paper_trade_perfect_pattern_lookback,
        )

    def _has_perfect_pattern_win_rate(self, symbol: str) -> bool:
        if not settings.paper_trade_perfect_pattern_leverage_enabled:
            return False
        try:
            resolver = self._pattern_performance_resolver()
            return bool(
                resolver.has_perfect_live_pattern(
                    symbol,
                    min_win_rate_pct=100.0,
                    min_trades=1,
                )
            )
        except Exception:
            return False

    def _use_strong_bear_short_leverage(self, symbol: str, side: str | None = None) -> bool:
        side_key = str(side or "").strip().upper()
        if side_key and side_key != "SHORT":
            return False
        try:
            pattern = str(self.pattern_analyzer.current_symbol_pattern(symbol) or "").strip().upper()
        except Exception:
            pattern = ""
        if pattern != "STRONG_BEAR":
            return False
        try:
            btc_trend = str(self.pattern_analyzer.btc_trend_now() or "").strip().upper()
        except Exception:
            btc_trend = ""
        return btc_trend == "SHORT"

    def _resolve_open_leverage(
        self,
        symbol: str,
        requested_leverage: int | None = None,
        side: str | None = None,
    ) -> int:
        leverage = int(requested_leverage or self._resolve_default_leverage(symbol))
        if requested_leverage is not None:
            return max(1, leverage)
        if self._use_strong_bear_short_leverage(symbol=symbol, side=side):
            leverage = max(leverage, 10)
        if self._has_perfect_pattern_win_rate(symbol):
            leverage = max(leverage, int(settings.paper_trade_perfect_pattern_leverage))
        return max(1, leverage)

    def _resolve_max_risk_pct(self, symbol: str) -> float:
        if self._is_major_symbol(symbol):
            return max(float(settings.paper_trade_max_risk_pct), float(settings.paper_trade_major_max_risk_pct))
        return float(settings.paper_trade_max_risk_pct)

    def _resolve_open_max_risk_pct(self, symbol: str, leverage: int) -> float:
        base = self._resolve_max_risk_pct(symbol)
        if self._has_perfect_pattern_win_rate(symbol):
            base = max(
                float(base),
                calc_estimated_margin_ratio_pct(
                    leverage=max(1, int(leverage)),
                    maint_margin_rate=settings.paper_trade_maint_margin_rate,
                ),
            )
        return float(base)

    @staticmethod
    def _normalize_repo_scope(value: str | None) -> str:
        scope = str(value or "main").strip().lower()
        if scope not in {"main", "candles", "auto"}:
            return "main"
        return scope

    def _resolve_repo(
        self,
        *,
        repo_scope: str | None = None,
        entry_type: str | None = None,
    ) -> MySQLTradeRepository:
        scope = self._normalize_repo_scope(repo_scope)
        normalized_entry_type = str(entry_type or "").strip().upper()
        if scope == "auto":
            scope = "candles" if normalized_entry_type == "ML_CANDLES_TEST" else "main"
        elif scope == "main" and normalized_entry_type == "ML_CANDLES_TEST":
            scope = "candles"

        if scope == "candles":
            if self.candle_repo is None:
                raise HTTPException(status_code=503, detail="Paper trading candle DB is not configured")
            return self.candle_repo
        if self.repo is None:
            raise HTTPException(status_code=503, detail="Paper trading DB is not configured")
        return self.repo

    def _resolve_close_context(self, row: dict) -> tuple[str | None, str | None]:
        closed_at = _parse_dt(row.get("closed_at")) or _parse_dt(row.get("opened_at"))
        if closed_at is None:
            return None, None
        pattern = str(row.get("close_candle_pattern") or "").strip().upper() or None
        btc_trend = str(row.get("btc_trend_at_close") or "").strip().upper() or None
        if pattern is None:
            try:
                pattern = self.pattern_analyzer.symbol_pattern_at(
                    symbol=str(row.get("symbol") or ""),
                    at_dt=closed_at,
                )
            except Exception:
                pattern = None
        if btc_trend is None:
            try:
                btc_trend = self.pattern_analyzer.btc_trend_at(closed_at)
            except Exception:
                btc_trend = None
        return pattern, btc_trend

    def _require_repo(self) -> MySQLTradeRepository:
        return self._resolve_repo(repo_scope="main")

    def _resolve_btc_follow_map(self, rows: list[dict]) -> dict[str, bool | None]:
        if not rows:
            return {}
        symbols = sorted(
            {
                str(item.get("symbol") or "").strip()
                for item in rows
                if item.get("symbol") and self._coerce_optional_bool(item.get("btc_following")) is None
            }
        )
        if not symbols:
            return {}
        if not callable(self.btc_follow_resolver):
            return {sym: None for sym in symbols}

        out: dict[str, bool | None] = {}
        for symbol in symbols:
            try:
                out[symbol] = bool(self.btc_follow_resolver(symbol))
            except Exception:
                out[symbol] = None
        return out

    @staticmethod
    def _coerce_optional_bool(value: object) -> bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(int(value))
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y"}:
            return True
        if text in {"0", "false", "no", "n"}:
            return False
        return None

    @staticmethod
    def _parse_feature_snapshot(raw: object) -> dict[str, Any] | None:
        if raw is None:
            return None
        payload: object = raw
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8", errors="ignore")
        if isinstance(payload, str):
            text = payload.strip()
            if not text:
                return None
            try:
                payload = json.loads(text)
            except Exception:
                return None
        return payload if isinstance(payload, dict) else None

    @classmethod
    def _extract_entry_snapshot_context(cls, raw: object) -> dict[str, str | None]:
        snapshot = cls._parse_feature_snapshot(raw)
        if not snapshot:
            return {
                "entry_source": None,
                "entry_stage": None,
                "entry_signal_label": None,
                "entry_signal_type": None,
                "entry_execution_mode": None,
            }

        def pick(key: str) -> str | None:
            value = snapshot.get(key)
            if value is None:
                return None
            text = str(value).strip()
            return text or None

        return {
            "entry_source": pick("source"),
            "entry_stage": pick("stage"),
            "entry_signal_label": pick("signal_label"),
            "entry_signal_type": pick("signal_type"),
            "entry_execution_mode": pick("execution_mode"),
        }

    @staticmethod
    def _hourly_stats_from_row(row: dict | None) -> dict[str, float | int]:
        if not row:
            return {
                "total_orders": 0,
                "wins": 0,
                "losses": 0,
                "win_rate_pct": 0.0,
                "loss_rate_pct": 0.0,
                "net_pnl": 0.0,
                "avg_pnl": 0.0,
            }
        return {
            "total_orders": int(row.get("total_orders") or 0),
            "wins": int(row.get("wins") or 0),
            "losses": int(row.get("losses") or 0),
            "win_rate_pct": float(row.get("win_rate_pct") or 0.0),
            "loss_rate_pct": float(row.get("loss_rate_pct") or 0.0),
            "net_pnl": float(row.get("net_pnl") or 0.0),
            "avg_pnl": float(row.get("avg_pnl") or 0.0),
        }

    @staticmethod
    def _classify_hourly_action(
        *,
        stats: dict[str, float | int],
        min_samples: int,
        block_win_rate_pct: float,
        strict_win_rate_pct: float,
    ) -> tuple[str, str]:
        total_orders = int(stats.get("total_orders") or 0)
        wins = int(stats.get("wins") or 0)
        losses = int(stats.get("losses") or 0)
        win_rate_pct = float(stats.get("win_rate_pct") or 0.0)
        net_pnl = float(stats.get("net_pnl") or 0.0)

        if total_orders < min_samples:
            return "LOW_DATA", f"<{min_samples} samples"
        if win_rate_pct <= block_win_rate_pct or (losses > wins and net_pnl < 0.0):
            return "BLOCK", "bad hour"
        if win_rate_pct <= strict_win_rate_pct or net_pnl < 0.0:
            return "STRICT", "raise min win"
        return "ALLOW", "normal"

    @classmethod
    def _map_trade(cls, row: dict, btc_following: bool | None = None) -> PaperTrade:
        snapshot_context = cls._extract_entry_snapshot_context(row.get("feature_snapshot_json"))
        entry_price = float(row["entry_price"])
        quantity = float(row["quantity"])
        leverage = int(row["leverage"])
        close_price = float(row["close_price"]) if row.get("close_price") is not None else None
        mark_price = float(row["mark_price"]) if row.get("mark_price") is not None else None
        pnl = float(row["pnl"]) if row.get("pnl") is not None else None
        margin_usdt = (
            float(row["margin_usdt"])
            if row.get("margin_usdt") is not None
            else calc_margin_usdt(entry_price=entry_price, quantity=quantity, leverage=leverage)
        )
        pnl_pct = None
        status = str(row["status"])
        side = str(row["side"])
        if pnl is None and status.upper() == "OPEN" and mark_price is not None:
            pnl = (mark_price - entry_price) * quantity if side == "LONG" else (entry_price - mark_price) * quantity
        if pnl is not None and margin_usdt > 0:
            pnl_pct = (pnl / margin_usdt) * 100

        row_btc_follow = cls._coerce_optional_bool(row.get("btc_following"))
        resolved_btc_follow = row_btc_follow if row_btc_follow is not None else btc_following

        return PaperTrade(
            id=int(row["id"]),
            symbol=str(row["symbol"]),
            side=side,
            btc_following=resolved_btc_follow,
            entry_type=str(row.get("entry_type") or "LIMIT"),
            signal_win_probability=float(row["signal_win_probability"]),
            effective_win_probability=float(row.get("effective_win_probability") or row["signal_win_probability"]),
            entry_price=entry_price,
            take_profit=float(row["take_profit"]),
            stop_loss=float(row["stop_loss"]),
            liq_ema99_15m=float(row["liq_ema99_15m"]) if row.get("liq_ema99_15m") is not None else None,
            liq_ema99_1h=float(row["liq_ema99_1h"]) if row.get("liq_ema99_1h") is not None else None,
            liq_zone_price=float(row["liq_zone_price"]) if row.get("liq_zone_price") is not None else None,
            liq_zone_score=float(row["liq_zone_score"]) if row.get("liq_zone_score") is not None else None,
            entry_point_score=float(row["entry_point_score"]) if row.get("entry_point_score") is not None else None,
            quantity=quantity,
            leverage=leverage,
            status=status,
            opened_at=_parse_dt(row.get("opened_at")) or datetime.utcnow(),
            closed_at=_parse_dt(row.get("closed_at")),
            close_price=close_price,
            mark_price=mark_price,
            mark_price_timestamp=str(row["mark_price_timestamp"]) if row.get("mark_price_timestamp") is not None else None,
            close_reason=str(row["close_reason"]) if row.get("close_reason") is not None else None,
            reference_win_symbol=str(row["reference_win_symbol"]) if row.get("reference_win_symbol") is not None else None,
            reference_win_at=_parse_dt(row.get("reference_win_at")),
            pnl=pnl,
            pnl_pct=pnl_pct,
            commission_usdt=float(row["commission_usdt"]) if row.get("commission_usdt") is not None else None,
            mae_pct=float(row["mae_pct"]) if row.get("mae_pct") is not None else None,
            mfe_pct=float(row["mfe_pct"]) if row.get("mfe_pct") is not None else None,
            margin_usdt=margin_usdt,
            result=int(row["result"]) if row.get("result") is not None else None,
            current_candle_pattern=str(row["current_candle_pattern"]) if row.get("current_candle_pattern") is not None else None,
            current_btc_trend=str(row["current_btc_trend"]) if row.get("current_btc_trend") is not None else None,
            close_candle_pattern=str(row["close_candle_pattern"]) if row.get("close_candle_pattern") is not None else None,
            btc_trend_at_close=str(row["btc_trend_at_close"]) if row.get("btc_trend_at_close") is not None else None,
            entry_source=snapshot_context["entry_source"],
            entry_stage=snapshot_context["entry_stage"],
            entry_signal_label=snapshot_context["entry_signal_label"],
            entry_signal_type=snapshot_context["entry_signal_type"],
            entry_execution_mode=snapshot_context["entry_execution_mode"],
        )

    def _enrich_live_context(self, row: dict) -> dict:
        out = dict(row)
        symbol = str(out.get("symbol") or "").strip()
        if not symbol:
            return out
        if out.get("current_candle_pattern") is None:
            try:
                out["current_candle_pattern"] = self.pattern_analyzer.current_symbol_pattern(symbol)
            except Exception:
                out["current_candle_pattern"] = None
        if out.get("current_btc_trend") is None:
            try:
                out["current_btc_trend"] = self.pattern_analyzer.btc_trend_now()
            except Exception:
                out["current_btc_trend"] = None
        return out

    async def _enrich_open_prices(self, rows: list[dict]) -> list[dict]:
        if not rows:
            return rows

        symbols = [str(row.get("symbol") or "").strip() for row in rows if str(row.get("symbol") or "").strip()]
        if not symbols:
            return rows

        unique_symbols = list(dict.fromkeys(symbols))
        prices: dict[str, float] = {}
        timestamps: dict[str, str] = {}

        if self.price_stream is not None:
            try:
                stream_prices, _stamp, stream_timestamps = await self.price_stream.get_prices(unique_symbols)
                prices.update({str(symbol): float(price) for symbol, price in stream_prices.items() if price is not None})
                timestamps.update({str(symbol): str(ts) for symbol, ts in stream_timestamps.items() if ts})
            except Exception:
                pass

        missing = [symbol for symbol in unique_symbols if symbol not in prices]
        if missing:
            try:
                tickers = await asyncio.to_thread(self.market_client.fetch_tickers, missing)
                if isinstance(tickers, dict):
                    for symbol in missing:
                        ticker = tickers.get(symbol)
                        if not isinstance(ticker, dict):
                            continue
                        px = ticker.get("last") or ticker.get("close")
                        if px is None:
                            bid = ticker.get("bid")
                            ask = ticker.get("ask")
                            if bid is not None and ask is not None:
                                px = (bid + ask) / 2
                        if px is None:
                            continue
                        prices[symbol] = float(px)
                        stamp = ticker.get("datetime")
                        if stamp:
                            timestamps[symbol] = str(stamp)
            except Exception:
                pass

        still_missing = [symbol for symbol in unique_symbols if symbol not in prices]
        for symbol in still_missing:
            try:
                ticker = await asyncio.to_thread(self.market_client.fetch_ticker, symbol)
                if isinstance(ticker, dict):
                    px = ticker.get("last") or ticker.get("close")
                    if px is None:
                        bid = ticker.get("bid")
                        ask = ticker.get("ask")
                        if bid is not None and ask is not None:
                            px = (bid + ask) / 2
                    if px is not None:
                        prices[symbol] = float(px)
                        stamp = ticker.get("datetime")
                        if stamp:
                            timestamps[symbol] = str(stamp)
                        continue
            except Exception:
                pass

            try:
                rows = await asyncio.to_thread(self.market_client.fetch_ohlcv, symbol, "1m", 2)
                if rows:
                    prices[symbol] = float(rows[-1][4])
            except Exception:
                pass

        enriched: list[dict] = []
        for row in rows:
            out = dict(row)
            symbol = str(out.get("symbol") or "").strip()
            if symbol and prices.get(symbol) is not None:
                out["mark_price"] = prices[symbol]
                if timestamps.get(symbol):
                    out["mark_price_timestamp"] = timestamps[symbol]
            enriched.append(out)
        return enriched

    def _enrich_close_context(self, row: dict) -> dict:
        out = dict(row)
        if str(out.get("status") or "").upper() != "CLOSED":
            return out
        pattern, btc_trend = self._resolve_close_context(out)
        if out.get("close_candle_pattern") is None:
            out["close_candle_pattern"] = pattern
        if out.get("btc_trend_at_close") is None:
            out["btc_trend_at_close"] = btc_trend
        if (pattern or btc_trend) and out.get("id") is not None and (
            row.get("close_candle_pattern") is None or row.get("btc_trend_at_close") is None
        ):
            try:
                repo_scope = "candles" if str(out.get("entry_type") or "").upper() == "ML_CANDLES_TEST" else "main"
                repo = self._resolve_repo(repo_scope=repo_scope, entry_type=str(out.get("entry_type") or ""))
                repo.update_trade_close_context(
                    int(out["id"]),
                    close_candle_pattern=pattern,
                    btc_trend_at_close=btc_trend,
                )
            except Exception:
                pass
        return out

    @staticmethod
    def _dedupe_pattern_rows(rows: list[dict]) -> list[dict]:
        seen: set[tuple] = set()
        out: list[dict] = []
        for row in rows:
            key = (
                str(row.get("symbol") or ""),
                str(row.get("side") or ""),
                str(row.get("entry_type") or ""),
                str(row.get("opened_at") or ""),
                str(row.get("closed_at") or ""),
                str(row.get("close_price") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out

    @staticmethod
    def _row_sort_ts(row: dict) -> float:
        dt = _parse_dt(row.get("closed_at")) or _parse_dt(row.get("opened_at"))
        if dt is None:
            return 0.0
        if dt.tzinfo is None:
            return dt.timestamp()
        return dt.astimezone(timezone.utc).timestamp()

    @staticmethod
    def _entry_hour_type_specs() -> list[tuple[str, str]]:
        return [
            ("ALL", "ALL"),
            ("LIMIT", "ML (LIMIT)"),
            ("EMA99_BOUNCE", "EMA99 Bounce"),
            ("PUMP_ENTRY_TOUCH", "Pump Hunter"),
            ("ML_CANDLES_BG", "ML Candles BG"),
            ("ML_CANDLES_TEST", "ML Candles Test"),
            ("ML_TEST", "ML Test"),
        ]

    @classmethod
    def _entry_hour_type_label(cls, key: str) -> str:
        normalized = str(key or "ALL").upper()
        for item_key, label in cls._entry_hour_type_specs():
            if item_key == normalized:
                return label
        return normalized

    @classmethod
    def _normalize_entry_hour_key(cls, value: str | None) -> str:
        normalized = str(value or "ALL").strip().upper()
        valid = {item_key for item_key, _ in cls._entry_hour_type_specs()}
        return normalized if normalized in valid else "ALL"

    @staticmethod
    def _dedupe_entry_hour_rows(rows: list[dict]) -> list[dict]:
        seen: set[tuple] = set()
        out: list[dict] = []
        for row in rows:
            key = (
                str(row.get("symbol") or ""),
                str(row.get("side") or ""),
                str(row.get("entry_type") or ""),
                str(row.get("opened_at") or ""),
                str(row.get("closed_at") or ""),
                str(row.get("pnl") or ""),
                str(row.get("result") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        return out

    def _list_entry_hour_repos(self) -> list[MySQLTradeRepository]:
        repos: list[MySQLTradeRepository] = []
        seen: set[tuple[str, int, str]] = set()
        for scope in ("main", "candles"):
            try:
                repo = self._resolve_repo(repo_scope=scope)
            except Exception:
                continue
            identity = (str(repo.host), int(repo.port), str(repo.database))
            if identity in seen:
                continue
            seen.add(identity)
            repos.append(repo)
        return repos

    @staticmethod
    def _build_entry_hour_row(trade_date: str, buckets: dict[int, dict[str, float | int]]) -> PaperTradeEntryHourRow:
        cells: list[PaperTradeEntryHourCell] = []
        total_trades = 0
        win_trades = 0
        loss_trades = 0
        total_pnl = 0.0
        for hour_vn in range(24):
            bucket = buckets.get(hour_vn) or {}
            hour_total = int(bucket.get("total_trades") or 0)
            hour_wins = int(bucket.get("win_trades") or 0)
            hour_losses = int(bucket.get("loss_trades") or 0)
            hour_pnl = float(bucket.get("total_pnl") or 0.0)
            total_trades += hour_total
            win_trades += hour_wins
            loss_trades += hour_losses
            total_pnl += hour_pnl
            cells.append(
                PaperTradeEntryHourCell(
                    hour_vn=hour_vn,
                    total_trades=hour_total,
                    win_trades=hour_wins,
                    loss_trades=hour_losses,
                    win_rate=(hour_wins / hour_total) if hour_total > 0 else 0.0,
                    total_pnl=hour_pnl,
                    avg_pnl=(hour_pnl / hour_total) if hour_total > 0 else 0.0,
                )
            )
        return PaperTradeEntryHourRow(
            trade_date=trade_date,
            total_trades=total_trades,
            win_trades=win_trades,
            loss_trades=loss_trades,
            win_rate=(win_trades / total_trades) if total_trades > 0 else 0.0,
            total_pnl=total_pnl,
            avg_pnl=(total_pnl / total_trades) if total_trades > 0 else 0.0,
            cells=cells,
        )

    async def get_open(
        self,
        repo_scope: str = Query(default="main", pattern="^(main|candles)$"),
    ) -> PaperTradeListResponse:
        repo = self._resolve_repo(repo_scope=repo_scope)
        rows = [self._enrich_live_context(row) for row in repo.list_open_trades()]
        rows = await self._enrich_open_prices(rows)
        btc_follow_map = self._resolve_btc_follow_map(rows)
        return PaperTradeListResponse(
            items=[self._map_trade(row, btc_following=btc_follow_map.get(str(row.get("symbol") or ""))) for row in rows]
        )

    def get_history(
        self,
        limit: int = Query(default=200, ge=1, le=2000),
        page: int | None = Query(default=None, ge=1),
        page_size: int | None = Query(default=None, ge=1, le=200),
        repo_scope: str = Query(default="main", pattern="^(main|candles)$"),
        include_patterns: bool = Query(default=False),
    ) -> PaperTradeListResponse:
        repo = self._resolve_repo(repo_scope=repo_scope)
        if page is None and page_size is None:
            rows = repo.list_recent_trades(limit=limit)
            if include_patterns:
                rows = [self._enrich_close_context(row) for row in rows]
            btc_follow_map = self._resolve_btc_follow_map(rows)
            return PaperTradeListResponse(
                items=[self._map_trade(row, btc_following=btc_follow_map.get(str(row.get("symbol") or ""))) for row in rows]
            )

        target_page = page or 1
        target_page_size = page_size or min(limit, 200)
        rows, total = repo.list_recent_trades_paged(page=target_page, page_size=target_page_size)
        if include_patterns:
            rows = [self._enrich_close_context(row) for row in rows]
        btc_follow_map = self._resolve_btc_follow_map(rows)
        total_pages = max(1, math.ceil(total / target_page_size)) if total > 0 else 1
        return PaperTradeListResponse(
            items=[self._map_trade(row, btc_following=btc_follow_map.get(str(row.get("symbol") or ""))) for row in rows],
            total=total,
            page=min(target_page, total_pages),
            page_size=target_page_size,
            total_pages=total_pages,
        )

    def get_stats(
        self,
        repo_scope: str = Query(default="main", pattern="^(main|candles)$"),
    ) -> PaperTradeStatsResponse:
        repo = self._resolve_repo(repo_scope=repo_scope)
        payload = repo.stats()
        payload["order_usdt"] = settings.paper_trade_order_usdt
        payload["margin_usdt"] = settings.paper_trade_margin_usdt if settings.paper_trade_margin_usdt > 0 else (
            settings.paper_trade_order_usdt / max(1, settings.paper_trade_leverage)
        )
        payload["leverage"] = settings.paper_trade_leverage
        payload["maint_margin_rate"] = settings.paper_trade_maint_margin_rate
        payload["max_risk_pct"] = settings.paper_trade_max_risk_pct
        stats = PaperTradeStats(**payload)
        return PaperTradeStatsResponse(stats=stats)

    def get_ema99_bounce_signals(
        self,
        max_symbols: int = Query(default=200, ge=10, le=250),
        max_items: int = Query(default=12, ge=1, le=50),
    ) -> PaperTradeEma99BounceSignalsResponse:
        payload = self.ema99_bounce_scanner.scan_current_signals(
            max_symbols=max_symbols,
            max_items=max_items,
        )
        return PaperTradeEma99BounceSignalsResponse(**payload)

    def get_daily_summary(self, days: int = Query(default=30, ge=1, le=365)) -> PaperTradeDailySummaryResponse:
        repo = self._require_repo()
        rows = repo.daily_summary(days=days)
        return PaperTradeDailySummaryResponse(items=[PaperTradeDailySummary(**row) for row in rows])

    def get_entry_hour_matrix(
        self,
        days: int = Query(default=30, ge=1, le=365),
        entry_type_key: str = Query(default="ALL"),
    ) -> PaperTradeEntryHourMatrixResponse:
        safe_days = max(1, int(days))
        safe_entry_type = self._normalize_entry_hour_key(entry_type_key)
        from_dt = self._now_vn_naive() - timedelta(days=safe_days - 1)
        repos = self._list_entry_hour_repos()
        if not repos:
            raise HTTPException(status_code=503, detail="Paper trading DB is not configured")

        rows: list[dict] = []
        for repo in repos:
            try:
                rows.extend(repo.list_closed_trades_since(from_dt=from_dt))
            except Exception:
                continue
        rows = self._dedupe_entry_hour_rows(rows)

        option_counts: dict[str, int] = {item_key: 0 for item_key, _ in self._entry_hour_type_specs()}
        filtered_rows: list[dict] = []
        for row in rows:
            opened_at = _parse_dt(row.get("opened_at"))
            if opened_at is None:
                continue
            if opened_at.tzinfo is not None:
                opened_at = opened_at.astimezone(timezone(timedelta(hours=7))).replace(tzinfo=None)
            if opened_at < from_dt:
                continue
            normalized_entry_type = str(row.get("entry_type") or "LIMIT").upper().strip() or "LIMIT"
            option_counts["ALL"] += 1
            if normalized_entry_type in option_counts:
                option_counts[normalized_entry_type] += 1
            if safe_entry_type != "ALL" and normalized_entry_type != safe_entry_type:
                continue
            row["_opened_at_vn"] = opened_at
            row["_entry_type_key"] = normalized_entry_type
            filtered_rows.append(row)

        daily_buckets: dict[str, dict[int, dict[str, float | int]]] = {}
        total_buckets: dict[int, dict[str, float | int]] = {}
        total_trades = 0
        win_trades = 0
        loss_trades = 0
        total_pnl = 0.0

        def touch(bucket_map: dict[int, dict[str, float | int]], hour_vn: int, pnl: float, result: object) -> None:
            bucket = bucket_map.setdefault(
                hour_vn,
                {
                    "total_trades": 0,
                    "win_trades": 0,
                    "loss_trades": 0,
                    "total_pnl": 0.0,
                },
            )
            bucket["total_trades"] = int(bucket["total_trades"]) + 1
            if int(result or 0) == 1:
                bucket["win_trades"] = int(bucket["win_trades"]) + 1
            elif result is not None:
                bucket["loss_trades"] = int(bucket["loss_trades"]) + 1
            bucket["total_pnl"] = float(bucket["total_pnl"]) + pnl

        for row in filtered_rows:
            opened_at = row.get("_opened_at_vn")
            if not isinstance(opened_at, datetime):
                continue
            trade_date = str(opened_at.date())
            hour_vn = int(opened_at.hour)
            pnl = float(row.get("pnl") or 0.0)
            result = row.get("result")
            total_trades += 1
            if int(result or 0) == 1:
                win_trades += 1
            elif result is not None:
                loss_trades += 1
            total_pnl += pnl
            touch(daily_buckets.setdefault(trade_date, {}), hour_vn, pnl, result)
            touch(total_buckets, hour_vn, pnl, result)

        items = [
            self._build_entry_hour_row(trade_date, daily_buckets[trade_date])
            for trade_date in sorted(daily_buckets.keys(), reverse=True)
        ]
        total_row = self._build_entry_hour_row("TOTAL", total_buckets)
        summary = PaperTradeEntryHourSummary(
            active_days=len(items),
            total_trades=total_trades,
            win_trades=win_trades,
            loss_trades=loss_trades,
            win_rate=(win_trades / total_trades) if total_trades > 0 else 0.0,
            total_pnl=total_pnl,
            avg_pnl=(total_pnl / total_trades) if total_trades > 0 else 0.0,
        )
        options = [
            PaperTradeEntryHourTypeOption(
                key=item_key,
                label=label,
                total_trades=int(option_counts.get(item_key) or 0),
            )
            for item_key, label in self._entry_hour_type_specs()
        ]
        return PaperTradeEntryHourMatrixResponse(
            lookback_days=safe_days,
            entry_type_key=safe_entry_type,
            entry_type_label=self._entry_hour_type_label(safe_entry_type),
            summary=summary,
            total_row=total_row,
            entry_type_options=options,
            items=items,
        )

    def get_pattern_stats(
        self,
        lookback: int = Query(default=1000, ge=0, le=50000),
        repo_scope: str = Query(default="all", pattern="^(main|candles|all)$"),
        include_unknown: bool = Query(default=False),
    ) -> PaperTradePatternStatsResponse:
        safe_scope = str(repo_scope or "all").lower()
        rows: list[dict] = []
        if safe_scope in {"main", "candles"}:
            repo = self._resolve_repo(repo_scope=safe_scope)
            rows = repo.list_closed_trades_for_pattern_stats(limit=lookback)
        else:
            main_rows: list[dict] = []
            candle_rows: list[dict] = []
            try:
                main_rows = self._resolve_repo(repo_scope="main").list_closed_trades_for_pattern_stats(limit=lookback)
            except Exception:
                main_rows = []
            try:
                candle_rows = self._resolve_repo(repo_scope="candles").list_closed_trades_for_pattern_stats(limit=lookback)
            except Exception:
                candle_rows = []
            rows = self._dedupe_pattern_rows(main_rows + candle_rows)
            rows.sort(
                key=lambda row: (self._row_sort_ts(row), int(row.get("id") or 0)),
                reverse=True,
            )
            if lookback > 0:
                rows = rows[:lookback]

        enriched_rows = [self._enrich_close_context(row) for row in rows]
        buckets: dict[tuple[str, str], dict[str, float | int | str]] = {}
        included_rows = 0
        unknown_rows = 0
        for row in enriched_rows:
            if not self._is_ml_basic_entry_type(row.get("entry_type")):
                continue
            pattern = str(row.get("close_candle_pattern") or "").upper().strip()
            btc_trend = str(row.get("btc_trend_at_close") or "").upper().strip()
            if not pattern or not btc_trend:
                unknown_rows += 1
                if not include_unknown:
                    continue
            pattern = pattern or "UNKNOWN"
            btc_trend = btc_trend or "UNKNOWN"
            included_rows += 1
            key = (pattern, btc_trend)
            bucket = buckets.setdefault(
                key,
                {
                    "candle_pattern": pattern,
                    "btc_trend": btc_trend,
                    "total_trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "net_pnl": 0.0,
                },
            )
            bucket["total_trades"] = int(bucket["total_trades"]) + 1
            if int(row.get("result") or 0) == 1:
                bucket["wins"] = int(bucket["wins"]) + 1
            elif row.get("result") is not None:
                bucket["losses"] = int(bucket["losses"]) + 1
            bucket["net_pnl"] = float(bucket["net_pnl"]) + float(row.get("pnl") or 0.0)

        items: list[PaperTradePatternStatsItem] = []
        for bucket in buckets.values():
            total_trades = int(bucket["total_trades"])
            wins = int(bucket["wins"])
            losses = int(bucket["losses"])
            net_pnl = float(bucket["net_pnl"])
            items.append(
                PaperTradePatternStatsItem(
                    candle_pattern=str(bucket["candle_pattern"]),
                    btc_trend=str(bucket["btc_trend"]),
                    total_trades=total_trades,
                    wins=wins,
                    losses=losses,
                    win_rate_pct=(wins / total_trades * 100.0) if total_trades > 0 else 0.0,
                    loss_rate_pct=(losses / total_trades * 100.0) if total_trades > 0 else 0.0,
                    net_pnl=net_pnl,
                    avg_pnl=(net_pnl / total_trades) if total_trades > 0 else 0.0,
                )
            )
        items.sort(
            key=lambda item: (item.total_trades, item.win_rate_pct, item.net_pnl),
            reverse=True,
        )
        return PaperTradePatternStatsResponse(
            repo_scope=safe_scope,
            lookback=int(lookback),
            closed_trades=included_rows,
            unknown_trades=unknown_rows,
            items=items,
        )

    def backfill_pattern_stats(
        self,
        repo_scope: str = Query(default="all", pattern="^(main|candles|all)$"),
        batch_size: int = Query(default=500, ge=1, le=5000),
    ) -> PaperTradePatternBackfillResponse:
        safe_scope = str(repo_scope or "all").lower()
        repos: list[MySQLTradeRepository] = []
        if safe_scope in {"main", "candles"}:
            repos.append(self._resolve_repo(repo_scope=safe_scope))
        else:
            try:
                repos.append(self._resolve_repo(repo_scope="main"))
            except Exception:
                pass
            try:
                candle_repo = self._resolve_repo(repo_scope="candles")
                if candle_repo not in repos:
                    repos.append(candle_repo)
            except Exception:
                pass

        remaining = int(batch_size)
        processed = 0
        updated = 0
        for repo in repos:
            if remaining <= 0:
                break
            rows = repo.list_closed_trades_missing_close_context(limit=remaining)
            for row in rows:
                processed += 1
                pattern, btc_trend = self._resolve_close_context(row)
                if not pattern and not btc_trend:
                    continue
                try:
                    repo.update_trade_close_context(
                        int(row["id"]),
                        close_candle_pattern=pattern,
                        btc_trend_at_close=btc_trend,
                    )
                    updated += 1
                except Exception:
                    continue
            remaining = max(0, int(batch_size) - processed)

        return PaperTradePatternBackfillResponse(
            repo_scope=safe_scope,
            batch_size=int(batch_size),
            processed=processed,
            updated=updated,
        )

    def get_hourly_windows(
        self,
        days: int = Query(default=settings.paper_trade_hourly_profile_lookback_days, ge=1, le=3650),
        scope: str = Query(default="ENTRY:LIMIT"),
        weekday_vn: int | None = Query(default=None, ge=0, le=6),
        trend_key: str = Query(default="ALL"),
        min_samples: int = Query(default=settings.paper_trade_hourly_bad_window_min_samples, ge=1, le=10000),
        block_win_rate_pct: float = Query(default=settings.paper_trade_hourly_bad_window_block_win_rate_pct, ge=0.0, le=100.0),
        strict_win_rate_pct: float = Query(default=settings.paper_trade_hourly_bad_window_strict_win_rate_pct, ge=0.0, le=100.0),
    ) -> PaperTradeHourlyWindowResponse:
        repo = self._require_repo()
        safe_scope = str(scope or "ENTRY:LIMIT").upper()
        safe_trend = str(trend_key or "ALL").upper()
        if safe_trend not in {"ALL", "LONG", "SHORT", "NEUTRAL"}:
            safe_trend = "ALL"
        scope_tokens: list[str] = [safe_scope]
        if weekday_vn is not None:
            scope_tokens.append(f"DOW:{int(weekday_vn)}")
        if safe_trend != "ALL":
            scope_tokens.append(f"TREND:{safe_trend}")
        query_scope = "|".join(scope_tokens)
        safe_min_samples = max(1, int(min_samples))
        safe_block = float(block_win_rate_pct)
        safe_strict = max(safe_block, float(strict_win_rate_pct))

        repo.refresh_hourly_profiles(lookback_days=days)
        rows = repo.list_hourly_profiles(scope=query_scope)
        by_side_hour: dict[str, dict[int, dict]] = {"ALL": {}, "LONG": {}, "SHORT": {}}
        for row in rows:
            side_key = str(row.get("side_key") or "ALL").upper()
            if side_key not in by_side_hour:
                continue
            hour_vn = int(row.get("hour_vn") or 0)
            if hour_vn < 0 or hour_vn > 23:
                continue
            by_side_hour[side_key][hour_vn] = row

        items: list[PaperTradeHourlyWindow] = []
        for hour_vn in range(24):
            all_stats = self._hourly_stats_from_row(by_side_hour["ALL"].get(hour_vn))
            long_stats = self._hourly_stats_from_row(by_side_hour["LONG"].get(hour_vn))
            short_stats = self._hourly_stats_from_row(by_side_hour["SHORT"].get(hour_vn))

            all_action, all_note = self._classify_hourly_action(
                stats=all_stats,
                min_samples=safe_min_samples,
                block_win_rate_pct=safe_block,
                strict_win_rate_pct=safe_strict,
            )
            long_action, long_note = self._classify_hourly_action(
                stats=long_stats,
                min_samples=safe_min_samples,
                block_win_rate_pct=safe_block,
                strict_win_rate_pct=safe_strict,
            )
            short_action, short_note = self._classify_hourly_action(
                stats=short_stats,
                min_samples=safe_min_samples,
                block_win_rate_pct=safe_block,
                strict_win_rate_pct=safe_strict,
            )

            items.append(
                PaperTradeHourlyWindow(
                    hour_vn=hour_vn,
                    all=PaperTradeHourlySideStats(**all_stats, action=all_action, note=all_note),
                    long=PaperTradeHourlySideStats(**long_stats, action=long_action, note=long_note),
                    short=PaperTradeHourlySideStats(**short_stats, action=short_action, note=short_note),
                    is_bad_window=(long_action in {"BLOCK", "STRICT"} or short_action in {"BLOCK", "STRICT"}),
                )
            )

        current_hour_vn = int(datetime.now(timezone(timedelta(hours=7))).hour)
        return PaperTradeHourlyWindowResponse(
            lookback_days=int(days),
            min_samples=safe_min_samples,
            block_win_rate_pct=safe_block,
            strict_win_rate_pct=safe_strict,
            current_hour_vn=current_hour_vn,
            weekday_vn=(int(weekday_vn) if weekday_vn is not None else None),
            trend_key=safe_trend,
            items=items,
        )

    @staticmethod
    def _now_vn_naive() -> datetime:
        return datetime.now(timezone(timedelta(hours=7))).replace(tzinfo=None)

    @staticmethod
    def _to_vn_naive(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is not None:
            return value.astimezone(timezone(timedelta(hours=7))).replace(tzinfo=None)
        return value

    @classmethod
    def _map_event_window(cls, row: dict, now_vn: datetime | None = None) -> MarketEventWindow:
        starts_at = _parse_dt(row.get("starts_at")) or datetime.utcnow()
        ends_at = _parse_dt(row.get("ends_at")) or starts_at
        if starts_at.tzinfo is not None:
            starts_at = starts_at.astimezone(timezone(timedelta(hours=7))).replace(tzinfo=None)
        if ends_at.tzinfo is not None:
            ends_at = ends_at.astimezone(timezone(timedelta(hours=7))).replace(tzinfo=None)

        now_naive = now_vn or cls._now_vn_naive()
        phase = "PAST"
        minutes_to_start: int | None = None
        minutes_to_end: int | None = None
        if now_naive < starts_at:
            phase = "UPCOMING"
            minutes_to_start = max(0, int(math.ceil((starts_at - now_naive).total_seconds() / 60.0)))
        elif starts_at <= now_naive <= ends_at:
            phase = "ONGOING"
            minutes_to_end = max(0, int(math.ceil((ends_at - now_naive).total_seconds() / 60.0)))

        return MarketEventWindow(
            id=int(row.get("id") or 0),
            title=str(row.get("title") or ""),
            category=str(row.get("category") or "macro"),
            impact_level=str(row.get("impact_level") or "HIGH"),
            starts_at=starts_at,
            ends_at=ends_at,
            expected_volatility_pct=float(row["expected_volatility_pct"]) if row.get("expected_volatility_pct") is not None else None,
            source_url=str(row["source_url"]) if row.get("source_url") is not None else None,
            note=str(row["note"]) if row.get("note") is not None else None,
            is_active=bool(int(row.get("is_active") or 0)),
            created_at=_parse_dt(row.get("created_at")) or datetime.utcnow(),
            updated_at=_parse_dt(row.get("updated_at")) or datetime.utcnow(),
            phase=phase,
            minutes_to_start=minutes_to_start,
            minutes_to_end=minutes_to_end,
        )

    def list_event_windows(
        self,
        phase: str = Query(default="upcoming"),
        days_ahead: int = Query(default=7, ge=1, le=365),
        active_only: bool = Query(default=True),
        limit: int = Query(default=200, ge=1, le=2000),
    ) -> MarketEventWindowListResponse:
        repo = self._require_repo()
        now_vn = self._now_vn_naive()
        phase_key = str(phase or "upcoming").strip().upper()
        if phase_key not in {"ALL", "UPCOMING", "ONGOING", "PAST"}:
            raise HTTPException(status_code=422, detail="phase must be one of: all, upcoming, ongoing, past")

        starts_from: datetime | None = None
        ends_to: datetime | None = None
        if phase_key == "UPCOMING":
            starts_from = now_vn
            ends_to = now_vn + timedelta(days=int(days_ahead))
        elif phase_key == "ONGOING":
            starts_from = now_vn
            ends_to = now_vn
        elif phase_key == "PAST":
            ends_to = now_vn

        rows = repo.list_market_event_windows(
            starts_from=starts_from,
            ends_to=ends_to,
            active_only=bool(active_only),
            limit=int(limit),
        )
        items = [self._map_event_window(row, now_vn=now_vn) for row in rows]
        if phase_key != "ALL":
            items = [item for item in items if item.phase == phase_key]

        return MarketEventWindowListResponse(
            server_time_vn=now_vn,
            phase=phase_key,
            count=len(items),
            items=items,
        )

    def create_event_window(self, req: MarketEventWindowCreateRequest) -> MarketEventWindow:
        repo = self._require_repo()
        starts_at = self._to_vn_naive(req.starts_at)
        ends_at = self._to_vn_naive(req.ends_at)
        if starts_at is None or ends_at is None or ends_at <= starts_at:
            raise HTTPException(status_code=422, detail="ends_at must be greater than starts_at")

        payload = req.dict()
        payload["starts_at"] = starts_at
        payload["ends_at"] = ends_at
        payload["impact_level"] = str(payload.get("impact_level") or "HIGH").upper()
        payload["category"] = str(payload.get("category") or "macro").strip().lower()
        row = repo.create_market_event_window(payload)
        if row is None:
            raise HTTPException(status_code=500, detail="Cannot create event window")
        return self._map_event_window(row, now_vn=self._now_vn_naive())

    def update_event_window(self, event_id: int, req: MarketEventWindowUpdateRequest) -> MarketEventWindow:
        repo = self._require_repo()
        current = repo.get_market_event_window(event_id)
        if current is None:
            raise HTTPException(status_code=404, detail=f"Event window {event_id} not found")

        payload = req.dict(exclude_none=True)
        if "impact_level" in payload:
            payload["impact_level"] = str(payload.get("impact_level") or "HIGH").upper()
        if "category" in payload:
            payload["category"] = str(payload.get("category") or "macro").strip().lower()

        cur_start = self._to_vn_naive(_parse_dt(current.get("starts_at")))
        cur_end = self._to_vn_naive(_parse_dt(current.get("ends_at")))
        next_start = self._to_vn_naive(payload.get("starts_at")) if "starts_at" in payload else cur_start
        next_end = self._to_vn_naive(payload.get("ends_at")) if "ends_at" in payload else cur_end
        if next_start is None or next_end is None or next_end <= next_start:
            raise HTTPException(status_code=422, detail="ends_at must be greater than starts_at")

        payload["starts_at"] = next_start
        payload["ends_at"] = next_end

        row = repo.update_market_event_window(event_id, payload)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Event window {event_id} not found")
        return self._map_event_window(row, now_vn=self._now_vn_naive())

    def disable_event_window(self, event_id: int) -> MarketEventWindow:
        repo = self._require_repo()
        row = repo.disable_market_event_window(event_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Event window {event_id} not found")
        return self._map_event_window(row, now_vn=self._now_vn_naive())

    @staticmethod
    def _normalize_impact_level(raw: str | None) -> str:
        text = str(raw or "").strip().upper()
        if "CRITICAL" in text:
            return "CRITICAL"
        if "HIGH" in text:
            return "HIGH"
        if "MEDIUM" in text:
            return "MEDIUM"
        return "LOW"

    @staticmethod
    def _impact_rank(level: str) -> int:
        key = str(level or "LOW").strip().upper()
        order = {
            "LOW": 1,
            "MEDIUM": 2,
            "HIGH": 3,
            "CRITICAL": 4,
        }
        return int(order.get(key, 1))

    @classmethod
    def _build_forexfactory_window(
        cls,
        date_text: str,
        time_text: str,
        impact_level: str,
    ) -> tuple[datetime, datetime] | None:
        raw_date = str(date_text or "").strip()
        raw_time = str(time_text or "").strip().lower()
        if not raw_date:
            return None

        try:
            day = datetime.strptime(raw_date, "%m-%d-%Y").date()
        except Exception:
            return None

        vn_tz = timezone(timedelta(hours=7))
        all_day_time = raw_time in {"", "all day", "day", "tentative"} or "day" in raw_time

        if all_day_time:
            start_utc = datetime(day.year, day.month, day.day, 0, 0, tzinfo=timezone.utc)
            end_utc = start_utc + timedelta(hours=24)
            start_vn = start_utc.astimezone(vn_tz).replace(tzinfo=None)
            end_vn = end_utc.astimezone(vn_tz).replace(tzinfo=None)
            return start_vn, end_vn

        normalized = raw_time.replace(" ", "").upper()
        parsed_time = None
        for fmt in ("%I:%M%p", "%I%p", "%H:%M"):
            try:
                parsed_time = datetime.strptime(normalized, fmt).time()
                break
            except Exception:
                continue
        if parsed_time is None:
            return None

        event_utc = datetime(
            day.year,
            day.month,
            day.day,
            parsed_time.hour,
            parsed_time.minute,
            tzinfo=timezone.utc,
        )
        impact = cls._normalize_impact_level(impact_level)
        lead_minutes = {
            "LOW": 5,
            "MEDIUM": 10,
            "HIGH": 20,
            "CRITICAL": 30,
        }.get(impact, 10)
        lag_minutes = {
            "LOW": 30,
            "MEDIUM": 60,
            "HIGH": 120,
            "CRITICAL": 180,
        }.get(impact, 60)

        starts_at = (event_utc - timedelta(minutes=lead_minutes)).astimezone(vn_tz).replace(tzinfo=None)
        ends_at = (event_utc + timedelta(minutes=lag_minutes)).astimezone(vn_tz).replace(tzinfo=None)
        if ends_at <= starts_at:
            ends_at = starts_at + timedelta(minutes=30)
        return starts_at, ends_at

    @staticmethod
    def _xml_text(node: ET.Element, tag: str) -> str:
        child = node.find(tag)
        if child is None or child.text is None:
            return ""
        return str(child.text).strip()

    async def _fetch_forexfactory_events(self, feed_url: str) -> list[dict[str, str]]:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            response = await client.get(feed_url)
            response.raise_for_status()

        try:
            root = ET.fromstring(response.content)
        except ET.ParseError as exc:
            raise HTTPException(status_code=502, detail=f"Cannot parse event feed XML: {exc}") from exc

        items: list[dict[str, str]] = []
        for event_node in root.findall(".//event"):
            items.append(
                {
                    "title": self._xml_text(event_node, "title"),
                    "country": self._xml_text(event_node, "country"),
                    "date": self._xml_text(event_node, "date"),
                    "time": self._xml_text(event_node, "time"),
                    "impact": self._xml_text(event_node, "impact"),
                    "forecast": self._xml_text(event_node, "forecast"),
                    "previous": self._xml_text(event_node, "previous"),
                    "url": self._xml_text(event_node, "url"),
                }
            )
        return items

    async def import_event_windows(
        self,
        source: str = Query(default="forexfactory"),
        min_impact: str = Query(default="MEDIUM"),
        days_back: int = Query(default=1, ge=0, le=30),
        days_ahead: int = Query(default=14, ge=1, le=60),
        limit: int = Query(default=300, ge=10, le=2000),
        feed_url: str | None = Query(default=None),
    ) -> MarketEventImportResponse:
        repo = self._require_repo()
        source_key = str(source or "forexfactory").strip().lower()
        if source_key != "forexfactory":
            raise HTTPException(status_code=422, detail="Only source=forexfactory is supported for now")

        min_impact_key = self._normalize_impact_level(min_impact)
        now_vn = self._now_vn_naive()
        from_dt = now_vn - timedelta(days=int(days_back))
        to_dt = now_vn + timedelta(days=int(days_ahead))
        source_feed_url = str(feed_url or "https://nfs.faireconomy.media/ff_calendar_thisweek.xml").strip()

        try:
            feed_items = await self._fetch_forexfactory_events(source_feed_url)
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail=f"Cannot fetch event feed: {exc}") from exc

        inserted = 0
        updated = 0
        skipped = 0
        impact_volatility = {
            "LOW": 0.8,
            "MEDIUM": 1.2,
            "HIGH": 2.0,
            "CRITICAL": 3.0,
        }

        for event in feed_items[: int(limit)]:
            impact = self._normalize_impact_level(event.get("impact"))
            if self._impact_rank(impact) < self._impact_rank(min_impact_key):
                skipped += 1
                continue

            window = self._build_forexfactory_window(
                date_text=str(event.get("date") or ""),
                time_text=str(event.get("time") or ""),
                impact_level=impact,
            )
            if window is None:
                skipped += 1
                continue

            starts_at, ends_at = window
            if ends_at < from_dt or starts_at > to_dt:
                skipped += 1
                continue

            country = str(event.get("country") or "").strip().upper()
            title_raw = str(event.get("title") or "").strip()
            if not title_raw:
                skipped += 1
                continue
            title = f"[{country}] {title_raw}" if country else title_raw

            notes: list[str] = ["Imported from ForexFactory weekly feed"]
            time_text = str(event.get("time") or "").strip()
            if time_text:
                notes.append(f"Raw time: {time_text} UTC")
            forecast = str(event.get("forecast") or "").strip()
            previous = str(event.get("previous") or "").strip()
            if forecast:
                notes.append(f"Forecast: {forecast}")
            if previous:
                notes.append(f"Previous: {previous}")

            payload = {
                "title": title,
                "category": "macro_news",
                "impact_level": impact,
                "starts_at": starts_at,
                "ends_at": ends_at,
                "expected_volatility_pct": impact_volatility.get(impact),
                "source_url": str(event.get("url") or "").strip() or source_feed_url,
                "note": " | ".join(notes),
                "is_active": True,
            }
            row, created = repo.upsert_market_event_window(payload)
            if row is None:
                skipped += 1
                continue
            if created:
                inserted += 1
            else:
                updated += 1

        rows = repo.list_market_event_windows(
            starts_from=from_dt,
            ends_to=to_dt,
            active_only=True,
            limit=min(int(limit), 500),
        )
        items = [self._map_event_window(row, now_vn=now_vn) for row in rows]

        return MarketEventImportResponse(
            source="FOREXFACTORY",
            imported_at_vn=now_vn,
            total_in_feed=len(feed_items),
            inserted=inserted,
            updated=updated,
            skipped=skipped,
            count=len(items),
            items=items,
        )

    async def market_open(self, req: PaperMarketOpenRequest) -> PaperTrade:
        repo = self._resolve_repo(repo_scope=req.repo_scope, entry_type=req.entry_type)
        entry_type = str(req.entry_type or "MARKET").strip().upper() or "MARKET"
        isolated_entry_scope = self._uses_isolated_entry_scope(entry_type)
        if repo.has_open_trade(
            symbol=req.symbol,
            side=req.side,
            entry_type=entry_type if isolated_entry_scope else None,
        ):
            raise HTTPException(status_code=409, detail=f"Open trade already exists for {req.symbol} {req.side}")
        runtime_repo, runtime_engine = get_paper_trade_runtime()
        if runtime_engine is not None and runtime_repo is repo:
            hard_block_reason = runtime_engine._entry_hard_block_reason()
            if hard_block_reason:
                raise HTTPException(status_code=409, detail=str(hard_block_reason))
            if not isolated_entry_scope:
                entry_guard_reason = runtime_engine._entry_guard_reason(symbol=req.symbol, side=req.side)
                if entry_guard_reason:
                    raise HTTPException(status_code=409, detail=str(entry_guard_reason))
            portfolio_guard_reason = runtime_engine._portfolio_guard_reason(side=req.side, entry_type=entry_type)
            if portfolio_guard_reason:
                raise HTTPException(status_code=409, detail=str(portfolio_guard_reason))
            if runtime_engine._is_reentry_cooldown_active(
                symbol=req.symbol,
                side=req.side,
                entry_type=entry_type,
            ):
                raise HTTPException(status_code=409, detail="Reentry cooldown")

        requested_entry_price = req.entry_price
        market_price: float | None = None
        if self.price_stream is not None:
            try:
                stream_price, _ = await self.price_stream.get_price(symbol=req.symbol)
                if stream_price is not None:
                    market_price = float(stream_price)
            except Exception:
                pass

        if market_price is None:
            try:
                ticker = self.market_client.fetch_ticker(req.symbol)
                market_price = ticker.get("last") or ticker.get("close")
                if market_price is None:
                    bid = ticker.get("bid")
                    ask = ticker.get("ask")
                    if bid is not None and ask is not None:
                        market_price = (bid + ask) / 2
                if market_price is None:
                    rows = self.market_client.fetch_ohlcv(req.symbol, timeframe="1m", limit=2)
                    market_price = rows[-1][4] if rows else None
                if market_price is None:
                    raise RuntimeError("No market price")
            except Exception as exc:
                if requested_entry_price is None:
                    raise HTTPException(status_code=503, detail=f"Cannot open market trade for {req.symbol}: {exc}") from exc

        if market_price is None and requested_entry_price is not None:
            market_price = float(requested_entry_price)
        if market_price is None:
            raise HTTPException(status_code=503, detail=f"Cannot open market trade for {req.symbol}: no fill price")

        leverage = self._resolve_open_leverage(req.symbol, req.leverage, req.side)
        if entry_type in {"PUMP_ENTRY_TOUCH", "EMA99_BOUNCE"}:
            normalized_tp = float(req.take_profit)
            normalized_sl = float(req.stop_loss)
        else:
            atr_value = self._resolve_symbol_atr(req.symbol)
            normalized_tp, normalized_sl = normalize_tp_sl(
                side=req.side,
                entry_price=float(market_price),
                take_profit=req.take_profit,
                stop_loss=req.stop_loss,
                min_sl_pct=max(
                    settings.paper_trade_min_sl_pct,
                    calc_min_sl_pct_from_loss(min_sl_loss_pct=settings.paper_trade_min_sl_loss_pct),
                ),
                sl_extra_buffer_pct=settings.paper_trade_sl_extra_buffer_pct,
                atr_value=atr_value,
                sl_atr_multiplier=settings.paper_trade_sl_atr_multiplier,
                min_rr=settings.paper_trade_min_rr,
                max_tp_pct=max(0.0, settings.paper_trade_max_tp_pct) / 100.0,
                leverage=leverage,
                max_margin_loss_pct=settings.paper_trade_max_margin_loss_pct,
            )
        risk_pct = calc_estimated_margin_ratio_pct(
            leverage=leverage,
            maint_margin_rate=settings.paper_trade_maint_margin_rate,
        )
        max_risk_pct = self._resolve_open_max_risk_pct(req.symbol, leverage)
        if risk_pct > max_risk_pct:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Risk too high for {req.symbol}: {risk_pct:.2f}% > "
                    f"max {max_risk_pct:.2f}% (est. margin ratio)"
                ),
            )

        if req.quantity is not None:
            quantity = req.quantity
        else:
            order_usdt = req.order_usdt or settings.paper_trade_order_usdt
            quantity = calc_quantity_from_order_usdt(
                entry_price=float(market_price),
                order_usdt=order_usdt,
                fallback_quantity=settings.paper_trade_quantity,
            )
        margin_usdt = req.margin_usdt or settings.paper_trade_margin_usdt
        if margin_usdt <= 0:
            margin_usdt = calc_margin_usdt(
                entry_price=float(market_price),
                quantity=quantity,
                leverage=leverage,
            )
        feature_snapshot = await asyncio.to_thread(self._capture_feature_snapshot, req.symbol, req.side)
        if isinstance(req.entry_snapshot, dict) and req.entry_snapshot:
            merged_snapshot = dict(feature_snapshot or {})
            for key, value in req.entry_snapshot.items():
                if not isinstance(key, str):
                    continue
                merged_snapshot[key] = value
            feature_snapshot = merged_snapshot
        btc_following: bool | None = None
        if callable(self.btc_follow_resolver):
            try:
                btc_following = bool(self.btc_follow_resolver(req.symbol))
            except Exception:
                btc_following = None

        trade_id = repo.create_open_trade(
            {
                "symbol": req.symbol,
                "side": req.side,
                "btc_following": btc_following,
                "entry_type": entry_type,
                "signal_win_probability": req.signal_win_probability,
                "effective_win_probability": req.effective_win_probability or req.signal_win_probability,
                "entry_price": float(market_price),
                "take_profit": normalized_tp,
                "stop_loss": normalized_sl,
                "reference_win_symbol": req.reference_win_symbol,
                "reference_win_at": req.reference_win_at,
                "entry_point_score": req.entry_point_score,
                "quantity": quantity,
                "margin_usdt": margin_usdt,
                "leverage": leverage,
                "feature_snapshot": feature_snapshot,
            }
        )
        if runtime_engine is not None and runtime_repo is repo and not isolated_entry_scope:
            try:
                runtime_engine.register_open_pressure_event(side=req.side)
                await runtime_engine.apply_open_pressure_profit_exit()
            except Exception:
                pass
        rows = repo.list_recent_trades(limit=1)
        if not rows:
            raise HTTPException(status_code=500, detail=f"Cannot read created trade {trade_id}")
        return self._map_trade(rows[0])

    async def manual_close(
        self,
        trade_id: int,
        req: PaperManualCloseRequest,
        repo_scope: str = Query(default="main", pattern="^(main|candles|auto)$"),
    ) -> PaperTrade:
        repo = self._resolve_repo(repo_scope=repo_scope)
        open_rows = repo.list_open_trades()
        row = next((item for item in open_rows if int(item.get("id") or 0) == int(trade_id)), None)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Open trade {trade_id} not found")

        symbol = str(row["symbol"])
        side = str(row["side"])
        entry = float(row["entry_price"])
        qty = float(row["quantity"])

        close_price: float | None = None
        if self.price_stream is not None:
            try:
                stream_price, _ = await self.price_stream.get_price(symbol=symbol)
                if stream_price is not None:
                    close_price = float(stream_price)
            except Exception:
                close_price = None
        if close_price is None:
            try:
                ticker = self.market_client.fetch_ticker(symbol)
                px = ticker.get("last") or ticker.get("close")
                if px is not None:
                    close_price = float(px)
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"Cannot fetch close price for {symbol}: {exc}") from exc
        if close_price is None:
            raise HTTPException(status_code=503, detail=f"Cannot fetch close price for {symbol}")

        pnl = (close_price - entry) * qty if side == "LONG" else (entry - close_price) * qty
        leverage = int(row.get("leverage") or settings.paper_trade_leverage)
        margin_usdt = (
            float(row["margin_usdt"])
            if row.get("margin_usdt") is not None
            else calc_margin_usdt(entry_price=entry, quantity=qty, leverage=leverage)
        )
        pnl_pct = (pnl / margin_usdt) * 100 if margin_usdt > 0 else 0.0
        prev_mae = float(row.get("mae_pct") or 0.0)
        prev_mfe = float(row.get("mfe_pct") or 0.0)
        repo.update_trade_excursions(
            trade_id=trade_id,
            mae_pct=min(prev_mae, pnl_pct),
            mfe_pct=max(prev_mfe, pnl_pct),
        )
        result = req.force_result if req.force_result is not None else (1 if pnl >= 0 else 0)
        manual_reason = "MANUAL_FORCE_LOSS" if req.force_result == 0 else "MANUAL_FORCE_WIN" if req.force_result == 1 else "MANUAL"
        # Compute exchange fee, entry_type for manual closes is always 'MARKET' (instant close)
        entry_type = str(row.get("entry_type") or "LIMIT")
        fee_taker = float(settings.binance_fee_taker_pct) if hasattr(settings, "binance_fee_taker_pct") else 0.0005
        fee_maker = float(settings.binance_fee_maker_pct) if hasattr(settings, "binance_fee_maker_pct") else 0.0002
        notional = entry * qty
        rate = fee_taker if entry_type.upper() == "MARKET" else fee_maker
        commission = notional * rate * 2
        net_pnl = pnl - commission
        close_candle_pattern, btc_trend_at_close = self._resolve_close_context(
            {
                "symbol": symbol,
                "closed_at": datetime.now(tz=timezone.utc),
            }
        )
        repo.close_trade(
            trade_id=trade_id,
            close_price=close_price,
            pnl=net_pnl,
            result=int(result),
            close_reason=manual_reason,
            commission_usdt=commission,
            close_candle_pattern=close_candle_pattern,
            btc_trend_at_close=btc_trend_at_close,
        )

        recent = repo.list_recent_trades(limit=200)
        closed = next((item for item in recent if int(item.get("id") or 0) == int(trade_id)), None)
        if closed is None:
            raise HTTPException(status_code=500, detail=f"Cannot read closed trade {trade_id}")
        return self._map_trade(closed)

    def _resolve_symbol_atr(self, symbol: str) -> float | None:
        if settings.paper_trade_sl_atr_multiplier <= 0:
            return None
        try:
            rows = self.market_client.fetch_ohlcv(
                symbol=symbol,
                timeframe=settings.paper_trade_sl_atr_timeframe,
                limit=settings.paper_trade_sl_atr_limit,
            )
            return calc_atr_from_ohlcv(rows=rows, period=14)
        except Exception:
            return None

    def _capture_feature_snapshot(self, symbol: str, side: str) -> dict[str, float] | None:
        try:
            row = self.data_pipeline.build_latest_feature_row(symbol=symbol, limit=400)
            if row is None:
                return None
            payload = {k: float(v) for k, v in row.to_dict().items()}
            if side == "LONG":
                payload["setup_side"] = 1.0
            elif side == "SHORT":
                payload["setup_side"] = 0.0
            return payload
        except Exception:
            return None


paper_trade_api = PaperTradeAPI()
router = paper_trade_api.router
