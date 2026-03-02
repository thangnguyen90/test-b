from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import time
from typing import Any

from app.api.signals import get_scan_snapshot
from app.core.config import settings
from app.services.binance_client import BinanceFuturesClient
from app.services.liquidation_ml_predictor import LiquidationMLPredictor
from app.services.ml_predictor import MLPredictor
from app.services.mysql_trade_repo import MySQLTradeRepository
from app.services.risk_manager import (
    calc_atr_from_ohlcv,
    calc_estimated_margin_ratio_pct,
    calc_min_sl_pct_from_loss,
    calc_margin_usdt,
    calc_quantity_from_order_usdt,
    normalize_tp_sl,
)


class PaperTradingEngine:
    def __init__(
        self,
        repo: MySQLTradeRepository,
        predictor: MLPredictor,
        liquid_predictor: LiquidationMLPredictor | None = None,
        price_stream: Any | None = None,
        min_win_probability: float = 0.75,
        quantity: float = 0.01,
        order_usdt: float = 10.0,
        margin_usdt: float = 0.0,
        leverage: int = 5,
        poll_interval_sec: float = 6.0,
        min_sl_pct: float = 0.004,
        min_sl_loss_pct: float = 5.0,
        sl_extra_buffer_pct: float = 0.0,
        sl_atr_multiplier: float = 0.0,
        sl_atr_timeframe: str = "5m",
        sl_atr_limit: int = 120,
        min_rr: float = 1.5,
        maint_margin_rate: float = 0.02,
        max_risk_pct: float = 12.0,
        max_hold_minutes: int = 120,
        disable_sl: bool = False,
        move_sl_to_entry_pnl_pct: float = 15.0,
        move_sl_lock_pnl_pct: float = 10.0,
        liquid_enabled: bool = False,
        liquid_min_win_probability: float = 0.68,
        liquid_top_vol_days: int = 1,
        liquid_max_symbols: int = 30,
        liquid_entry_tolerance_pct: float = 0.003,
    ) -> None:
        self.repo = repo
        self.predictor = predictor
        self.liquid_predictor = liquid_predictor
        self.price_stream = price_stream
        self.market_client = BinanceFuturesClient()
        self.min_win_probability = min_win_probability
        self.quantity = quantity
        self.order_usdt = max(0.0, order_usdt)
        self.margin_usdt = max(0.0, margin_usdt)
        self.leverage = leverage
        self.poll_interval_sec = max(1.0, poll_interval_sec)
        self.min_sl_pct = min_sl_pct
        self.min_sl_loss_pct = max(0.0, min_sl_loss_pct)
        self.sl_extra_buffer_pct = max(0.0, sl_extra_buffer_pct)
        self.sl_atr_multiplier = max(0.0, sl_atr_multiplier)
        self.sl_atr_timeframe = sl_atr_timeframe or "5m"
        self.sl_atr_limit = max(30, min(500, int(sl_atr_limit)))
        self.min_rr = min_rr
        self.maint_margin_rate = max(0.0, maint_margin_rate)
        self.max_risk_pct = max(0.0, max_risk_pct)
        self.max_hold_minutes = max(1, max_hold_minutes)
        self.disable_sl = disable_sl
        self.move_sl_to_entry_pnl_pct = max(0.0, move_sl_to_entry_pnl_pct)
        self.move_sl_lock_pnl_pct = max(0.0, min(float(move_sl_lock_pnl_pct), self.move_sl_to_entry_pnl_pct))
        self.liquid_enabled = liquid_enabled
        self.liquid_min_win_probability = max(0.0, liquid_min_win_probability)
        self.liquid_top_vol_days = max(1, min(7, int(liquid_top_vol_days)))
        self.liquid_max_symbols = max(5, min(80, int(liquid_max_symbols)))
        self.liquid_entry_tolerance_pct = max(0.0008, float(liquid_entry_tolerance_pct))
        self._task: asyncio.Task | None = None
        self._running = False
        self._vn_tz = timezone(timedelta(hours=7))
        self._atr_cache: dict[str, tuple[float, float]] = {}
        self._top_vol_cache: tuple[float, list[str]] | None = None

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _loop(self) -> None:
        while self._running:
            try:
                await self._run_once()
            except Exception:
                # Keep the worker alive even if one iteration fails.
                pass
            await asyncio.sleep(self.poll_interval_sec)

    async def _run_once(self) -> None:
        snapshot = await asyncio.to_thread(get_scan_snapshot, min_win=0.7, max_symbols=100)
        signals = snapshot.get("signals", [])
        open_trades = self.repo.list_open_trades()
        top_vol_symbols: list[str] = []
        if self.liquid_enabled and self.liquid_predictor is not None:
            top_vol_symbols = await asyncio.to_thread(self._load_top_volatility_symbols)

        price_symbols = {str(item.get("symbol")) for item in signals if item.get("symbol")}
        for symbol in top_vol_symbols:
            price_symbols.add(symbol)
        for trade in open_trades:
            symbol = str(trade.get("symbol") or "")
            if symbol:
                price_symbols.add(symbol)
        market_prices = await asyncio.to_thread(self._resolve_market_prices, list(price_symbols))
        stream_prices = await self._resolve_stream_prices(list(price_symbols))
        if stream_prices:
            # Prefer websocket stream cache for realtime TP/SL checks.
            market_prices.update(stream_prices)

        # 1) Open simulated orders when price reaches predicted entry for >=75% setups.
        for item in signals:
            raw_prob = float(item.get("win_probability") or 0.0)
            if raw_prob < self.min_win_probability:
                continue

            symbol = str(item.get("symbol"))
            side = str(item.get("side"))
            if self.repo.has_open_trade(symbol=symbol, side=side):
                continue

            entry = float(item.get("predicted_entry_price") or 0.0)
            tp = float(item.get("take_profit") or 0.0)
            sl = float(item.get("stop_loss") or 0.0)
            if entry <= 0 or tp <= 0 or sl <= 0:
                continue

            market_price = market_prices.get(symbol)
            if market_price is None:
                market_price = await asyncio.to_thread(self._resolve_market_price, symbol)
            if market_price is None:
                continue

            # Blend model signal with realized historical accuracy for this symbol.
            hist_acc = self.repo.symbol_accuracy(symbol=symbol, lookback=300)
            effective_prob = (raw_prob * 0.8 + hist_acc * 0.2) if hist_acc is not None else raw_prob
            if effective_prob < self.min_win_probability:
                continue

            # Trigger condition: market touches/gets through entry.
            touched = self._entry_touched(side=side, market_price=market_price, entry=entry)
            if not touched:
                continue

            normalized_tp, normalized_sl = normalize_tp_sl(
                side=side,
                entry_price=entry,
                take_profit=tp,
                stop_loss=sl,
                min_sl_pct=max(
                    self.min_sl_pct,
                    calc_min_sl_pct_from_loss(min_sl_loss_pct=self.min_sl_loss_pct),
                ),
                sl_extra_buffer_pct=self.sl_extra_buffer_pct,
                atr_value=await self._resolve_symbol_atr(symbol),
                sl_atr_multiplier=self.sl_atr_multiplier,
                min_rr=self.min_rr,
                max_tp_pct=max(0.0, settings.paper_trade_max_tp_pct) / 100.0,
            )
            risk_pct = calc_estimated_margin_ratio_pct(
                leverage=self.leverage,
                maint_margin_rate=self.maint_margin_rate,
            )
            if risk_pct > self.max_risk_pct:
                continue

            quantity = calc_quantity_from_order_usdt(
                entry_price=entry,
                order_usdt=self.order_usdt,
                fallback_quantity=self.quantity,
            )
            margin_usdt = self.margin_usdt
            if margin_usdt <= 0:
                margin_usdt = calc_margin_usdt(entry_price=entry, quantity=quantity, leverage=self.leverage)

            self.repo.create_open_trade(
                {
                    "symbol": symbol,
                    "side": side,
                    "entry_type": "LIMIT",
                    "signal_win_probability": raw_prob,
                    "effective_win_probability": effective_prob,
                    "entry_price": entry,
                    "take_profit": normalized_tp,
                    "stop_loss": normalized_sl,
                    "quantity": quantity,
                    "margin_usdt": margin_usdt,
                    "leverage": self.leverage,
                    "mae_pct": 0.0,
                    "mfe_pct": 0.0,
                }
            )

        # 1b) Separate liquidation+EMA99 model on top volatility symbols.
        if self.liquid_enabled and self.liquid_predictor is not None:
            for symbol in top_vol_symbols:
                market_price = market_prices.get(symbol)
                if market_price is None:
                    market_price = await asyncio.to_thread(self._resolve_market_price, symbol)
                if market_price is None:
                    continue

                try:
                    liq_signal = await asyncio.to_thread(
                        self.liquid_predictor.predict,
                        symbol,
                        float(market_price),
                    )
                except Exception:
                    continue

                raw_prob = float(liq_signal.win_probability)
                if raw_prob < self.liquid_min_win_probability:
                    continue
                side = str(liq_signal.side)
                if self.repo.has_open_trade(symbol=symbol, side=side):
                    continue

                entry = float(liq_signal.predicted_entry_price)
                tp = float(liq_signal.take_profit)
                sl = float(liq_signal.stop_loss)
                if entry <= 0 or tp <= 0 or sl <= 0:
                    continue

                near_entry = abs(float(market_price) - entry) / entry <= self.liquid_entry_tolerance_pct
                if not (near_entry or bool(liq_signal.near_ema)):
                    continue

                hist_acc = self.repo.symbol_accuracy(symbol=symbol, lookback=300)
                effective_prob = (raw_prob * 0.8 + hist_acc * 0.2) if hist_acc is not None else raw_prob
                if effective_prob < self.min_win_probability:
                    continue

                normalized_tp, normalized_sl = normalize_tp_sl(
                    side=side,
                    entry_price=entry,
                    take_profit=tp,
                    stop_loss=sl,
                    min_sl_pct=max(
                        self.min_sl_pct,
                        calc_min_sl_pct_from_loss(min_sl_loss_pct=self.min_sl_loss_pct),
                    ),
                    sl_extra_buffer_pct=self.sl_extra_buffer_pct,
                    atr_value=await self._resolve_symbol_atr(symbol),
                    sl_atr_multiplier=self.sl_atr_multiplier,
                    min_rr=self.min_rr,
                    max_tp_pct=max(0.0, settings.paper_trade_max_tp_pct) / 100.0,
                )
                risk_pct = calc_estimated_margin_ratio_pct(
                    leverage=self.leverage,
                    maint_margin_rate=self.maint_margin_rate,
                )
                if risk_pct > self.max_risk_pct:
                    continue

                quantity = calc_quantity_from_order_usdt(
                    entry_price=entry,
                    order_usdt=self.order_usdt,
                    fallback_quantity=self.quantity,
                )
                margin_usdt = self.margin_usdt
                if margin_usdt <= 0:
                    margin_usdt = calc_margin_usdt(entry_price=entry, quantity=quantity, leverage=self.leverage)

                self.repo.create_open_trade(
                    {
                        "symbol": symbol,
                        "side": side,
                        "entry_type": "LIQ_EMA99",
                        "signal_win_probability": raw_prob,
                        "effective_win_probability": effective_prob,
                        "entry_price": entry,
                        "take_profit": normalized_tp,
                        "stop_loss": normalized_sl,
                        "quantity": quantity,
                        "margin_usdt": margin_usdt,
                        "leverage": self.leverage,
                        "mae_pct": 0.0,
                        "mfe_pct": 0.0,
                    }
                )

        # 2) Manage open trades: close on TP, otherwise apply timeout policy.
        for trade in open_trades:
            symbol = str(trade["symbol"])
            side = str(trade["side"])
            price = market_prices.get(symbol)
            if price is None:
                stream_price = await self._resolve_stream_price(symbol)
                price = stream_price if stream_price is not None else await asyncio.to_thread(self._resolve_market_price, symbol)
            if price is None:
                continue

            entry = float(trade["entry_price"])
            tp = float(trade["take_profit"])
            sl = float(trade["stop_loss"])
            qty = float(trade["quantity"])

            # Once ROI reaches trigger, move SL to a locked-profit level.
            pnl_pct = self._calc_pnl_pct(side=side, entry=entry, mark_price=price, leverage=int(trade["leverage"]))
            prev_mae = float(trade.get("mae_pct") or 0.0)
            prev_mfe = float(trade.get("mfe_pct") or 0.0)
            next_mae = min(prev_mae, pnl_pct)
            next_mfe = max(prev_mfe, pnl_pct)
            if (abs(next_mae - prev_mae) > 1e-9) or (abs(next_mfe - prev_mfe) > 1e-9):
                self.repo.update_trade_excursions(
                    trade_id=int(trade["id"]),
                    mae_pct=next_mae,
                    mfe_pct=next_mfe,
                )
            if not self.disable_sl and pnl_pct >= self.move_sl_to_entry_pnl_pct:
                locked_sl = self._calc_locked_profit_sl(
                    side=side,
                    entry=entry,
                    mark_price=price,
                    leverage=int(trade["leverage"]),
                )
                if locked_sl is not None:
                    if (side == "LONG" and locked_sl > sl) or (side == "SHORT" and locked_sl < sl):
                        self.repo.update_stop_loss(trade_id=int(trade["id"]), stop_loss=locked_sl)
                        sl = locked_sl

            # SL has higher priority than TP per user requirement.
            sl_hit = False
            if not self.disable_sl:
                sl_hit = (side == "LONG" and price <= sl) or (side == "SHORT" and price >= sl)
            if sl_hit:
                pnl = self._calc_pnl(side=side, entry=entry, close_price=price, quantity=qty)
                close_reason = 1 if pnl >= 0 else 0
                self.repo.close_trade(
                    trade_id=int(trade["id"]),
                    close_price=price,
                    pnl=pnl,
                    result=close_reason,
                    close_reason="SL",
                )
                continue

            # Immediate TP exit.
            tp_hit = (side == "LONG" and price >= tp) or (side == "SHORT" and price <= tp)
            if tp_hit:
                pnl = self._calc_pnl(side=side, entry=entry, close_price=price, quantity=qty)
                close_reason = 1 if pnl >= 0 else 0
                self.repo.close_trade(
                    trade_id=int(trade["id"]),
                    close_price=price,
                    pnl=pnl,
                    result=close_reason,
                    close_reason="TP",
                )
                continue

            # Timeout policy.
            if not self._is_expired(trade.get("opened_at")):
                continue

            pnl = self._calc_pnl(side=side, entry=entry, close_price=price, quantity=qty)
            if pnl > 0:
                close_reason = 1
                self.repo.close_trade(
                    trade_id=int(trade["id"]),
                    close_price=price,
                    pnl=pnl,
                    result=close_reason,
                    close_reason="TIMEOUT_PROFIT",
                )
                continue

            # If expired but still in loss: move TP to entry and wait for breakeven exit.
            if abs(tp - entry) > max(1e-9, abs(entry) * 1e-8):
                self.repo.update_take_profit(trade_id=int(trade["id"]), take_profit=entry)

            recovered_to_entry = (side == "LONG" and price >= entry) or (side == "SHORT" and price <= entry)
            if not recovered_to_entry:
                continue

            close_reason = 1 if pnl >= 0 else 0

            self.repo.close_trade(
                trade_id=int(trade["id"]),
                close_price=price,
                pnl=pnl,
                result=close_reason,
                close_reason="TIMEOUT_BREAKEVEN",
            )

    def _resolve_market_price(self, symbol: str) -> float | None:
        try:
            ticker = self.market_client.fetch_ticker(symbol=symbol)
            price = self._extract_price_from_ticker(ticker)
            if price is not None:
                return float(price)
        except Exception:
            pass

        try:
            rows = self.market_client.fetch_ohlcv(symbol=symbol, timeframe="1m", limit=2)
            if rows:
                return float(rows[-1][4])
        except Exception:
            return None

        return None

    def _resolve_market_prices(self, symbols: list[str]) -> dict[str, float]:
        unique = [s for s in sorted(set(symbols)) if s]
        if not unique:
            return {}
        try:
            payload = self.market_client.fetch_tickers(unique)
        except Exception:
            return {}

        out: dict[str, float] = {}
        if not isinstance(payload, dict):
            return out
        for symbol in unique:
            ticker = payload.get(symbol)
            if not isinstance(ticker, dict):
                continue
            price = self._extract_price_from_ticker(ticker)
            if price is not None:
                out[symbol] = float(price)
        return out

    def _load_top_volatility_symbols(self) -> list[str]:
        now = time.time()
        if self._top_vol_cache is not None and (now - self._top_vol_cache[0]) <= 300:
            return list(self._top_vol_cache[1])
        try:
            markets = self.market_client.load_markets()
            all_symbols: list[str] = []
            for market in markets.values():
                if not market.get("active", True):
                    continue
                if market.get("swap") is not True:
                    continue
                if market.get("settle") != "USDT":
                    continue
                sym = market.get("symbol")
                if sym:
                    all_symbols.append(str(sym))
            all_symbols = sorted(set(all_symbols))
            tickers = self.market_client.fetch_tickers(all_symbols[:220])
            ranked: list[tuple[str, float]] = []
            for symbol in all_symbols:
                ticker = tickers.get(symbol) if isinstance(tickers, dict) else None
                if not isinstance(ticker, dict):
                    continue
                pct = ticker.get("percentage")
                if pct is None:
                    pct = ticker.get("change")
                try:
                    ranked.append((symbol, abs(float(pct))))
                except Exception:
                    continue
            ranked.sort(key=lambda x: x[1], reverse=True)
            symbols = [sym for sym, _ in ranked[: self.liquid_max_symbols]]
            self._top_vol_cache = (now, symbols)
            return symbols
        except Exception:
            if self._top_vol_cache is not None:
                return list(self._top_vol_cache[1])
            return []

    async def _resolve_stream_prices(self, symbols: list[str]) -> dict[str, float]:
        if self.price_stream is None or not symbols:
            return {}
        try:
            prices, _, _ = await self.price_stream.get_prices(symbols)
            return {str(k): float(v) for k, v in prices.items() if v is not None}
        except Exception:
            return {}

    async def _resolve_stream_price(self, symbol: str) -> float | None:
        if self.price_stream is None:
            return None
        try:
            price, _ = await self.price_stream.get_price(symbol=symbol)
            return float(price) if price is not None else None
        except Exception:
            return None

    async def _resolve_symbol_atr(self, symbol: str) -> float | None:
        if self.sl_atr_multiplier <= 0:
            return None
        now = time.time()
        cached = self._atr_cache.get(symbol)
        if cached is not None and (now - cached[0]) <= 60:
            return cached[1]
        try:
            rows = await asyncio.to_thread(
                self.market_client.fetch_ohlcv,
                symbol,
                self.sl_atr_timeframe,
                self.sl_atr_limit,
            )
            atr = calc_atr_from_ohlcv(rows=rows, period=14)
            if atr is None:
                return None
            value = float(atr)
            self._atr_cache[symbol] = (now, value)
            return value
        except Exception:
            return None

    @staticmethod
    def _extract_price_from_ticker(ticker: dict[str, Any]) -> float | None:
        price = ticker.get("last") or ticker.get("close")
        if price is not None:
            return float(price)
        bid = ticker.get("bid")
        ask = ticker.get("ask")
        if bid is not None and ask is not None:
            return float((bid + ask) / 2)
        return None

    @staticmethod
    def _entry_touched(side: str, market_price: float, entry: float) -> bool:
        # Fill only when mark is near entry (avoid opening stale LIMIT entries far away).
        tolerance = entry * 0.0008
        return abs(market_price - entry) <= tolerance

    @staticmethod
    def _calc_pnl(side: str, entry: float, close_price: float, quantity: float) -> float:
        if side == "LONG":
            return (close_price - entry) * quantity
        return (entry - close_price) * quantity

    @staticmethod
    def _calc_pnl_pct(side: str, entry: float, mark_price: float, leverage: int) -> float:
        if entry <= 0 or leverage <= 0:
            return 0.0
        move = (mark_price - entry) / entry if side == "LONG" else (entry - mark_price) / entry
        return move * leverage * 100

    def _calc_locked_profit_sl(self, side: str, entry: float, mark_price: float, leverage: int) -> float | None:
        if entry <= 0 or leverage <= 0:
            return None
        if self.move_sl_lock_pnl_pct <= 0:
            return entry

        lock_move_pct = (self.move_sl_lock_pnl_pct / 100.0) / float(leverage)
        if side == "LONG":
            target = entry * (1.0 + lock_move_pct)
            # Keep SL slightly below mark to avoid accidental instant close on update tick.
            return min(target, mark_price * 0.99999)

        target = entry * (1.0 - lock_move_pct)
        # Keep SL slightly above mark to avoid accidental instant close on update tick.
        return max(target, mark_price * 1.00001)

    def _is_expired(self, opened_at: Any) -> bool:
        if opened_at is None:
            return False
        if isinstance(opened_at, datetime):
            dt = opened_at
        else:
            try:
                dt = datetime.fromisoformat(str(opened_at))
            except Exception:
                return False

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self._vn_tz)

        held_seconds = (datetime.now(self._vn_tz) - dt).total_seconds()
        return held_seconds >= (self.max_hold_minutes * 60)
