from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Query

from app.deps import ml_predictor
from app.services.binance_client import BinanceFuturesClient

router = APIRouter(prefix="/api/v1/signals", tags=["signals"])
market_client = BinanceFuturesClient()

_SYMBOLS_CACHE: dict = {"symbols": [], "expires_at": 0.0}
_LAST_SCAN_CACHE: dict | None = None
_BLOCK_UNTIL_TS = 0.0


def get_cached_symbols_snapshot(max_symbols: int | None = None) -> list[str]:
    cached = list(_SYMBOLS_CACHE.get("symbols", []))
    if max_symbols is None:
        return cached
    return cached[:max_symbols]


def _safe_float(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _is_418_error(exc: Exception) -> bool:
    text = str(exc)
    return "418" in text or "I'm a teapot" in text or "Client Error" in text


def _get_usdt_swap_symbols(max_symbols: int, cache_ttl_sec: int = 600) -> list[str]:
    now = time.time()
    if _SYMBOLS_CACHE["symbols"] and now < float(_SYMBOLS_CACHE["expires_at"]):
        return _SYMBOLS_CACHE["symbols"][:max_symbols]

    markets = market_client.load_markets()
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
    _SYMBOLS_CACHE["expires_at"] = now + cache_ttl_sec
    return symbols[:max_symbols]


def _scan_signals_impl(min_win: float, max_symbols: int, symbols: list[str] | None = None) -> dict:
    global _LAST_SCAN_CACHE, _BLOCK_UNTIL_TS

    now_ts = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()

    if now_ts < _BLOCK_UNTIL_TS and _LAST_SCAN_CACHE is not None:
        payload = {
            **_LAST_SCAN_CACHE,
            "source": "cache",
            "blocked_until": datetime.fromtimestamp(_BLOCK_UNTIL_TS, tz=timezone.utc).isoformat(),
            "timestamp": now_iso,
        }
        return payload

    if symbols:
        scan_symbols = symbols[:max_symbols]
    else:
        try:
            scan_symbols = _get_usdt_swap_symbols(max_symbols=max_symbols)
        except Exception:
            scan_symbols = _SYMBOLS_CACHE["symbols"][:max_symbols] if _SYMBOLS_CACHE["symbols"] else []

    matches: list[dict] = []

    try:
        tickers_map = market_client.fetch_tickers(scan_symbols) if scan_symbols else {}
    except Exception as exc:
        if _is_418_error(exc):
            _BLOCK_UNTIL_TS = now_ts + 180
        if _LAST_SCAN_CACHE is not None:
            payload = {
                **_LAST_SCAN_CACHE,
                "source": "cache",
                "error": str(exc),
                "timestamp": now_iso,
            }
            return payload
        tickers_map = {}

    for symbol in scan_symbols:
        try:
            ticker = tickers_map.get(symbol, {}) if isinstance(tickers_map, dict) else {}
            last_price = _safe_float(ticker.get("last"))
            if last_price is None:
                last_price = _safe_float(ticker.get("close"))
            if last_price is None:
                bid = _safe_float(ticker.get("bid"))
                ask = _safe_float(ticker.get("ask"))
                if bid is not None and ask is not None:
                    last_price = (bid + ask) / 2
            if last_price is None:
                continue

            signal = ml_predictor.predict(symbol=symbol, mark_price=last_price)
        except Exception:
            continue

        if signal.win_probability >= min_win:
            matches.append(
                {
                    "symbol": signal.symbol,
                    "side": signal.side,
                    "signal_source": "ML",
                    "win_probability": signal.win_probability,
                    "predicted_entry_price": signal.predicted_entry_price,
                    "stop_loss": signal.stop_loss,
                    "take_profit": signal.take_profit,
                }
            )

    matches.sort(key=lambda item: item["win_probability"], reverse=True)
    payload = {
        "min_win": min_win,
        "scanned": len(scan_symbols),
        "count": len(matches),
        "signals": matches,
        "source": "live",
        "timestamp": now_iso,
    }
    _LAST_SCAN_CACHE = payload
    return payload


def get_scan_snapshot(min_win: float = 0.7, max_symbols: int = 80, symbols: str | None = None) -> dict:
    parsed_symbols = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else None
    return _scan_signals_impl(min_win=min_win, max_symbols=max_symbols, symbols=parsed_symbols)


@router.get("/latest")
def get_latest_signal(
    symbol: str = Query(default="BTC/USDT"),
    mark_price: float = Query(default=100.0, gt=0),
) -> dict:
    result = ml_predictor.predict(symbol=symbol, mark_price=mark_price)
    return {
        "symbol": result.symbol,
        "side": result.side,
        "signal_source": "ML",
        "win_probability": result.win_probability,
        "predicted_entry_price": result.predicted_entry_price,
        "stop_loss": result.stop_loss,
        "take_profit": result.take_profit,
    }


@router.get("/scan")
def scan_signals(
    min_win: float = Query(default=0.7, ge=0.0, le=1.0),
    max_symbols: int = Query(default=80, ge=1, le=200),
    symbols: str | None = Query(default=None),
) -> dict:
    return get_scan_snapshot(min_win=min_win, max_symbols=max_symbols, symbols=symbols)
