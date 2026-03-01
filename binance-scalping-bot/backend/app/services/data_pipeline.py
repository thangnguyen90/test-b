from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from app.services.binance_client import BinanceFuturesClient


DEFAULT_SYMBOLS = [
    "PNUT/USDT",
    "MANA/USDT",
    "KAITO/USDT",
    "NEAR/USDT",
    "GMT/USDT",
    "TON/USDT",
    "GOAT/USDT",
    "PONKE/USDT",
    "SAFE/USDT",
    "AVA/USDT",
    "TOKEN/USDT",
    "MOCA/USDT",
    "PEOPLE/USDT",
    "SOL/USDT",
    "SUI/USDT",
    "DOGE/USDT",
    "ENA/USDT",
    "MOVE/USDT",
    "ADA/USDT",
    "TURBO/USDT",
    "NEIRO/USDT",
    "AVAX/USDT",
    "DOT/USDT",
    "XRP/USDT",
    "NEIROETH/USDT",
    "LINK/USDT",
    "XLM/USDT",
    "ZEC/USDT",
    "ATOM/USDT",
    "BAT/USDT",
    "NEO/USDT",
    "QTUM/USDT",
]

BASE_FEATURE_COLUMNS = [
    "close_m5",
    "volume_m5",
    "ema8_m5",
    "ema13_m5",
    "ema21_m5",
    "rsi14_m5",
    "macd_m5",
    "macd_signal_m5",
    "bb_upper_m5",
    "bb_lower_m5",
    "atr14_m5",
    "close_h1",
    "ema8_h1",
    "ema13_h1",
    "ema21_h1",
    "rsi14_h1",
    "atr14_h1",
    "setup_side",
]

LIQUIDATION_FEATURE_COLUMNS = [
    "liq_long_proxy_m5",
    "liq_short_proxy_m5",
    "liq_imbalance_proxy_m5",
    "vol_spike_z_m5",
]

FEATURE_COLUMNS = BASE_FEATURE_COLUMNS + LIQUIDATION_FEATURE_COLUMNS


@dataclass
class PreparedData:
    features: pd.DataFrame
    labels: pd.Series


