from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from app.api.signals import get_scan_snapshot
from app.services.binance_client import BinanceFuturesClient
from app.services.ml_predictor import MLPredictor
from app.services.mysql_trade_repo import MySQLTradeRepository
from app.services.risk_manager import normalize_tp_sl


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
        self._task: asyncio.Task | None = None
        self._running = False

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
        snapshot = get_scan_snapshot(min_win=0.7, max_symbols=100)
        signals = snapshot.get("signals", [])

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

            market_price = self._resolve_market_price(symbol)
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

        # 2) Manage open trades: close when TP/SL is hit.
        open_trades = self.repo.list_open_trades()
        for trade in open_trades:
            symbol = str(trade["symbol"])
            side = str(trade["side"])
            price = self._resolve_market_price(symbol)
            if price is None:
                continue

            entry = float(trade["entry_price"])
            tp = float(trade["take_profit"])
            sl = float(trade["stop_loss"])
            qty = float(trade["quantity"])

            close_reason: int | None = None
            if side == "LONG":
                if price >= tp:
                    close_reason = 1
                elif price <= sl:
                    close_reason = 0
            else:
                if price <= tp:
                    close_reason = 1
                elif price >= sl:
                    close_reason = 0

            if close_reason is None:
                continue

            pnl = self._calc_pnl(side=side, entry=entry, close_price=price, quantity=qty)
            self.repo.close_trade(
                trade_id=int(trade["id"]),
                close_price=price,
                pnl=pnl,
                result=close_reason,
            )

    def _resolve_market_price(self, symbol: str) -> float | None:
        try:
            ticker = self.market_client.fetch_ticker(symbol=symbol)
            price = ticker.get("last") or ticker.get("close")
            if price is None:
                bid = ticker.get("bid")
                ask = ticker.get("ask")
                if bid is not None and ask is not None:
                    price = (bid + ask) / 2
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
