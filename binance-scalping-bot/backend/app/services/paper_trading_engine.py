from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

from app.api.signals import get_scan_snapshot
from app.services.binance_client import BinanceFuturesClient
from app.services.ml_predictor import MLPredictor
from app.services.mysql_trade_repo import MySQLTradeRepository
from app.services.risk_manager import calc_margin_risk_pct, normalize_tp_sl


class PaperTradingEngine:
    def __init__(
        self,
        repo: MySQLTradeRepository,
        predictor: MLPredictor,
        min_win_probability: float = 0.75,
        quantity: float = 0.01,
        leverage: int = 5,
        poll_interval_sec: float = 6.0,
        min_sl_pct: float = 0.004,
        min_rr: float = 1.5,
        max_risk_pct: float = 12.0,
        max_hold_minutes: int = 120,
        disable_sl: bool = False,
        move_sl_to_entry_pnl_pct: float = 15.0,
    ) -> None:
        self.repo = repo
        self.predictor = predictor
        self.market_client = BinanceFuturesClient()
        self.min_win_probability = min_win_probability
        self.quantity = quantity
        self.leverage = leverage
        self.poll_interval_sec = max(2.0, poll_interval_sec)
        self.min_sl_pct = min_sl_pct
        self.min_rr = min_rr
        self.max_risk_pct = max(0.0, max_risk_pct)
        self.max_hold_minutes = max(1, max_hold_minutes)
        self.disable_sl = disable_sl
        self.move_sl_to_entry_pnl_pct = max(0.0, move_sl_to_entry_pnl_pct)
        self._task: asyncio.Task | None = None
        self._running = False
        self._vn_tz = timezone(timedelta(hours=7))

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

        price_symbols = {str(item.get("symbol")) for item in signals if item.get("symbol")}
        for trade in open_trades:
            symbol = str(trade.get("symbol") or "")
            if symbol:
                price_symbols.add(symbol)
        market_prices = await asyncio.to_thread(self._resolve_market_prices, list(price_symbols))

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
                min_sl_pct=self.min_sl_pct,
                min_rr=self.min_rr,
            )
            risk_pct = calc_margin_risk_pct(
                side=side,
                entry_price=entry,
                stop_loss=normalized_sl,
                leverage=self.leverage,
            )
            if risk_pct > self.max_risk_pct:
                continue

            self.repo.create_open_trade(
                {
                    "symbol": symbol,
                    "side": side,
                    "signal_win_probability": raw_prob,
                    "effective_win_probability": effective_prob,
                    "entry_price": entry,
                    "take_profit": normalized_tp,
                    "stop_loss": normalized_sl,
                    "quantity": self.quantity,
                    "leverage": self.leverage,
                }
            )

        # 2) Manage open trades: close on TP, otherwise apply timeout policy.
        for trade in open_trades:
            symbol = str(trade["symbol"])
            side = str(trade["side"])
            price = market_prices.get(symbol)
            if price is None:
                price = await asyncio.to_thread(self._resolve_market_price, symbol)
            if price is None:
                continue

            entry = float(trade["entry_price"])
            tp = float(trade["take_profit"])
            sl = float(trade["stop_loss"])
            qty = float(trade["quantity"])

            # Move SL to breakeven once unrealized ROI on margin is above threshold.
            pnl_pct = self._calc_pnl_pct(side=side, entry=entry, mark_price=price, leverage=int(trade["leverage"]))
            if pnl_pct >= self.move_sl_to_entry_pnl_pct:
                if abs(sl - entry) > max(1e-9, abs(entry) * 1e-8):
                    self.repo.update_stop_loss(trade_id=int(trade["id"]), stop_loss=entry)
                    sl = entry

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
        tolerance = entry * 0.0008
        if side == "LONG":
            return market_price <= (entry + tolerance)
        return market_price >= (entry - tolerance)

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
