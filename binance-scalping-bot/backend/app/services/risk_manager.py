from __future__ import annotations


def normalize_tp_sl(
    side: str,
    entry_price: float,
    take_profit: float,
    stop_loss: float,
    min_sl_pct: float = 0.004,
    min_rr: float = 1.5,
    sl_extra_buffer_pct: float = 0.0,
    atr_value: float | None = None,
    sl_atr_multiplier: float = 0.0,
    max_tp_pct: float | None = None,
    leverage: int = 1,
    max_margin_loss_pct: float | None = 15.0,
) -> tuple[float, float]:
    """
    Normalize TP/SL to avoid unrealistically tight SL after market entry slippage.
    """
    entry = float(entry_price)
    tp = float(take_profit)
    sl = float(stop_loss)

    signal_sl_distance = abs(entry - sl)
    signal_tp_distance = abs(tp - entry)
    floor_sl_distance = max(entry * float(min_sl_pct), entry * 0.0005)
    if atr_value is not None and atr_value > 0 and sl_atr_multiplier > 0:
        floor_sl_distance = max(floor_sl_distance, float(atr_value) * float(sl_atr_multiplier))
    buffer_sl_distance = max(0.0, entry * float(sl_extra_buffer_pct))
    floor_sl_distance += buffer_sl_distance

    sl_distance = max(signal_sl_distance, floor_sl_distance)
    
    # Cap max SL distance to max margin loss %
    if max_margin_loss_pct is not None and max_margin_loss_pct > 0:
        max_sl_distance = entry * (float(max_margin_loss_pct) / 100.0) / float(max(1, leverage))
        sl_distance = min(sl_distance, max_sl_distance)

    tp_distance = max(signal_tp_distance, sl_distance * float(min_rr))
    if max_tp_pct is not None and max_tp_pct > 0:
        tp_distance = min(tp_distance, entry * float(max_tp_pct))

    if side == "LONG":
        normalized_sl = entry - sl_distance
        normalized_tp = entry + tp_distance
    else:
        normalized_sl = entry + sl_distance
        normalized_tp = entry - tp_distance

    return normalized_tp, normalized_sl


def calc_dynamic_take_profit(
    side: str,
    entry_price: float,
    atr_value: float,
    leverage: int,
    atr_multiplier: float = 2.0,
    min_margin_pct: float = 5.0,
    max_margin_pct: float = 10.0,
) -> float:
    """
    Tính Toán Take Profit Động Dựa Trên ATR Và Margin% Mong Muốn.
    atr_value: Biến động của nến.
    leverage: Đòn bẩy.
    min_margin_pct: Lợi nhuận kỳ vọng tối thiểu (VD: 5% theo margin).
    max_margin_pct: Lợi nhuận kỳ vọng tối đa (VD: 10% theo margin).
    """
    entry = float(entry_price)
    lev = max(1, int(leverage))

    # Margin% mong muốn quy ra khoảng cách giá (Price Distance)
    # Lãi margin 5%: PriceDistance = Entry * (5 / 100 / Leverage)
    min_distance = entry * (float(min_margin_pct) / 100.0 / lev)
    max_distance = entry * (float(max_margin_pct) / 100.0 / lev)

    # TP tự động sinh ra do biên độ nến kỳ vọng
    volatile_distance = float(atr_value) * float(atr_multiplier)

    # Khống chế (Clip) khoảng cách vào ngưỡng Min/Max Margin mong muốn
    tp_distance = max(min_distance, min(volatile_distance, max_distance))

    if side == "LONG":
        return entry + tp_distance
    else:
        return entry - tp_distance


def calc_margin_risk_pct(side: str, entry_price: float, stop_loss: float, leverage: int) -> float:
    entry = float(entry_price)
    sl = float(stop_loss)
    lev = max(1, int(leverage))
    if entry <= 0:
        return 0.0

    if side == "LONG":
        move = max(0.0, (entry - sl) / entry)
    else:
        move = max(0.0, (sl - entry) / entry)
    return move * lev * 100


def calc_estimated_margin_ratio_pct(leverage: int, maint_margin_rate: float) -> float:
    lev = max(1, int(leverage))
    mmr = max(0.0, float(maint_margin_rate))
    return mmr * lev * 100.0


def calc_min_sl_pct_from_loss(min_sl_loss_pct: float) -> float:
    loss_pct = max(0.0, float(min_sl_loss_pct))
    # loss% on order value (notional) ~= price_move_pct * 100
    return loss_pct / 100.0


def calc_quantity_from_margin_usdt(
    entry_price: float,
    leverage: int,
    margin_usdt: float,
    fallback_quantity: float = 0.01,
) -> float:
    entry = float(entry_price)
    lev = max(1, int(leverage))
    margin = float(margin_usdt)
    fallback = max(0.00000001, float(fallback_quantity))
    if entry <= 0 or margin <= 0:
        return fallback
    notional = margin * lev
    quantity = notional / entry
    return max(0.00000001, float(quantity))


def calc_quantity_from_order_usdt(
    entry_price: float,
    order_usdt: float,
    fallback_quantity: float = 0.01,
) -> float:
    entry = float(entry_price)
    order_value = float(order_usdt)
    fallback = max(0.00000001, float(fallback_quantity))
    if entry <= 0 or order_value <= 0:
        return fallback
    quantity = order_value / entry
    return max(0.00000001, float(quantity))


def calc_margin_usdt(entry_price: float, quantity: float, leverage: int) -> float:
    entry = float(entry_price)
    qty = float(quantity)
    lev = max(1, int(leverage))
    if entry <= 0 or qty <= 0:
        return 0.0
    notional = entry * qty
    return max(0.0, notional / lev)


def calc_atr_from_ohlcv(rows: list[list[float]], period: int = 14) -> float | None:
    if len(rows) < max(2, period + 1):
        return None

    true_ranges: list[float] = []
    prev_close = float(rows[0][4])
    for row in rows[1:]:
        if len(row) < 5:
            continue
        high = float(row[2])
        low = float(row[3])
        close = float(row[4])
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        true_ranges.append(tr)
        prev_close = close

    if len(true_ranges) < period:
        return None
    window = true_ranges[-period:]
    return sum(window) / period
