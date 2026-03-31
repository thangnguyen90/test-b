from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, Query

from app.core.config import settings
from app.deps import get_paper_trade_runtime, ml_candles_predictor, ml_predictor, ml_test_predictor
from app.services.binance_client import BinanceFuturesClient
from app.services.risk_manager import calc_estimated_margin_ratio_pct
from app.services.signal_candle_pattern_service import signal_candle_pattern_service

router = APIRouter(prefix="/api/v1/signals", tags=["signals"])
market_client = BinanceFuturesClient()

_SYMBOLS_CACHE: dict = {"symbols": [], "expires_at": 0.0}
_LAST_SCAN_CACHE: dict[str, dict] = {}
_BLOCK_UNTIL_TS = 0.0
_FALLBACK_SCAN_SYMBOLS: list[str] = [
    'BTC/USDT:USDT',
    'ETH/USDT:USDT',
    'SOL/USDT:USDT',
    'XRP/USDT:USDT',
    'BNB/USDT:USDT',
    'DOGE/USDT:USDT',
    'ADA/USDT:USDT',
    'TRX/USDT:USDT',
    'LINK/USDT:USDT',
    'AVAX/USDT:USDT',
    'DOT/USDT:USDT',
    'SUI/USDT:USDT',
    'TON/USDT:USDT',
    'NEAR/USDT:USDT',
    'ATOM/USDT:USDT',
    'LTC/USDT:USDT',
    'BCH/USDT:USDT',
    'APT/USDT:USDT',
    'ARB/USDT:USDT',
    'OP/USDT:USDT',
]


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


def _activity_score_from_ticker(ticker: dict) -> float:
    if not isinstance(ticker, dict):
        return 0.0
    quote_volume = max(0.0, _safe_float(ticker.get("quoteVolume")) or 0.0)
    change_pct = abs(_safe_float(ticker.get("percentage")) or 0.0)
    trade_count = max(0.0, _safe_float(ticker.get("count")) or 0.0)
    high = _safe_float(ticker.get("high"))
    low = _safe_float(ticker.get("low"))
    intraday_range_pct = ((high - low) / low * 100.0) if high is not None and low is not None and low > 0 else 0.0
    momentum_boost = 1.0 + min(change_pct, 40.0) / 20.0 + min(intraday_range_pct, 20.0) / 20.0
    return quote_volume * momentum_boost + trade_count * 100.0


