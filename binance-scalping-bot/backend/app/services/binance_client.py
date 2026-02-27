from __future__ import annotations

from typing import Any

import ccxt


class BinanceFuturesClient:
    def __init__(self) -> None:
        self.exchange = None

    def _get_exchange(self) -> ccxt.binanceusdm:
        if self.exchange is None:
            self.exchange = ccxt.binanceusdm({"enableRateLimit": True})
        return self.exchange

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 1000) -> list[list[Any]]:
        exchange = self._get_exchange()
        return exchange.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=limit)

    def load_markets(self) -> dict[str, Any]:
        exchange = self._get_exchange()
        return exchange.load_markets()

    def fetch_ticker(self, symbol: str) -> dict[str, Any]:
        exchange = self._get_exchange()
        return exchange.fetch_ticker(symbol=symbol)

    def fetch_tickers(self, symbols: list[str]) -> dict[str, Any]:
        exchange = self._get_exchange()
        return exchange.fetch_tickers(symbols=symbols)
