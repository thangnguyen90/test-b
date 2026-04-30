from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.services.binance_client import BinanceFuturesClient


DEFAULT_SYMBOLS = [
    "MOVR/USDT:USDT",
    "AAVE/USDT:USDT",
    "DYDX/USDT:USDT",
    "ENS/USDT:USDT",
    "OP/USDT:USDT",
]


@dataclass
class SignalStats:
    symbol: str
    timeframe: str
    bars: int
    signals: int
    avg_ret_1: float
    avg_ret_2: float
    avg_ret_4: float
    avg_ret_8: float
    median_mfe: float
    median_mae: float
    mfe_ge_3_rate: float
    mfe_ge_5_rate: float
    positive_close_rate: float
    last_signal_at: str | None
    last_entry_at: str | None
    last_entry_price: float | None
    last_signal_volume_ratio: float | None
    last_signal_gap_pct: float | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Backtest EMA99 bounce + volume expansion signals")
    parser.add_argument("--symbols", nargs="*", default=DEFAULT_SYMBOLS)
    parser.add_argument("--timeframes", nargs="*", default=["1h", "4h"])
    parser.add_argument("--limit", type=int, default=900)
    return parser.parse_args()


def to_frame(rows: list[list[Any]]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
    frame["timestamp"] = pd.to_datetime(frame["timestamp"], unit="ms", utc=True)
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = frame[column].astype(float)
    return frame.sort_values("timestamp").reset_index(drop=True)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    return out.fillna(50.0)


def macd_hist(close: pd.Series) -> pd.Series:
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    return macd - signal


def prepare_frame(rows: list[list[Any]]) -> pd.DataFrame:
    frame = to_frame(rows)
    frame["ema25"] = frame["close"].ewm(span=25, adjust=False).mean()
    frame["ema99"] = frame["close"].ewm(span=99, adjust=False).mean()
    frame["ema99_slope_3"] = frame["ema99"].pct_change(3).fillna(0.0)
    frame["rsi14"] = rsi(frame["close"], 14)
    frame["macd_hist"] = macd_hist(frame["close"]).fillna(0.0)
    frame["vol_ma20"] = frame["volume"].rolling(20, min_periods=5).mean()
    frame["volume_ratio"] = (
        frame["volume"] / frame["vol_ma20"].replace(0.0, np.nan)
    ).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    frame["candle_range"] = (frame["high"] - frame["low"]).replace(0.0, np.nan)
    frame["close_in_range"] = (
        (frame["close"] - frame["low"]) / frame["candle_range"]
    ).replace([np.inf, -np.inf], np.nan).fillna(0.5)
    frame["body_pct"] = (
        (frame["close"] - frame["open"]).abs() / frame["open"].replace(0.0, np.nan)
    ).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    frame["ema99_gap_pct"] = (
        (frame["close"] - frame["ema99"]) / frame["ema99"].replace(0.0, np.nan) * 100.0
    ).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    frame["touch_gap_pct"] = (
        (frame["low"] - frame["ema99"]).abs() / frame["ema99"].replace(0.0, np.nan) * 100.0
    ).replace([np.inf, -np.inf], np.nan).fillna(999.0)
    return frame


def signal_mask(frame: pd.DataFrame, timeframe: str) -> pd.Series:
    if timeframe == "4h":
        touch_tol_pct = 1.2
        max_gap_pct = 6.0
        body_min_pct = 0.35
        lookback_slope = 4
    else:
        touch_tol_pct = 0.8
        max_gap_pct = 4.0
        body_min_pct = 0.25
        lookback_slope = 3

    ema99_up = frame["ema99"] > frame["ema99"].shift(lookback_slope)
    trend_ok = (frame["ema25"] > frame["ema99"]) | ema99_up
    touch_ema99 = frame["touch_gap_pct"] <= touch_tol_pct
    reclaim_ema99 = frame["close"] > frame["ema99"]
    bullish_candle = frame["close"] > frame["open"]
    close_strong = frame["close_in_range"] >= 0.55
    volume_ok = (frame["volume"] > frame["volume"].shift(1)) & (frame["volume_ratio"] >= 1.15)
    momentum_ok = (frame["rsi14"] > frame["rsi14"].shift(1)) & (frame["macd_hist"] > frame["macd_hist"].shift(1))
    not_extended = frame["ema99_gap_pct"].between(0.0, max_gap_pct)
    body_ok = (frame["body_pct"] * 100.0) >= body_min_pct

    signal = (
        trend_ok
        & touch_ema99
        & reclaim_ema99
        & bullish_candle
        & close_strong
        & volume_ok
        & momentum_ok
        & not_extended
        & body_ok
    )
    return signal & (~signal.shift(1, fill_value=False))


def evaluate_symbol(symbol: str, timeframe: str, frame: pd.DataFrame) -> SignalStats:
    signal = signal_mask(frame, timeframe)
    lookahead = 6 if timeframe == "4h" else 8
    signal_indexes = [int(idx) for idx in np.flatnonzero(signal.to_numpy()) if int(idx) + 1 < len(frame)]

    ret_1: list[float] = []
    ret_2: list[float] = []
    ret_4: list[float] = []
    ret_8: list[float] = []
    mfe_list: list[float] = []
    mae_list: list[float] = []
    positive_close_count = 0

    last_signal_at: str | None = None
    last_entry_at: str | None = None
    last_entry_price: float | None = None
    last_signal_volume_ratio: float | None = None
    last_signal_gap_pct: float | None = None

    for idx in signal_indexes:
        entry_idx = idx + 1
        entry = float(frame.iloc[entry_idx]["open"])
        if not math.isfinite(entry) or entry <= 0:
            continue

        end_idx = min(len(frame) - 1, entry_idx + lookahead)
        future = frame.iloc[entry_idx : end_idx + 1]
        if future.empty:
            continue

        high_max = float(future["high"].max())
        low_min = float(future["low"].min())
        close_1 = float(frame.iloc[min(len(frame) - 1, entry_idx + 1)]["close"])
        close_2 = float(frame.iloc[min(len(frame) - 1, entry_idx + 2)]["close"])
        close_4 = float(frame.iloc[min(len(frame) - 1, entry_idx + 4)]["close"])
        close_8 = float(frame.iloc[min(len(frame) - 1, end_idx)]["close"])

        ret_1.append(((close_1 - entry) / entry) * 100.0)
        ret_2.append(((close_2 - entry) / entry) * 100.0)
        ret_4.append(((close_4 - entry) / entry) * 100.0)
        ret_8.append(((close_8 - entry) / entry) * 100.0)
        mfe_list.append(((high_max - entry) / entry) * 100.0)
        mae_list.append(((low_min - entry) / entry) * 100.0)
        if close_8 > entry:
            positive_close_count += 1

        last_signal_at = frame.iloc[idx]["timestamp"].isoformat()
        last_entry_at = frame.iloc[entry_idx]["timestamp"].isoformat()
        last_entry_price = entry
        last_signal_volume_ratio = float(frame.iloc[idx]["volume_ratio"])
        last_signal_gap_pct = float(frame.iloc[idx]["touch_gap_pct"])

    total = len(ret_8)
    if total == 0:
        return SignalStats(
            symbol=symbol,
            timeframe=timeframe,
            bars=len(frame),
            signals=0,
            avg_ret_1=0.0,
            avg_ret_2=0.0,
            avg_ret_4=0.0,
            avg_ret_8=0.0,
            median_mfe=0.0,
            median_mae=0.0,
            mfe_ge_3_rate=0.0,
            mfe_ge_5_rate=0.0,
            positive_close_rate=0.0,
            last_signal_at=None,
            last_entry_at=None,
            last_entry_price=None,
            last_signal_volume_ratio=None,
            last_signal_gap_pct=None,
        )

    return SignalStats(
        symbol=symbol,
        timeframe=timeframe,
        bars=len(frame),
        signals=total,
        avg_ret_1=float(np.mean(ret_1)),
        avg_ret_2=float(np.mean(ret_2)),
        avg_ret_4=float(np.mean(ret_4)),
        avg_ret_8=float(np.mean(ret_8)),
        median_mfe=float(np.median(mfe_list)),
        median_mae=float(np.median(mae_list)),
        mfe_ge_3_rate=float(np.mean([value >= 3.0 for value in mfe_list]) * 100.0),
        mfe_ge_5_rate=float(np.mean([value >= 5.0 for value in mfe_list]) * 100.0),
        positive_close_rate=float((positive_close_count / total) * 100.0),
        last_signal_at=last_signal_at,
        last_entry_at=last_entry_at,
        last_entry_price=last_entry_price,
        last_signal_volume_ratio=last_signal_volume_ratio,
        last_signal_gap_pct=last_signal_gap_pct,
    )


def format_value(value: float | None, digits: int = 2) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def main() -> int:
    args = parse_args()
    client = BinanceFuturesClient()

    stats_rows: list[SignalStats] = []
    for symbol in args.symbols:
        for timeframe in args.timeframes:
            try:
                rows = client.fetch_ohlcv(symbol=symbol, timeframe=timeframe, limit=args.limit)
                frame = prepare_frame(rows)
                stats_rows.append(evaluate_symbol(symbol, timeframe, frame))
            except Exception as exc:
                print(f"{symbol} {timeframe} error: {exc}")

    if not stats_rows:
        print("No results.")
        return 1

    print("symbol,timeframe,signals,avg_ret_1,avg_ret_2,avg_ret_4,avg_ret_8,median_mfe,median_mae,mfe_ge_3_rate,mfe_ge_5_rate,positive_close_rate,last_signal_at,last_entry_at,last_entry_price,last_signal_volume_ratio,last_signal_gap_pct")
    for row in stats_rows:
        print(
            ",".join(
                [
                    row.symbol,
                    row.timeframe,
                    str(row.signals),
                    format_value(row.avg_ret_1),
                    format_value(row.avg_ret_2),
                    format_value(row.avg_ret_4),
                    format_value(row.avg_ret_8),
                    format_value(row.median_mfe),
                    format_value(row.median_mae),
                    format_value(row.mfe_ge_3_rate),
                    format_value(row.mfe_ge_5_rate),
                    format_value(row.positive_close_rate),
                    row.last_signal_at or "-",
                    row.last_entry_at or "-",
                    format_value(row.last_entry_price, digits=6),
                    format_value(row.last_signal_volume_ratio),
                    format_value(row.last_signal_gap_pct),
                ]
            )
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