def _get_usdt_swap_symbols(max_symbols: int, cache_ttl_sec: int | None = None) -> list[str]:
    ttl_sec = max(30, int(cache_ttl_sec or settings.signals_active_symbols_cache_sec))
    now = time.time()
    if _SYMBOLS_CACHE["symbols"] and now < float(_SYMBOLS_CACHE["expires_at"]):
        return _SYMBOLS_CACHE["symbols"][:max_symbols]

    symbols: list[str] = []
    try:
        markets = market_client.load_markets()
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
    except Exception:
        symbols = []

    symbols = sorted(set(symbols)) if symbols else list(_FALLBACK_SCAN_SYMBOLS)
    _SYMBOLS_CACHE["symbols"] = symbols
    _SYMBOLS_CACHE["expires_at"] = now + ttl_sec
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
    entry_type: str = "LIMIT",
    force_entry_type_scope: bool = False,
    candle_pattern_sample: dict | None = None,
    feature_snapshot: dict | None = None,
) -> tuple[bool, str, float, bool | None]:
    try:
        repo, engine = get_paper_trade_runtime()
        if repo is None or engine is None:
            return False, "Paper engine offline", raw_win_probability, None

        if entry <= 0 or take_profit <= 0 or stop_loss <= 0:
            return False, "Invalid TP/SL", raw_win_probability, None

        normalized_entry_type = str(entry_type or "LIMIT").strip().upper() or "LIMIT"
        try:
            skip_btc_guards = bool(engine._skip_btc_guards_for_entry_type(normalized_entry_type))
        except Exception:
            skip_btc_guards = normalized_entry_type.startswith("ML_CANDLES") and normalized_entry_type != "ML_CANDLES_BG"

        btc_guard: dict = {}
        if not skip_btc_guards:
            try:
                btc_guard = engine._resolve_btc_trend_guard()
            except Exception:
                btc_guard = {}

        btc_following: bool | None
        try:
            btc_following = bool(engine._is_symbol_following_btc(symbol))
        except Exception:
            btc_following = None

        if normalized_entry_type == "LIMIT":
            try:
                basic_ml_short_guard_reason = engine._basic_ml_post_dump_short_guard_reason(
                    symbol=symbol,
                    side=side,
                    btc_guard=btc_guard,
                )
                if basic_ml_short_guard_reason:
                    return False, str(basic_ml_short_guard_reason), raw_win_probability, btc_following
            except Exception:
                pass
            try:
                basic_ml_long_guard_reason = engine._basic_ml_post_pump_long_guard_reason(
                    symbol=symbol,
                    side=side,
                    btc_guard=btc_guard,
                )
                if basic_ml_long_guard_reason:
                    return False, str(basic_ml_long_guard_reason), raw_win_probability, btc_following
            except Exception:
                pass
            try:
                basic_ml_candle_confirm_reason = engine._basic_ml_btc_candle_confirmation_reason(
                    symbol=symbol,
                    side=side,
                    btc_guard=btc_guard,
                )
                if basic_ml_candle_confirm_reason:
                    return False, str(basic_ml_candle_confirm_reason), raw_win_probability, btc_following
            except Exception:
                pass

        try:
            entry_block_reason = engine._entry_block_reason(
                symbol=symbol,
                side=side,
                entry_type=normalized_entry_type,
                btc_guard=btc_guard,
            )
            if entry_block_reason:
                return False, str(entry_block_reason), raw_win_probability, btc_following
        except Exception:
            pass
        try:
            if bool(engine._is_open_paused()):
                if not engine._should_bypass_instant_sl_global_pause_for_entry_type(normalized_entry_type):
                    pause_reason = str(getattr(engine, "_open_pause_reason", "") or "").strip()
                    return False, (pause_reason or "Open paused"), raw_win_probability, btc_following
        except Exception:
            pass
        try:
            if not engine._should_bypass_instant_sl_symbol_guard_for_entry_type(normalized_entry_type):
                instant_sl_guard_reason = engine._instant_sl_guard_reason(symbol=symbol, side=side)
                if instant_sl_guard_reason:
                    return False, str(instant_sl_guard_reason), raw_win_probability, btc_following
        except Exception:
            pass

        try:
            if bool(
                engine._has_conflicting_open_trade(
                    symbol=symbol,
                    side=side,
                    entry_type=normalized_entry_type,
                    force_entry_type_scope=force_entry_type_scope,
                )
            ):
                return False, "Duplicate", raw_win_probability, btc_following
        except Exception:
            return False, "Repo unavailable", raw_win_probability, btc_following
        try:
            reentry_cooldown_reason = engine._reentry_cooldown_reason(
                symbol=symbol,
                side=side,
                entry_type=normalized_entry_type,
                force_entry_type_scope=force_entry_type_scope,
            )
            if reentry_cooldown_reason:
                return False, str(reentry_cooldown_reason), raw_win_probability, btc_following
        except Exception:
            pass

        try:
            open_rows = repo.list_open_trades()
        except Exception:
            open_rows = []
        target_side = str(side or "").upper()
        opposite_rows: list[dict] = []
        for row in open_rows:
            row_symbol = str(row.get("symbol") or "")
            row_side = str(row.get("side") or "").upper()
            row_entry_type = str(row.get("entry_type") or "").strip().upper()
            if row_symbol != symbol:
                continue
            if row_side not in {"LONG", "SHORT"}:
                continue
            if force_entry_type_scope and row_entry_type != normalized_entry_type:
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
                    return False, "Opposite open PnL<=0", raw_win_probability, btc_following
            except Exception:
                return False, "Opposite check unavailable", raw_win_probability, btc_following

        min_win = float(getattr(engine, "min_win_probability", 0.75))
        if raw_win_probability < min_win:
            return False, f"Win<{min_win * 100:.1f}%", raw_win_probability, btc_following

        try:
            hist_acc = repo.symbol_accuracy(symbol=symbol, lookback=300)
        except Exception:
            hist_acc = None
        effective_probability = (
            (raw_win_probability * 0.8 + hist_acc * 0.2)
            if hist_acc is not None
            else raw_win_probability
        )
        penalty_reason: str | None = None
        if normalized_entry_type.startswith("ML_CANDLES"):
            try:
                effective_probability, penalty_reason = engine._apply_recent_symbol_behavior_penalty(
                    symbol=symbol,
                    side=side,
                    entry_type=normalized_entry_type,
                    effective_prob=effective_probability,
                    force_entry_type_scope=force_entry_type_scope,
                )
            except Exception:
                penalty_reason = None
        if effective_probability < min_win:
            if penalty_reason:
                return False, str(penalty_reason), effective_probability, btc_following
            return False, f"EffectiveWin<{min_win * 100:.1f}%", effective_probability, btc_following

        if normalized_entry_type == "LIMIT":
            try:
                basic_pattern_reason, _, _ = engine._evaluate_basic_ml_pattern_gate(
                    symbol=symbol,
                    side=side,
                    candle_pattern_sample=candle_pattern_sample,
                    feature_snapshot=feature_snapshot,
                )
            except Exception:
                return False, "Basic pattern gate unavailable", effective_probability, btc_following
            if basic_pattern_reason:
                return False, str(basic_pattern_reason), effective_probability, btc_following

        if not skip_btc_guards:
            try:
                trend_hour_lock_reason = engine._btc_trend_hour_lock_reason(
                    symbol=symbol,
                    side=side,
                    btc_guard=btc_guard,
                )
                if trend_hour_lock_reason:
                    return False, str(trend_hour_lock_reason), effective_probability, btc_following
            except Exception:
                pass

            try:
                kill_short_reason = engine._btc_kill_short_guard_reason(
                    side=side,
                    btc_guard=btc_guard,
                )
                if kill_short_reason:
                    return False, str(kill_short_reason), effective_probability, btc_following
            except Exception:
                pass

            try:
                can_open_now, required_min_win = engine._evaluate_hourly_bad_window_guard(
                    side=side,
                    entry_type=normalized_entry_type,
                    base_min_win=min_win,
                    btc_guard=btc_guard,
                )
                if not can_open_now:
                    return False, "Hourly bad window", effective_probability, btc_following
                required_min_win = float(
                    engine._apply_bullish_short_nonfollow_min_win_bonus(
                        required_min_win=required_min_win,
                        side=side,
                        symbol=symbol,
                        btc_guard=btc_guard,
                    )
                )
                if normalized_entry_type == "LIMIT":
                    required_min_win = float(
                        engine._apply_basic_ml_post_dump_short_min_win(
                            required_min_win=required_min_win,
                            symbol=symbol,
                            side=side,
                            btc_guard=btc_guard,
                        )
                    )
                    required_min_win = float(
                        engine._apply_basic_ml_post_pump_long_min_win(
                            required_min_win=required_min_win,
                            symbol=symbol,
                            side=side,
                            btc_guard=btc_guard,
                        )
                    )
                if effective_probability < required_min_win:
                    return False, f"EffectiveWin<{required_min_win * 100:.1f}%", effective_probability, btc_following
            except Exception:
                pass

        try:
            if not bool(engine._pass_short_sl_streak_guard(side=side)):
                return False, "Short SL cooldown", effective_probability, btc_following
        except Exception:
            pass

        if not skip_btc_guards:
            try:
                open_index = engine._index_open_trades_by_symbol(open_rows)
                if not bool(
                    engine._pass_bullish_short_nonfollow_ratio_guard(
                        side=side,
                        symbol=symbol,
                        btc_guard=btc_guard,
                        open_trades_by_symbol=open_index,
                    )
                ):
                    return False, "Short ratio guard", effective_probability, btc_following
            except Exception:
                pass

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
                    return False, "BTC up-shock long block", effective_probability, btc_following
                if follows_btc and str(side).upper() == "SHORT" and shock_direction == "DOWN":
                    return False, "BTC down-shock short block", effective_probability, btc_following

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
                    return False, f"BTC trend {trend_side}", effective_probability, btc_following
                return False, "BTC filter", effective_probability, btc_following

        try:
            entry, take_profit, stop_loss = engine._adjust_entry_for_btc_kill_short(
                side=side,
                entry=entry,
                take_profit=take_profit,
                stop_loss=stop_loss,
                market_price=market_price,
                btc_guard=btc_guard,
            )
        except Exception:
            pass

        if normalized_entry_type == "ML_CANDLES_BG":
            try:
                entry_timing_reason = engine._ml_candles_bg_entry_timing_reason(
                    symbol=symbol,
                    side=side,
                    market_price=float(market_price),
                    entry=entry,
                    btc_guard=btc_guard,
                )
            except Exception:
                entry_timing_reason = "Entry not touched"
            if entry_timing_reason:
                return False, str(entry_timing_reason), effective_probability, btc_following
        else:
            try:
                touched = bool(engine._entry_touched(side=side, market_price=market_price, entry=entry))
            except Exception:
                touched = False
            if not touched:
                return False, "Entry not touched", effective_probability, btc_following

        try:
            leverage = max(1, int(engine._resolve_symbol_leverage(symbol)))
            max_risk_pct = max(0.0, float(engine._resolve_symbol_max_risk_pct(symbol)))
            maint_margin_rate = max(0.0, float(getattr(engine, "maint_margin_rate", 0.02)))
            risk_pct = calc_estimated_margin_ratio_pct(
                leverage=leverage,
                maint_margin_rate=maint_margin_rate,
            )
            if risk_pct > max_risk_pct:
                return False, f"Risk {risk_pct:.2f}%>{max_risk_pct:.2f}%", effective_probability, btc_following
        except Exception:
            return False, "Risk check unavailable", effective_probability, btc_following

        return True, "-", effective_probability, btc_following
    except Exception:
        return False, "Precheck unavailable", raw_win_probability, None


