import time

from fastapi import APIRouter, HTTPException

from app.api.signals import get_cached_symbols_snapshot
from app.deps import price_stream
from app.services.binance_client import BinanceFuturesClient

router = APIRouter(prefix="/api/v1/market", tags=["market"])

_client = BinanceFuturesClient()
_SYMBOLS_CACHE: dict = {"symbols": [], "expires_at": 0.0}


@router.get("/symbols")
def get_binance_futures_symbols() -> dict:
    now = time.time()
    if _SYMBOLS_CACHE["symbols"] and now < float(_SYMBOLS_CACHE["expires_at"]):
        cached = _SYMBOLS_CACHE["symbols"]
        return {"count": len(cached), "symbols": cached, "source": "cache"}

    try:
        markets = _client.load_markets()
    except Exception as exc:
        if _SYMBOLS_CACHE["symbols"]:
            cached = _SYMBOLS_CACHE["symbols"]
            return {
                "count": len(cached),
                "symbols": cached,
                "source": "cache",
                "error": str(exc),
            }
        shared_cached = get_cached_symbols_snapshot()
        if shared_cached:
            return {
                "count": len(shared_cached),
                "symbols": shared_cached,
                "source": "signals_cache",
                "error": str(exc),
            }
        raise HTTPException(status_code=503, detail=f"Cannot load Binance markets: {exc}") from exc

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
    _SYMBOLS_CACHE["symbols"] = symbols
    _SYMBOLS_CACHE["expires_at"] = now + 600
    return {"count": len(symbols), "symbols": symbols, "source": "live"}


@router.get("/price")
async def get_symbol_price(symbol: str) -> dict:
    stream_price, stream_ts = await price_stream.get_price(symbol=symbol)
    if stream_price is not None:
        return {
            "symbol": symbol,
            "price": float(stream_price),
            "timestamp": stream_ts,
            "source": "binance_stream",
        }

    ticker_error: Exception | None = None
    try:
        ticker = _client.fetch_ticker(symbol=symbol)
        last = ticker.get("last")
        if last is None:
            last = ticker.get("close")
        if last is None:
            bid = ticker.get("bid")
            ask = ticker.get("ask")
            if bid is not None and ask is not None:
                last = (bid + ask) / 2
        if last is not None:
            return {
                "symbol": symbol,
                "price": float(last),
                "timestamp": ticker.get("datetime"),
                "source": "ticker",
            }
    except Exception as exc:
        ticker_error = exc

    # Fallback for symbols where ticker endpoint is unavailable/intermittent.
    try:
        rows = _client.fetch_ohlcv(symbol=symbol, timeframe="1m", limit=2)
        if rows:
            last_row = rows[-1]
            return {
                "symbol": symbol,
                "price": float(last_row[4]),
                "timestamp": None,
                "source": "ohlcv",
            }
    except Exception as ohlcv_exc:
        error_text = f"ticker_error={ticker_error}; ohlcv_error={ohlcv_exc}"
        raise HTTPException(status_code=503, detail=f"Cannot fetch price for {symbol}: {error_text}") from ohlcv_exc

    error_text = f"ticker_error={ticker_error}; ohlcv_error=empty_ohlcv"
    raise HTTPException(status_code=503, detail=f"Cannot fetch price for {symbol}: {error_text}")


@router.get("/prices")
async def get_symbols_prices(symbols: str) -> dict:
    target_symbols = [s.strip() for s in symbols.split(",") if s.strip()]
    if not target_symbols:
        return {"count": 0, "prices": {}, "timestamp": None, "source": "empty"}

    prices, stamp = await price_stream.get_prices(target_symbols)
    missing = [sym for sym in target_symbols if sym not in prices]
    if missing:
        try:
            tickers = _client.fetch_tickers(missing)
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
                    if px is not None:
                        prices[symbol] = float(px)
        except Exception:
            pass

    return {
        "count": len(prices),
        "prices": prices,
        "timestamp": stamp,
        "missing": [sym for sym in target_symbols if sym not in prices],
        "source": "stream+fallback",
    }


@router.get("/klines")
def get_klines(symbol: str, timeframe: str = "5m", limit: int = 1000) -> dict:
    safe_limit = max(100, min(limit, 1500))
    try:
        rows = _client.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=safe_limit)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Cannot fetch klines for {symbol} ({timeframe}): {exc}",
        ) from exc

    candles = [
        {
            "timestamp": int(item[0]),
            "open": float(item[1]),
            "high": float(item[2]),
            "low": float(item[3]),
            "close": float(item[4]),
            "volume": float(item[5]),
        }
        for item in rows
    ]
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "count": len(candles),
        "candles": candles,
    }
