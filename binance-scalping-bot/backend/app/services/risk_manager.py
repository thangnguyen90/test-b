from __future__ import annotations


def normalize_tp_sl(
    side: str,
    entry_price: float,
    take_profit: float,
    stop_loss: float,
    min_sl_pct: float = 0.004,
    min_rr: float = 1.5,
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
    sl_distance = max(signal_sl_distance, floor_sl_distance)
    tp_distance = max(signal_tp_distance, sl_distance * float(min_rr))

    if side == "LONG":
        normalized_sl = entry - sl_distance
        normalized_tp = entry + tp_distance
    else:
        normalized_sl = entry + sl_distance
        normalized_tp = entry - tp_distance

    return normalized_tp, normalized_sl