def _resolve_live_btc_phase() -> str | None:
    try:
        _, engine = get_paper_trade_runtime()
        if engine is None:
            return None
        btc_guard = engine._resolve_btc_trend_guard()
    except Exception:
        return None
    trend_side = str((btc_guard or {}).get("side") or "NEUTRAL").upper()
    shock_direction = str((btc_guard or {}).get("shock_direction") or "FLAT").upper()
    confidence = _safe_float((btc_guard or {}).get("confidence")) or 0.0
    if trend_side in {"LONG", "SHORT"} and confidence >= 0.55:
        if shock_direction in {"UP", "DOWN"}:
            return f"{trend_side}_SHOCK_{shock_direction}"
        return f"{trend_side}_TREND"
    if shock_direction in {"UP", "DOWN"}:
        return f"SHOCK_{shock_direction}"
    return "NEUTRAL"


def _build_compare_signal_payload(
    *,
    symbol: str,
    mark_price: float,
    predictor,
    target_side: str | None = None,
) -> dict | None:
    try:
        compare_signal = predictor.predict(symbol=symbol, mark_price=mark_price)
    except Exception:
        return None
    return {
        "side": compare_signal.side,
        "win_probability": compare_signal.win_probability,
        "predicted_entry_price": compare_signal.predicted_entry_price,
        "take_profit": compare_signal.take_profit,
        "reference_win_symbol": getattr(compare_signal, "reference_win_symbol", None),
        "reference_win_at": getattr(compare_signal, "reference_win_at", None),
        "aligned": compare_signal.side == str(target_side or "").upper(),
    }


