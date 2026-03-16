from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import math

from fastapi import APIRouter, HTTPException, Query

from app.core.config import settings
from app.models.paper_trades import (
    PaperManualCloseRequest,
    PaperTradeDailySummary,
    PaperTradeDailySummaryResponse,
    PaperTradeHourlySideStats,
    PaperTradeHourlyWindow,
    PaperTradeHourlyWindowResponse,
    PaperMarketOpenRequest,
    PaperTrade,
    PaperTradeListResponse,
    PaperTradeStats,
    PaperTradeStatsResponse,
)
from app.services.binance_client import BinanceFuturesClient
from app.services.data_pipeline import DataPipeline
from app.services.mysql_trade_repo import MySQLTradeRepository
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
        self.price_stream = None
        self.major_symbol_resolver = None
        self.btc_follow_resolver = None
        self.market_client = BinanceFuturesClient()
        self.data_pipeline = DataPipeline()

        self.router.add_api_route("/open", self.get_open, methods=["GET"], response_model=PaperTradeListResponse)
        self.router.add_api_route("/history", self.get_history, methods=["GET"], response_model=PaperTradeListResponse)
        self.router.add_api_route("/stats", self.get_stats, methods=["GET"], response_model=PaperTradeStatsResponse)
        self.router.add_api_route("/daily", self.get_daily_summary, methods=["GET"], response_model=PaperTradeDailySummaryResponse)
        self.router.add_api_route("/hourly-windows", self.get_hourly_windows, methods=["GET"], response_model=PaperTradeHourlyWindowResponse)
        self.router.add_api_route("/market-open", self.market_open, methods=["POST"], response_model=PaperTrade)
        self.router.add_api_route("/close/{trade_id}", self.manual_close, methods=["POST"], response_model=PaperTrade)

    def bind_repo(self, repo: MySQLTradeRepository | None) -> None:
        self.repo = repo

    def bind_price_stream(self, price_stream: object | None) -> None:
        self.price_stream = price_stream

    def bind_major_symbol_resolver(self, resolver: object | None) -> None:
        self.major_symbol_resolver = resolver if callable(resolver) else None

    def bind_btc_follow_resolver(self, resolver: object | None) -> None:
        self.btc_follow_resolver = resolver if callable(resolver) else None

    @staticmethod
    def _normalize_symbol_key(symbol: str) -> str:
        return str(symbol or "").upper().strip().replace(":USDT", "")

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

    def _resolve_max_risk_pct(self, symbol: str) -> float:
        if self._is_major_symbol(symbol):
            return max(float(settings.paper_trade_max_risk_pct), float(settings.paper_trade_major_max_risk_pct))
        return float(settings.paper_trade_max_risk_pct)

    def _require_repo(self) -> MySQLTradeRepository:
        if self.repo is None:
            raise HTTPException(status_code=503, detail="Paper trading DB is not configured")
        return self.repo

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
    def _parse_calendar_date(value: str | None, field_name: str) -> datetime | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return datetime.strptime(text, "%Y-%m-%d")
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"{field_name} must be YYYY-MM-DD") from exc

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
        entry_price = float(row["entry_price"])
        quantity = float(row["quantity"])
        leverage = int(row["leverage"])
        close_price = float(row["close_price"]) if row.get("close_price") is not None else None
        pnl = float(row["pnl"]) if row.get("pnl") is not None else None
        margin_usdt = (
            float(row["margin_usdt"])
            if row.get("margin_usdt") is not None
            else calc_margin_usdt(entry_price=entry_price, quantity=quantity, leverage=leverage)
        )
        pnl_pct = None
        if pnl is not None and margin_usdt > 0:
            pnl_pct = (pnl / margin_usdt) * 100

        row_btc_follow = cls._coerce_optional_bool(row.get("btc_following"))
        resolved_btc_follow = row_btc_follow if row_btc_follow is not None else btc_following

        return PaperTrade(
            id=int(row["id"]),
            symbol=str(row["symbol"]),
            side=str(row["side"]),
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
            quantity=quantity,
            leverage=leverage,
            status=str(row["status"]),
            opened_at=_parse_dt(row.get("opened_at")) or datetime.utcnow(),
            closed_at=_parse_dt(row.get("closed_at")),
            close_price=close_price,
            close_reason=str(row["close_reason"]) if row.get("close_reason") is not None else None,
            pnl=pnl,
            pnl_pct=pnl_pct,
            commission_usdt=float(row["commission_usdt"]) if row.get("commission_usdt") is not None else None,
            mae_pct=float(row["mae_pct"]) if row.get("mae_pct") is not None else None,
            mfe_pct=float(row["mfe_pct"]) if row.get("mfe_pct") is not None else None,
            expected_mae_pct=float(row["expected_mae_pct"]) if row.get("expected_mae_pct") is not None else None,
            expected_mae_samples=int(row["expected_mae_samples"]) if row.get("expected_mae_samples") is not None else None,
            expected_mae_tier=str(row["expected_mae_tier"]) if row.get("expected_mae_tier") is not None else None,
            margin_usdt=margin_usdt,
            result=int(row["result"]) if row.get("result") is not None else None,
        )

    def get_open(self) -> PaperTradeListResponse:
        repo = self._require_repo()
        rows = repo.list_open_trades()
        btc_follow_map = self._resolve_btc_follow_map(rows)
        return PaperTradeListResponse(
            items=[self._map_trade(row, btc_following=btc_follow_map.get(str(row.get("symbol") or ""))) for row in rows]
        )

    def get_history(
        self,
        limit: int = Query(default=200, ge=1, le=2000),
        page: int | None = Query(default=None, ge=1),
        page_size: int | None = Query(default=None, ge=1, le=200),
        from_date: str | None = Query(default=None),
        to_date: str | None = Query(default=None),
    ) -> PaperTradeListResponse:
        repo = self._require_repo()
        from_dt = self._parse_calendar_date(from_date, "from_date")
        to_day_dt = self._parse_calendar_date(to_date, "to_date")
        if from_dt and to_day_dt and from_dt > to_day_dt:
            raise HTTPException(status_code=422, detail="from_date must be <= to_date")
        to_dt_exclusive = (to_day_dt + timedelta(days=1)) if to_day_dt is not None else None
        if page is None and page_size is None:
            rows = repo.list_recent_trades(
                limit=limit,
                from_updated_at=from_dt,
                to_updated_at_exclusive=to_dt_exclusive,
            )
            btc_follow_map = self._resolve_btc_follow_map(rows)
            return PaperTradeListResponse(
                items=[self._map_trade(row, btc_following=btc_follow_map.get(str(row.get("symbol") or ""))) for row in rows]
            )

        target_page = page or 1
        target_page_size = page_size or min(limit, 200)
        rows, total = repo.list_recent_trades_paged(
            page=target_page,
            page_size=target_page_size,
            from_updated_at=from_dt,
            to_updated_at_exclusive=to_dt_exclusive,
        )
        btc_follow_map = self._resolve_btc_follow_map(rows)
        total_pages = max(1, math.ceil(total / target_page_size)) if total > 0 else 1
        return PaperTradeListResponse(
            items=[self._map_trade(row, btc_following=btc_follow_map.get(str(row.get("symbol") or ""))) for row in rows],
            total=total,
            page=min(target_page, total_pages),
            page_size=target_page_size,
            total_pages=total_pages,
        )

    def get_stats(self) -> PaperTradeStatsResponse:
        repo = self._require_repo()
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

    def get_daily_summary(self, days: int = Query(default=30, ge=1, le=365)) -> PaperTradeDailySummaryResponse:
        repo = self._require_repo()
        rows = repo.daily_summary(days=days)
        return PaperTradeDailySummaryResponse(items=[PaperTradeDailySummary(**row) for row in rows])

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

    async def market_open(self, req: PaperMarketOpenRequest) -> PaperTrade:
        repo = self._require_repo()
        if repo.has_open_trade(symbol=req.symbol, side=req.side):
            raise HTTPException(status_code=409, detail=f"Open trade already exists for {req.symbol} {req.side}")

        market_price = req.entry_price
        if market_price is None:
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
                raise HTTPException(status_code=503, detail=f"Cannot open market trade for {req.symbol}: {exc}") from exc

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
            atr_value=self._resolve_symbol_atr(req.symbol),
            sl_atr_multiplier=settings.paper_trade_sl_atr_multiplier,
            min_rr=settings.paper_trade_min_rr,
            max_tp_pct=max(0.0, settings.paper_trade_max_tp_pct) / 100.0,
        )
        leverage = req.leverage or self._resolve_default_leverage(req.symbol)
        risk_pct = calc_estimated_margin_ratio_pct(
            leverage=leverage,
            maint_margin_rate=settings.paper_trade_maint_margin_rate,
        )
        max_risk_pct = self._resolve_max_risk_pct(req.symbol)
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
        expected_mae = None
        try:
            now_vn = datetime.now(timezone(timedelta(hours=7)))
            expected_mae = repo.estimate_expected_mae_pct(
                symbol=req.symbol,
                side=req.side,
                entry_type="MARKET",
                hour_vn=int(now_vn.hour),
                weekday_vn=int(now_vn.weekday()),
                lookback_days=max(1, int(settings.paper_trade_pre_entry_mae_lookback_days)),
                min_samples=max(5, int(settings.paper_trade_pre_entry_mae_min_samples)),
                quantile=float(settings.paper_trade_pre_entry_mae_quantile),
            )
        except Exception:
            expected_mae = None
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
                "entry_type": "MARKET",
                "signal_win_probability": req.signal_win_probability,
                "effective_win_probability": req.effective_win_probability or req.signal_win_probability,
                "entry_price": float(market_price),
                "take_profit": normalized_tp,
                "stop_loss": normalized_sl,
                "quantity": quantity,
                "margin_usdt": margin_usdt,
                "leverage": leverage,
                "expected_mae_pct": (
                    float(expected_mae.get("expected_mae_pct"))
                    if expected_mae and expected_mae.get("expected_mae_pct") is not None
                    else None
                ),
                "expected_mae_samples": (
                    int(expected_mae.get("samples"))
                    if expected_mae and expected_mae.get("samples") is not None
                    else None
                ),
                "expected_mae_tier": (
                    str(expected_mae.get("tier"))
                    if expected_mae and expected_mae.get("tier") is not None
                    else None
                ),
                "feature_snapshot": feature_snapshot,
            }
        )
        rows = repo.list_recent_trades(limit=1)
        if not rows:
            raise HTTPException(status_code=500, detail=f"Cannot read created trade {trade_id}")
        return self._map_trade(rows[0])

    async def manual_close(self, trade_id: int, req: PaperManualCloseRequest) -> PaperTrade:
        repo = self._require_repo()
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
        repo.close_trade(
            trade_id=trade_id,
            close_price=close_price,
            pnl=net_pnl,
            result=int(result),
            close_reason=manual_reason,
            commission_usdt=commission,
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
