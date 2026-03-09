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
    long_short_ratio: float | None = None,
    funding_rate: float | None = None,
    open_interest_notional: float | None = None,
) -> tuple[bool, str, float, bool | None, float, float, float, str | None]:
    try:
        repo, engine = get_paper_trade_runtime()
        if repo is None or engine is None:
            return False, "Paper engine offline", raw_win_probability, None, entry, take_profit, stop_loss, None

        if entry <= 0 or take_profit <= 0 or stop_loss <= 0:
            return False, "Invalid TP/SL", raw_win_probability, None, entry, take_profit, stop_loss, None

        try:
            if bool(engine._is_open_paused()):
                pause_reason = str(getattr(engine, "_open_pause_reason", "") or "").strip()
                return False, (pause_reason or "Open paused"), raw_win_probability, None, entry, take_profit, stop_loss, None
        except Exception:
            pass

        try:
            if repo.has_open_trade(symbol=symbol, side=side):
                return False, "Duplicate", raw_win_probability, None, entry, take_profit, stop_loss, None
        except Exception:
            return False, "Repo unavailable", raw_win_probability, None, entry, take_profit, stop_loss, None

        # Align precheck with engine flip rule:
        # opposite-direction open trades must be profitable before allowing a flip.
        try:
            open_rows = repo.list_open_trades()
        except Exception:
            open_rows = []
        target_side = str(side or "").upper()
        opposite_rows: list[dict] = []
        for row in open_rows:
            row_symbol = str(row.get("symbol") or "")
            row_side = str(row.get("side") or "").upper()
            if row_symbol != symbol:
                continue
            if row_side not in {"LONG", "SHORT"}:
                continue
            if row_side == target_side:
                continue
            opposite_rows.append(row)
        if opposite_rows:
            try:
                has_non_positive_pnl = False
                for row in opposite_rows:
                    row_side = str(row.get("side") or "").upper()
                    row_entry = float(row.get("entry_price") or 0.0)
                    row_qty = float(row.get("quantity") or 0.0)
                    if row_entry <= 0 or row_qty <= 0:
                        has_non_positive_pnl = True
                        break
                    row_pnl = float(
                        engine._calc_pnl(
                            side=row_side,
                            entry=row_entry,
                            close_price=float(market_price),
                            quantity=row_qty,
                        )
                    )
                    if row_pnl <= 0:
                        has_non_positive_pnl = True
                        break
                if has_non_positive_pnl:
                    return False, "Opposite open PnL<=0", raw_win_probability, None, entry, take_profit, stop_loss, None
            except Exception:
                return False, "Opposite check unavailable", raw_win_probability, None, entry, take_profit, stop_loss, None

        min_win = float(getattr(engine, "min_win_probability", 0.75))
        if raw_win_probability < min_win:
            return False, f"Win<{min_win * 100:.1f}%", raw_win_probability, None, entry, take_profit, stop_loss, None

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
            return False, f"EffectiveWin<{min_win * 100:.1f}%", effective_probability, None, entry, take_profit, stop_loss, None

        try:
            safe_to_trade, guard_reason = engine._evaluate_symbol_safety_guard(
                symbol=symbol,
                long_short_ratio=long_short_ratio,
                funding_rate=funding_rate,
                open_interest_notional=open_interest_notional,
            )
        except Exception:
            safe_to_trade, guard_reason = True, "-"
        if not safe_to_trade:
            return False, guard_reason, effective_probability, None, entry, take_profit, stop_loss, None

        entry_adjust_reason: str | None = None
        try:
            entry, take_profit, stop_loss, entry_adjust_reason = engine._apply_low_oi_entry_adjustment(
                symbol=symbol,
                side=side,
                entry=float(entry),
                take_profit=float(take_profit),
                stop_loss=float(stop_loss),
                open_interest_notional=open_interest_notional,
            )
        except Exception:
            entry_adjust_reason = None

        btc_guard: dict = {}
        try:
            btc_guard = engine._resolve_btc_trend_guard()
        except Exception:
            btc_guard = {}

        btc_following: bool | None
        try:
            btc_following = bool(engine._is_symbol_following_btc(symbol))
        except Exception:
            btc_following = None

        try:
            pass_btc_filter = bool(
                engine._pass_btc_filter(
                    symbol=symbol,
                    side=side,
                    effective_prob=effective_probability,
                    btc_guard=btc_guard,
                )
            )
        except Exception:
            pass_btc_filter = True
        if not pass_btc_filter:
            follows_btc = bool(btc_following)
            shock_direction = str((btc_guard or {}).get("shock_direction") or "FLAT").upper()
            if follows_btc and str(side).upper() == "LONG" and shock_direction == "UP":
                return False, "BTC up-shock long block", effective_probability, btc_following, entry, take_profit, stop_loss, entry_adjust_reason
            if follows_btc and str(side).upper() == "SHORT" and shock_direction == "DOWN":
                return False, "BTC down-shock short block", effective_probability, btc_following, entry, take_profit, stop_loss, entry_adjust_reason

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
                return False, f"BTC trend {trend_side}", effective_probability, btc_following, entry, take_profit, stop_loss, entry_adjust_reason
            return False, "BTC filter", effective_probability, btc_following, entry, take_profit, stop_loss, entry_adjust_reason

        try:
            touched = bool(engine._entry_touched(side=side, market_price=market_price, entry=entry))
        except Exception:
            touched = False
        if not touched:
            if entry_adjust_reason:
                return False, entry_adjust_reason, effective_probability, btc_following, entry, take_profit, stop_loss, entry_adjust_reason
            return False, "Entry not touched", effective_probability, btc_following, entry, take_profit, stop_loss, entry_adjust_reason

        try:
            leverage = max(
                1,
                int(
                    engine._resolve_symbol_leverage(
                        symbol,
                        long_short_ratio=long_short_ratio,
                        funding_rate=funding_rate,
                        open_interest_notional=open_interest_notional,
                    )
                ),
            )
            max_risk_pct = max(0.0, float(engine._resolve_symbol_max_risk_pct(symbol)))
            maint_margin_rate = max(0.0, float(getattr(engine, "maint_margin_rate", 0.02)))
            risk_pct = calc_estimated_margin_ratio_pct(
                leverage=leverage,
                maint_margin_rate=maint_margin_rate,
            )
            if risk_pct > max_risk_pct:
                return False, f"Risk {risk_pct:.2f}%>{max_risk_pct:.2f}%", effective_probability, btc_following, entry, take_profit, stop_loss, entry_adjust_reason
        except Exception:
            return False, "Risk check unavailable", effective_probability, btc_following, entry, take_profit, stop_loss, entry_adjust_reason

        return True, "-", effective_probability, btc_following, entry, take_profit, stop_loss, entry_adjust_reason
    except Exception:
        return False, "Precheck unavailable", raw_win_probability, None, entry, take_profit, stop_loss, None


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
            liq_zone_price = float(getattr(signal, "liq_zone_price", 0.0) or 0.0)
            long_short_ratio = float(getattr(signal, "long_short_ratio", 1.0) or 1.0)
            funding_rate = float(getattr(signal, "funding_rate", 0.0) or 0.0)
            open_interest_notional = float(getattr(signal, "open_interest_notional", 0.0) or 0.0)
            sentiment_bias = float(getattr(signal, "sentiment_bias", 0.0) or 0.0)
            liq_zone_value = max(0.0, open_interest_notional * 0.012 * (1.0 + abs(long_short_ratio - 1.0) * 0.35))
            try:
                (
                    can_enter,
                    blocked_reason,
                    effective_win_probability,
                    btc_following,
                    adjusted_entry,
                    adjusted_tp,
                    adjusted_sl,
                    entry_adjust_reason,
                ) = _evaluate_paper_entry_gate(
                    symbol=signal.symbol,
                    side=signal.side,
                    raw_win_probability=float(signal.win_probability),
                    entry=float(signal.predicted_entry_price),
                    take_profit=float(signal.take_profit),
                    stop_loss=float(signal.stop_loss),
                    market_price=float(last_price),
                    long_short_ratio=long_short_ratio,
                    funding_rate=funding_rate,
                    open_interest_notional=open_interest_notional,
                )
            except Exception:
                can_enter = False
                blocked_reason = "Precheck unavailable"
                effective_win_probability = float(signal.win_probability)
                btc_following = None
                adjusted_entry = float(signal.predicted_entry_price)
                adjusted_tp = float(signal.take_profit)
                adjusted_sl = float(signal.stop_loss)
                entry_adjust_reason = None
            matches.append(
                {
                    "symbol": signal.symbol,
                    "side": signal.side,
                    "signal_source": "LIQ_LS_FUND",
                    "win_probability": signal.win_probability,
                    "effective_win_probability": effective_win_probability,
                    "predicted_entry_price": adjusted_entry,
                    "stop_loss": adjusted_sl,
                    "take_profit": adjusted_tp,
                    "tp": adjusted_tp,
                    "mark_price": last_price,
                    "can_enter": can_enter,
                    "blocked_reason": blocked_reason,
                    "entry_adjusted": bool(entry_adjust_reason),
                    "btc_following": btc_following,
                    "liq_zone_price": liq_zone_price,
                    "liq_zone_value": liq_zone_value,
                    "liq_zone_score": float(getattr(signal, "liq_zone_score", 0.0) or 0.0),
                    "near_liq_zone": bool(getattr(signal, "near_liq_zone", False)),
                    "near_ema": bool(getattr(signal, "near_ema", False)),
                    "long_short_ratio": long_short_ratio,
                    "funding_rate": funding_rate,
                    "open_interest_notional": open_interest_notional,
                    "sentiment_bias": sentiment_bias,
                }
            )

    matches.sort(key=lambda item: item["win_probability"], reverse=True)
    
    funding_arb_signals = []
    from app.core.config import settings
    if settings.funding_arb_enabled:
        try:
            from app.services.analytics_service import AnalyticsService
            analytics = AnalyticsService(client=market_client)
            setups = analytics.funding_arbitrage_setups(
                min_rate=settings.funding_arb_min_rate,
                max_minutes=settings.funding_arb_max_minutes,
            )
            for setup in setups:
                funding_arb_signals.append({
                    "symbol": setup.get("symbol"),
                    "side": setup.get("side"),
                    "signal_source": "FUNDING_ARB_AUTO",
                    "funding_rate": setup.get("funding_rate"),
                    "minutes_to_funding": setup.get("minutes_to_funding"),
                    "mark_price": setup.get("mark_price"),
                })
        except Exception:
            pass

    payload = {
        "min_win": min_win,
        "scanned": len(scan_symbols),
        "count": len(matches),
        "signals": matches,
        "funding_arb_signals": funding_arb_signals,
        "source": "live",
        "timestamp": now_iso,
    }
    _LAST_SCAN_CACHE = payload
    return payload