class DataPipeline:
    def __init__(self, client: BinanceFuturesClient | None = None) -> None:
        self.client = client or BinanceFuturesClient()

    @staticmethod
    def _to_df(ohlcv: list[list[float]]) -> pd.DataFrame:
        df = pd.DataFrame(ohlcv, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df

    @staticmethod
    def _ema(series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    @staticmethod
    def _rsi(series: pd.Series, period: int = 14) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        rsi = 100 - (100 / (1 + rs))
        return rsi.fillna(50)

    @staticmethod
    def _macd(series: pd.Series) -> tuple[pd.Series, pd.Series]:
        ema12 = series.ewm(span=12, adjust=False).mean()
        ema26 = series.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        signal = macd.ewm(span=9, adjust=False).mean()
        return macd, signal

    @staticmethod
    def _bollinger(series: pd.Series, period: int = 20, num_std: float = 2.0) -> tuple[pd.Series, pd.Series]:
        mid = series.rolling(period).mean()
        std = series.rolling(period).std(ddof=0)
        upper = mid + (std * num_std)
        lower = mid - (std * num_std)
        return upper, lower

    @staticmethod
    def _atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
        prev_close = df["close"].shift(1)
        tr = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - prev_close).abs(),
                (df["low"] - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        return tr.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()

    def _enrich(self, df: pd.DataFrame, suffix: str) -> pd.DataFrame:
        out = df.copy()
        out[f"ema8_{suffix}"] = self._ema(out["close"], 8)
        out[f"ema13_{suffix}"] = self._ema(out["close"], 13)
        out[f"ema21_{suffix}"] = self._ema(out["close"], 21)
        out[f"rsi14_{suffix}"] = self._rsi(out["close"], 14)
        macd, macd_signal = self._macd(out["close"])
        out[f"macd_{suffix}"] = macd
        out[f"macd_signal_{suffix}"] = macd_signal
        upper, lower = self._bollinger(out["close"])
        out[f"bb_upper_{suffix}"] = upper
        out[f"bb_lower_{suffix}"] = lower
        out[f"atr14_{suffix}"] = self._atr(out, 14)
        if suffix == "m5":
            # Estimated liquidation pressure proxies:
            # long-liquidation proxy -> downside wick + abnormal volume
            # short-liquidation proxy -> upside wick + abnormal volume
            close_safe = out["close"].replace(0, np.nan)
            wick_up = ((out["high"] - np.maximum(out["open"], out["close"])) / close_safe).clip(lower=0)
            wick_down = ((np.minimum(out["open"], out["close"]) - out["low"]) / close_safe).clip(lower=0)
            vol_ma = out["volume"].rolling(20, min_periods=5).mean()
            vol_std = out["volume"].rolling(20, min_periods=5).std(ddof=0).replace(0, np.nan)
            vol_spike_z = ((out["volume"] - vol_ma) / vol_std).replace([np.inf, -np.inf], np.nan).fillna(0.0)
            vol_spike = np.maximum(vol_spike_z, 0.0)

            long_proxy = (wick_down.fillna(0.0) * (1.0 + vol_spike)).astype(float)
            short_proxy = (wick_up.fillna(0.0) * (1.0 + vol_spike)).astype(float)
            out["liq_long_proxy_m5"] = long_proxy
            out["liq_short_proxy_m5"] = short_proxy
            out["liq_imbalance_proxy_m5"] = (short_proxy - long_proxy).astype(float)
            out["vol_spike_z_m5"] = vol_spike_z.astype(float)

        out = out.rename(
            columns={
                "close": f"close_{suffix}",
                "volume": f"volume_{suffix}",
                "high": f"high_{suffix}",
                "low": f"low_{suffix}",
            }
        )
        return out

    @staticmethod
    def _side_from_setup(df: pd.DataFrame) -> pd.Series:
        h1_long = (df["close_h1"] > df["ema8_h1"]) & (df["ema8_h1"] > df["ema13_h1"]) & (df["ema13_h1"] > df["ema21_h1"])
        h1_short = (df["close_h1"] < df["ema8_h1"]) & (df["ema8_h1"] < df["ema13_h1"]) & (df["ema13_h1"] < df["ema21_h1"])

        m5_long = (df["close_m5"] > df["ema8_m5"]) & (df["ema8_m5"] > df["ema13_m5"]) & (df["ema13_m5"] > df["ema21_m5"])
        m5_short = (df["close_m5"] < df["ema8_m5"]) & (df["ema8_m5"] < df["ema13_m5"]) & (df["ema13_m5"] < df["ema21_m5"])

        pullback_long = (df["low_m5"] <= df["ema8_m5"]) & (df["close_m5"] > df["ema8_m5"])
        pullback_short = (df["high_m5"] >= df["ema8_m5"]) & (df["close_m5"] < df["ema8_m5"])

        long_setup = h1_long & m5_long & pullback_long
        short_setup = h1_short & m5_short & pullback_short

        setup = pd.Series(np.nan, index=df.index)
        setup.loc[long_setup] = 1
        setup.loc[short_setup] = 0
        return setup

    @staticmethod
    def _label_row(df: pd.DataFrame, idx: int, horizon: int, rr_ratio: float) -> float:
        row = df.iloc[idx]
        side = row["setup_side"]
        atr = row["atr14_m5"]
        entry = row["close_m5"]
        if pd.isna(side) or pd.isna(atr) or atr <= 0:
            return np.nan

        sl_distance = atr
        tp_distance = atr * rr_ratio

        if side == 1:
            stop = entry - sl_distance
            target = entry + tp_distance
        else:
            stop = entry + sl_distance
            target = entry - tp_distance

        future = df.iloc[idx + 1 : idx + 1 + horizon]
        if future.empty:
            return np.nan

        for _, nxt in future.iterrows():
            if side == 1:
                if nxt["low_m5"] <= stop:
                    return 0.0
                if nxt["high_m5"] >= target:
                    return 1.0
            else:
                if nxt["high_m5"] >= stop:
                    return 0.0
                if nxt["low_m5"] <= target:
                    return 1.0

        return np.nan

    def build_symbol_dataset(
        self,
        symbol: str,
        limit: int = 1000,
        horizon: int = 4,
        rr_ratio: float = 1.5,
    ) -> PreparedData:
        raw_m5 = self.client.fetch_ohlcv(symbol=symbol, timeframe="5m", limit=limit)
        raw_h1 = self.client.fetch_ohlcv(symbol=symbol, timeframe="1h", limit=max(300, limit // 12))

        m5 = self._enrich(self._to_df(raw_m5), "m5")
        h1 = self._enrich(self._to_df(raw_h1), "h1")

        merged = pd.merge_asof(
            m5.sort_values("timestamp"),
            h1.sort_values("timestamp"),
            on="timestamp",
            direction="backward",
            suffixes=("", "_h1dup"),
        )

        merged["setup_side"] = self._side_from_setup(merged)
        labels = [self._label_row(merged, idx, horizon=horizon, rr_ratio=rr_ratio) for idx in range(len(merged))]
        merged["target"] = labels

        clean = merged.dropna(subset=FEATURE_COLUMNS + ["target"]).copy()
        if clean.empty:
            return PreparedData(features=pd.DataFrame(columns=FEATURE_COLUMNS), labels=pd.Series(dtype=float))

        features = clean[FEATURE_COLUMNS].astype(float)
        labels_series = clean["target"].astype(int)
        return PreparedData(features=features, labels=labels_series)

    def build_training_dataset(
        self,
        symbols: Iterable[str],
        limit: int = 1000,
        horizon: int = 4,
        rr_ratio: float = 1.5,
    ) -> PreparedData:
        feature_frames: list[pd.DataFrame] = []
        label_frames: list[pd.Series] = []

        for symbol in symbols:
            try:
                prepared = self.build_symbol_dataset(
                    symbol=symbol,
                    limit=limit,
                    horizon=horizon,
                    rr_ratio=rr_ratio,
                )
            except Exception:
                continue

            if prepared.features.empty:
                continue

            feature_frames.append(prepared.features)
            label_frames.append(prepared.labels)

        if not feature_frames:
            return PreparedData(features=pd.DataFrame(columns=FEATURE_COLUMNS), labels=pd.Series(dtype=int))

        features = pd.concat(feature_frames, ignore_index=True)
        labels = pd.concat(label_frames, ignore_index=True)
        return PreparedData(features=features, labels=labels)

    def build_latest_feature_row(self, symbol: str, limit: int = 300) -> pd.Series | None:
        raw_m5 = self.client.fetch_ohlcv(symbol=symbol, timeframe="5m", limit=limit)
        raw_h1 = self.client.fetch_ohlcv(symbol=symbol, timeframe="1h", limit=max(300, limit // 12))
        m5 = self._enrich(self._to_df(raw_m5), "m5")
        h1 = self._enrich(self._to_df(raw_h1), "h1")

        merged = pd.merge_asof(
            m5.sort_values("timestamp"),
            h1.sort_values("timestamp"),
            on="timestamp",
            direction="backward",
            suffixes=("", "_h1dup"),
        )
        merged["setup_side"] = self._side_from_setup(merged).fillna(1.0)

        clean = merged.dropna(subset=FEATURE_COLUMNS).copy()
        if clean.empty:
            return None
        return clean[FEATURE_COLUMNS].astype(float).iloc[-1]
