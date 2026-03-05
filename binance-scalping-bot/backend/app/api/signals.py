from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Query

from app.deps import get_paper_trade_runtime, ml_predictor
from app.services.binance_client import BinanceFuturesClient
from app.services.risk_manager import calc_estimated_margin_ratio_pct

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


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _estimate_liq_zone(last_price: float, side: str, ticker: dict) -> tuple[float, float]:
    if last_price <= 0:
        return 0.0, 0.0

    quote_volume = max(0.0, _safe_float(ticker.get("quoteVolume")) or 0.0)
    change_pct = _safe_float(ticker.get("percentage")) or 0.0
    long_short_proxy = 1.0 + _clamp(change_pct / 100.0 * 1.8, -0.45, 0.45)

    base_bias = 0.012 + (abs(long_short_proxy - 1.0) * 0.01)
    zone_bias = abs(base_bias) if side == "LONG" else -abs(base_bias)
    liq_zone_price = last_price * (1.0 + zone_bias)

    oi_notional_proxy = quote_volume * 0.22
    liq_zone_value = oi_notional_proxy * 0.012 * (1.0 + abs(long_short_proxy - 1.0) * 0.35)
    return round(liq_zone_price, 6), max(0.0, liq_zone_value)


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


def _evaluate_paper_entry_gate(
    *,
    symbol: str,
    side: str,
    raw_win_probability: float,
    entry: float,
    take_profit: float,
    stop_loss: float,
    market_price: float,
) -> tuple[bool, str, float]:
    try:
        repo, engine = get_paper_trade_runtime()
        if repo is None or engine is None:
            return False, "Paper engine offline", raw_win_probability

        if entry <= 0 or take_profit <= 0 or stop_loss <= 0:
            return False, "Invalid TP/SL", raw_win_probability

        try:
            if bool(engine._is_open_paused()):
                pause_reason = str(getattr(engine, "_open_pause_reason", "") or "").strip()
                return False, (pause_reason or "Open paused"), raw_win_probability
        except Exception:
            pass

        try:
            if repo.has_open_trade(symbol=symbol, side=side, entry_type="LIMIT"):
                return False, "Duplicate", raw_win_probability
        except Exception:
            return False, "Repo unavailable", raw_win_probability

        min_win = float(getattr(engine, "min_win_probability", 0.75))
        if raw_win_probability < min_win:
            return False, f"Win<{min_win * 100:.1f}%", raw_win_probability

        try:
            hist_acc = repo.symbol_accuracy(symbol=symbol, lookback=300)
        except Exception:
            hist_acc = None
        effective_probability = (
            (raw_win_probability * 0.8 + hist_acc * 0.2)
            if hist_acc is not None
            else raw_win_probability
        )
        if effective_probability < min_win:
            return False, f"EffectiveWin<{min_win * 100:.1f}%", effective_probability

        btc_guard: dict = {}
        try:
            btc_guard = engine._resolve_btc_trend_guard()
        except Exception:
            btc_guard = {}

        try:
            pass_btc_filter = bool(
                engine._pass_btc_filter(
                    side=side,
                    effective_prob=effective_probability,
                    btc_guard=btc_guard,
                )
            )
        except Exception:
            pass_btc_filter = True
        if not pass_btc_filter:
            try:
                blocked_by_up_shock = not bool(engine._pass_btc_up_shock_long_guard(side=side, btc_guard=btc_guard))
            except Exception:
                blocked_by_up_shock = False
            if blocked_by_up_shock:
                return False, "BTC up-shock long block", effective_probability

            trend_side = str((btc_guard or {}).get("side") or "NEUTRAL").upper()
            confidence = _safe_float((btc_guard or {}).get("confidence")) or 0.0
            min_conf = float(getattr(engine, "btc_filter_min_confidence", 0.0))
            block_countertrend = bool(getattr(engine, "btc_filter_block_countertrend", False))
            if (
                block_countertrend
                and trend_side in {"LONG", "SHORT"}
                and str(side).upper() != trend_side
                and confidence >= min_conf
            ):
                return False, f"BTC trend {trend_side}", effective_probability
            return False, "BTC filter", effective_probability

        try:
            touched = bool(engine._entry_touched(side=side, market_price=market_price, entry=entry))
        except Exception:
            touched = False
        if not touched:
            return False, "Entry not touched", effective_probability

        try:
            leverage = max(1, int(engine._resolve_symbol_leverage(symbol)))
            max_risk_pct = max(0.0, float(engine._resolve_symbol_max_risk_pct(symbol)))
            maint_margin_rate = max(0.0, float(getattr(engine, "maint_margin_rate", 0.02)))
            risk_pct = calc_estimated_margin_ratio_pct(
                leverage=leverage,
                maint_margin_rate=maint_margin_rate,
            )
            if risk_pct > max_risk_pct:
                return False, f"Risk {risk_pct:.2f}%>{max_risk_pct:.2f}%", effective_probability
        except Exception:
            return False, "Risk check unavailable", effective_probability

        return True, "-", effective_probability
    except Exception:
        return False, "Precheck unavailable", raw_win_probability


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
            liq_zone_price, liq_zone_value = _estimate_liq_zone(
                last_price=last_price,
                side=signal.side,
                ticker=ticker if isinstance(ticker, dict) else {},
            )
            try:
                can_enter, blocked_reason, effective_win_probability = _evaluate_paper_entry_gate(
                    symbol=signal.symbol,
                    side=signal.side,
                    raw_win_probability=float(signal.win_probability),
                    entry=float(signal.predicted_entry_price),
                    take_profit=float(signal.take_profit),
                    stop_loss=float(signal.stop_loss),
                    market_price=float(last_price),
                )
            except Exception:
                can_enter = False
                blocked_reason = "Precheck unavailable"
                effective_win_probability = float(signal.win_probability)
            matches.append(
                {
                    "symbol": signal.symbol,
                    "side": signal.side,
                    "signal_source": "ML",
                    "win_probability": signal.win_probability,
                    "effective_win_probability": effective_win_probability,
                    "predicted_entry_price": signal.predicted_entry_price,
                    "stop_loss": signal.stop_loss,
                    "take_profit": signal.take_profit,
                    "mark_price": last_price,
                    "can_enter": can_enter,
                    "blocked_reason": blocked_reason,
                    "liq_zone_price": liq_zone_price,
                    "liq_zone_value": liq_zone_value,
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