def get_scan_snapshot(min_win: float = 0.6, max_symbols: int = 80, symbols: str | None = None) -> dict:
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
        "signal_source": "LIQ_LS_FUND",
        "win_probability": result.win_probability,
        "predicted_entry_price": result.predicted_entry_price,
        "stop_loss": result.stop_loss,
        "take_profit": result.take_profit,
        "tp": result.take_profit,
        "liq_zone_price": float(getattr(result, "liq_zone_price", 0.0) or 0.0),
        "liq_zone_score": float(getattr(result, "liq_zone_score", 0.0) or 0.0),
        "near_liq_zone": bool(getattr(result, "near_liq_zone", False)),
        "near_ema": bool(getattr(result, "near_ema", False)),
        "long_short_ratio": float(getattr(result, "long_short_ratio", 1.0) or 1.0),
        "funding_rate": float(getattr(result, "funding_rate", 0.0) or 0.0),
        "open_interest_notional": float(getattr(result, "open_interest_notional", 0.0) or 0.0),
        "sentiment_bias": float(getattr(result, "sentiment_bias", 0.0) or 0.0),
    }


@router.get("/scan")
def scan_signals(
    min_win: float = Query(default=0.6, ge=0.0, le=1.0),
    max_symbols: int = Query(default=80, ge=1, le=200),
    symbols: str | None = Query(default=None),
) -> dict:
    return get_scan_snapshot(min_win=min_win, max_symbols=max_symbols, symbols=symbols)