def _build_scan_match(
    *,
    signal,
    signal_source: str,
    last_price: float,
    ticker: dict,
    compare_field: str | None = None,
    compare_payload: dict | None = None,
    gate_entry_type: str = "LIMIT",
    force_entry_type_scope: bool = False,
    candle_pattern_sample: dict | None = None,
) -> dict:
    liq_zone_price, liq_zone_value = _estimate_liq_zone(
        last_price=last_price,
        side=signal.side,
        ticker=ticker if isinstance(ticker, dict) else {},
    )
    try:
        can_enter, blocked_reason, effective_win_probability, btc_following = _evaluate_paper_entry_gate(
            symbol=signal.symbol,
            side=signal.side,
            raw_win_probability=float(signal.win_probability),
            entry=float(signal.predicted_entry_price),
            take_profit=float(signal.take_profit),
            stop_loss=float(signal.stop_loss),
            market_price=float(last_price),
            entry_type=gate_entry_type,
            force_entry_type_scope=force_entry_type_scope,
            candle_pattern_sample=candle_pattern_sample,
            feature_snapshot=getattr(signal, "feature_snapshot", None),
        )
    except Exception:
        can_enter = False
        blocked_reason = "Precheck unavailable"
        effective_win_probability = float(signal.win_probability)
        btc_following = None
    payload = {
        "symbol": signal.symbol,
        "side": signal.side,
        "signal_source": signal_source,
        "win_probability": signal.win_probability,
        "effective_win_probability": effective_win_probability,
        "predicted_entry_price": signal.predicted_entry_price,
        "stop_loss": signal.stop_loss,
        "take_profit": signal.take_profit,
        "mark_price": last_price,
        "can_enter": can_enter,
        "blocked_reason": blocked_reason,
        "btc_following": btc_following,
        "liq_zone_price": liq_zone_price,
        "liq_zone_value": liq_zone_value,
        "reference_win_symbol": getattr(signal, "reference_win_symbol", None),
        "reference_win_at": getattr(signal, "reference_win_at", None),
        "candle_pattern_sample": candle_pattern_sample,
    }
    if compare_field:
        payload[compare_field] = compare_payload
    if signal_source == "ML":
        aligned = bool((compare_payload or {}).get("aligned"))
        if not aligned:
            payload["can_enter"] = False
            payload["blocked_reason"] = "Candles opposite" if compare_payload else "Candles unavailable"
    return payload


def _scan_signals_impl(min_win: float, max_symbols: int, symbols: list[str] | None = None) -> dict:
    global _LAST_SCAN_CACHE, _BLOCK_UNTIL_TS

    now_ts = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()
    normalized_symbols = tuple(str(s or "").strip().upper() for s in (symbols or []) if str(s or "").strip())
    cache_key = f"{float(min_win):.6f}|{int(max_symbols)}|{'/'.join(normalized_symbols)}"
    cached_payload = _LAST_SCAN_CACHE.get(cache_key)

    if now_ts < _BLOCK_UNTIL_TS and cached_payload is not None:
        payload = {
            **cached_payload,
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
    live_btc_phase = _resolve_live_btc_phase()

    try:
        tickers_map = market_client.fetch_tickers(scan_symbols) if scan_symbols else {}
    except Exception as exc:
        if _is_418_error(exc):
            _BLOCK_UNTIL_TS = now_ts + 180
        if cached_payload is not None:
            payload = {
                **cached_payload,
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
            ml_candles_payload = _build_compare_signal_payload(
                symbol=symbol,
                mark_price=float(last_price),
                predictor=ml_candles_predictor,
                target_side=signal.side,
            )
            candle_pattern_sample = signal_candle_pattern_service.match_signal(
                signal_source="ML",
                side=signal.side,
                feature_snapshot=getattr(signal, "feature_snapshot", None),
                live_btc_phase=live_btc_phase,
            )
            matches.append(
                _build_scan_match(
                    signal=signal,
                    signal_source="ML",
                    last_price=float(last_price),
                    ticker=ticker if isinstance(ticker, dict) else {},
                    compare_field="ml_candles",
                    compare_payload=ml_candles_payload,
                    gate_entry_type="LIMIT",
                    candle_pattern_sample=candle_pattern_sample,
                )
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
    _LAST_SCAN_CACHE[cache_key] = payload
    if len(_LAST_SCAN_CACHE) > 24:
        for old_key in list(_LAST_SCAN_CACHE.keys())[:-24]:
            _LAST_SCAN_CACHE.pop(old_key, None)
    return payload


def _scan_candles_signals_impl(
    min_win: float,
    max_symbols: int,
    symbols: list[str] | None = None,
    *,
    response_signal_source: str = "ML_CANDLES",
    gate_entry_type: str = "ML_CANDLES_TEST",
) -> dict:
    if symbols:
        scan_symbols = symbols[:max_symbols]
    else:
        try:
            scan_symbols = _get_usdt_swap_symbols(max_symbols=max_symbols)
        except Exception:
            scan_symbols = _SYMBOLS_CACHE["symbols"][:max_symbols] if _SYMBOLS_CACHE["symbols"] else []

    matches: list[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()
    live_btc_phase = _resolve_live_btc_phase()
    try:
        tickers_map = market_client.fetch_tickers(scan_symbols) if scan_symbols else {}
    except Exception:
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

            signal = ml_candles_predictor.predict(symbol=symbol, mark_price=last_price)
        except Exception:
            continue

        if signal.win_probability >= min_win:
            baseline_ml_payload = _build_compare_signal_payload(
                symbol=symbol,
                mark_price=float(last_price),
                predictor=ml_predictor,
                target_side=signal.side,
            )
            candle_pattern_sample = signal_candle_pattern_service.match_signal(
                signal_source="ML_CANDLES",
                side=signal.side,
                feature_snapshot=getattr(signal, "feature_snapshot", None),
                live_btc_phase=live_btc_phase,
            )
            matches.append(
                _build_scan_match(
                    signal=signal,
                    signal_source=response_signal_source,
                    last_price=float(last_price),
                    ticker=ticker if isinstance(ticker, dict) else {},
                    compare_field="baseline_ml",
                    compare_payload=baseline_ml_payload,
                    gate_entry_type=gate_entry_type,
                    force_entry_type_scope=True,
                    candle_pattern_sample=candle_pattern_sample,
                )
            )

    matches.sort(key=lambda item: item["win_probability"], reverse=True)
    return {
        "min_win": min_win,
        "scanned": len(scan_symbols),
        "count": len(matches),
        "signals": matches,
        "source": "live",
        "timestamp": now_iso,
    }


def _scan_test_signals_impl(min_win: float, max_symbols: int, symbols: list[str] | None = None) -> dict:
    if symbols:
        scan_symbols = symbols[:max_symbols]
    else:
        try:
            scan_symbols = _get_usdt_swap_symbols(max_symbols=max_symbols)
        except Exception:
            scan_symbols = _SYMBOLS_CACHE["symbols"][:max_symbols] if _SYMBOLS_CACHE["symbols"] else []

    matches: list[dict] = []
    now_iso = datetime.now(timezone.utc).isoformat()
    live_btc_phase = _resolve_live_btc_phase()
    try:
        tickers_map = market_client.fetch_tickers(scan_symbols) if scan_symbols else {}
    except Exception:
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

            signal = ml_test_predictor.predict(symbol=symbol, mark_price=last_price)
        except Exception:
            continue

        if signal.win_probability >= min_win:
            candle_pattern_sample = signal_candle_pattern_service.match_signal(
                signal_source="ML",
                side=signal.side,
                feature_snapshot=getattr(signal, "feature_snapshot", None),
                live_btc_phase=live_btc_phase,
            )
            matches.append(
                _build_scan_match(
                    signal=signal,
                    signal_source="ML_TEST",
                    last_price=float(last_price),
                    ticker=ticker if isinstance(ticker, dict) else {},
                    gate_entry_type="ML_TEST",
                    candle_pattern_sample=candle_pattern_sample,
                )
            )

    matches.sort(key=lambda item: item["win_probability"], reverse=True)
    return {
        "min_win": min_win,
        "scanned": len(scan_symbols),
        "count": len(matches),
        "signals": matches,
        "source": "live",
        "timestamp": now_iso,
    }


def get_scan_snapshot(
    min_win: float = 0.7,
    max_symbols: int = settings.signals_scan_default_max_symbols,
    symbols: str | None = None,
) -> dict:
    parsed_symbols = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else None
    return _scan_signals_impl(min_win=min_win, max_symbols=max_symbols, symbols=parsed_symbols)


def get_candles_scan_snapshot(
    min_win: float = 0.7,
    max_symbols: int = settings.signals_candles_scan_default_max_symbols,
    symbols: str | None = None,
) -> dict:
    parsed_symbols = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else None
    return _scan_candles_signals_impl(min_win=min_win, max_symbols=max_symbols, symbols=parsed_symbols)


def get_candles_bg_scan_snapshot(
    min_win: float = settings.paper_trade_candles_bg_min_win,
    max_symbols: int = settings.paper_trade_candles_bg_max_symbols,
    symbols: str | None = None,
) -> dict:
    parsed_symbols = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else None
    return _scan_candles_signals_impl(
        min_win=min_win,
        max_symbols=max_symbols,
        symbols=parsed_symbols,
        response_signal_source="ML_CANDLES_BG",
        gate_entry_type="ML_CANDLES_BG",
    )


def get_test_scan_snapshot(
    min_win: float = settings.paper_trade_test_ml_min_win,
    max_symbols: int = settings.paper_trade_test_ml_max_symbols,
    symbols: str | None = None,
) -> dict:
    parsed_symbols = [s.strip() for s in symbols.split(",") if s.strip()] if symbols else None
    return _scan_test_signals_impl(min_win=min_win, max_symbols=max_symbols, symbols=parsed_symbols)


@router.get("/latest")
def get_latest_signal(
    symbol: str = Query(default="BTC/USDT"),
    mark_price: float = Query(default=100.0, gt=0),
) -> dict:
    result = ml_predictor.predict(symbol=symbol, mark_price=mark_price)
    try:
        candle_result = ml_candles_predictor.predict(symbol=symbol, mark_price=mark_price)
        ml_candles_payload: dict | None = {
            "side": candle_result.side,
            "win_probability": candle_result.win_probability,
            "predicted_entry_price": candle_result.predicted_entry_price,
            "take_profit": candle_result.take_profit,
            "reference_win_symbol": getattr(candle_result, "reference_win_symbol", None),
            "reference_win_at": getattr(candle_result, "reference_win_at", None),
            "aligned": candle_result.side == result.side,
        }
    except Exception:
        ml_candles_payload = None
    return {
        "symbol": result.symbol,
        "side": result.side,
        "signal_source": "ML",
        "win_probability": result.win_probability,
        "predicted_entry_price": result.predicted_entry_price,
        "stop_loss": result.stop_loss,
        "take_profit": result.take_profit,
        "ml_candles": ml_candles_payload,
        "candle_pattern_sample": signal_candle_pattern_service.match_signal(
            signal_source="ML",
            side=result.side,
            feature_snapshot=getattr(result, "feature_snapshot", None),
            live_btc_phase=_resolve_live_btc_phase(),
        ),
    }


@router.get("/candles/latest")
def get_latest_candles_signal(
    symbol: str = Query(default="BTC/USDT"),
    mark_price: float = Query(default=100.0, gt=0),
) -> dict:
    result = ml_candles_predictor.predict(symbol=symbol, mark_price=mark_price)
    baseline_ml_payload = _build_compare_signal_payload(
        symbol=symbol,
        mark_price=mark_price,
        predictor=ml_predictor,
        target_side=result.side,
    )
    return {
        "symbol": result.symbol,
        "side": result.side,
        "signal_source": "ML_CANDLES",
        "win_probability": result.win_probability,
        "predicted_entry_price": result.predicted_entry_price,
        "stop_loss": result.stop_loss,
        "take_profit": result.take_profit,
        "reference_win_symbol": getattr(result, "reference_win_symbol", None),
        "reference_win_at": getattr(result, "reference_win_at", None),
        "baseline_ml": baseline_ml_payload,
        "candle_pattern_sample": signal_candle_pattern_service.match_signal(
            signal_source="ML_CANDLES",
            side=result.side,
            feature_snapshot=getattr(result, "feature_snapshot", None),
            live_btc_phase=_resolve_live_btc_phase(),
        ),
    }


@router.get("/scan")
def scan_signals(
    min_win: float = Query(default=0.7, ge=0.0, le=1.0),
    max_symbols: int = Query(default=settings.signals_scan_default_max_symbols, ge=1, le=600),
    symbols: str | None = Query(default=None),
) -> dict:
    return get_scan_snapshot(min_win=min_win, max_symbols=max_symbols, symbols=symbols)


@router.get("/candles/scan")
def scan_candles_signals(
    min_win: float = Query(default=0.7, ge=0.0, le=1.0),
    max_symbols: int = Query(default=settings.signals_candles_scan_default_max_symbols, ge=1, le=600),
    symbols: str | None = Query(default=None),
) -> dict:
    return get_candles_scan_snapshot(min_win=min_win, max_symbols=max_symbols, symbols=symbols)


@router.get("/candles/bg/scan")
def scan_candles_bg_signals(
    min_win: float = Query(default=settings.paper_trade_candles_bg_min_win, ge=0.0, le=1.0),
    max_symbols: int = Query(default=settings.paper_trade_candles_bg_max_symbols, ge=1, le=600),
    symbols: str | None = Query(default=None),
) -> dict:
    return get_candles_bg_scan_snapshot(min_win=min_win, max_symbols=max_symbols, symbols=symbols)


@router.get("/test/scan")
def scan_test_signals(
    min_win: float = Query(default=settings.paper_trade_test_ml_min_win, ge=0.0, le=1.0),
    max_symbols: int = Query(default=settings.paper_trade_test_ml_max_symbols, ge=1, le=600),
    symbols: str | None = Query(default=None),
) -> dict:
    return get_test_scan_snapshot(min_win=min_win, max_symbols=max_symbols, symbols=symbols)


@router.get("/candle-pattern-samples")
def list_candle_pattern_samples(
    signal_source: str | None = Query(default=None),
    only_good: bool = Query(default=False),
    refresh: bool = Query(default=False),
    limit: int = Query(default=120, ge=1, le=500),
) -> dict:
    items = signal_candle_pattern_service.get_catalog(signal_source=signal_source, force_refresh=refresh)
    if only_good:
        items = [item for item in items if bool(item.get("good_pattern"))]
    return {
        "count": min(len(items), limit),
        "items": items[:limit],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


