from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from random import random

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import train_test_split

from app.services.binance_client import BinanceFuturesClient


LIQUID_FEATURE_COLUMNS = [
    "dist_ema99_15m",
    "dist_ema99_1h",
    "ema99_slope_15m",
    "ema99_slope_1h",
    "wick_up_15m",
    "wick_down_15m",
    "vol_spike_z_15m",
    "atr14_pct_15m",
    "atr14_pct_1h",
    "ret_1_15m",
    "ret_3_15m",
    "ret_12_15m",
    "setup_side",
]


@dataclass
class LiquidPreparedData:
    features: pd.DataFrame
    labels: pd.Series
    near_ema_samples: int


@dataclass
class LiquidSignalResult:
    symbol: str
    side: str
    win_probability: float
    predicted_entry_price: float
    stop_loss: float
    take_profit: float
    ema99_15m: float
    ema99_1h: float
    near_ema: bool


class LiquidationMLPredictor:
    def __init__(
        self,
        model_path: str,
        *,
        client: BinanceFuturesClient | None = None,
        touch_tolerance_pct: float = 0.004,
        rr_ratio: float = 1.5,
    ) -> None:
        self.client = client or BinanceFuturesClient()
        self.model_path = Path(model_path)
        self.feature_columns = list(LIQUID_FEATURE_COLUMNS)
        self.touch_tolerance_pct = max(0.0005, float(touch_tolerance_pct))
        self.rr_ratio = max(1.0, float(rr_ratio))
        self.model: RandomForestClassifier | None = None
        self.trained_at: datetime | None = None
        self.accuracy: float | None = None
        self.roc_auc: float | None = None
        self.last_near_ema_samples: int = 0
        self._load_model_if_exists()

    def _load_model_if_exists(self) -> None:
        if not self.model_path.exists():
            return
        payload = joblib.load(self.model_path)
        self.model = payload.get("model")
        self.feature_columns = payload.get("feature_columns", LIQUID_FEATURE_COLUMNS)
        trained_at = payload.get("trained_at")
        self.trained_at = datetime.fromisoformat(trained_at) if trained_at else None
        self.accuracy = payload.get("accuracy")
        self.roc_auc = payload.get("roc_auc")
        self.touch_tolerance_pct = float(payload.get("touch_tolerance_pct", self.touch_tolerance_pct))
        self.rr_ratio = float(payload.get("rr_ratio", self.rr_ratio))

    @staticmethod
    def _to_df(rows: list[list[float]]) -> pd.DataFrame:
        df = pd.DataFrame(rows, columns=["timestamp", "open", "high", "low", "close", "volume"])
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        return df

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

    def _prepare_frame(self, symbol: str, limit: int = 900) -> pd.DataFrame:
        raw_15m = self.client.fetch_ohlcv(symbol=symbol, timeframe="15m", limit=max(320, limit))
        raw_1h = self.client.fetch_ohlcv(symbol=symbol, timeframe="1h", limit=max(280, limit // 4))
        m15 = self._to_df(raw_15m).sort_values("timestamp").copy()
        h1 = self._to_df(raw_1h).sort_values("timestamp").copy()

        m15["ema99_15m"] = m15["close"].ewm(span=99, adjust=False).mean()
        m15["atr14_15m"] = self._atr(m15, 14)
        close_safe = m15["close"].replace(0, np.nan)
        m15["wick_up_15m"] = ((m15["high"] - np.maximum(m15["open"], m15["close"])) / close_safe).clip(lower=0)
        m15["wick_down_15m"] = ((np.minimum(m15["open"], m15["close"]) - m15["low"]) / close_safe).clip(lower=0)
        m15["ret_1_15m"] = m15["close"].pct_change(1).fillna(0.0)
        m15["ret_3_15m"] = m15["close"].pct_change(3).fillna(0.0)
        m15["ret_12_15m"] = m15["close"].pct_change(12).fillna(0.0)
        vol_ma = m15["volume"].rolling(24, min_periods=6).mean()
        vol_std = m15["volume"].rolling(24, min_periods=6).std(ddof=0).replace(0, np.nan)
        m15["vol_spike_z_15m"] = ((m15["volume"] - vol_ma) / vol_std).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        m15["ema99_slope_15m"] = m15["ema99_15m"].pct_change(3).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        m15["atr14_pct_15m"] = (m15["atr14_15m"] / close_safe).replace([np.inf, -np.inf], np.nan).fillna(0.0)

        h1["ema99_1h"] = h1["close"].ewm(span=99, adjust=False).mean()
        h1["atr14_1h"] = self._atr(h1, 14)
        h1["ema99_slope_1h"] = h1["ema99_1h"].pct_change(2).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        h1["atr14_pct_1h"] = (h1["atr14_1h"] / h1["close"].replace(0, np.nan)).replace([np.inf, -np.inf], np.nan).fillna(0.0)

        merged = pd.merge_asof(
            m15,
            h1[["timestamp", "ema99_1h", "ema99_slope_1h", "atr14_pct_1h"]],
            on="timestamp",
            direction="backward",
        )
        merged["ema99_1h"] = merged["ema99_1h"].ffill()
        merged["ema99_slope_1h"] = merged["ema99_slope_1h"].ffill().fillna(0.0)
        merged["atr14_pct_1h"] = merged["atr14_pct_1h"].ffill().fillna(0.0)

        nearest_ema = np.where(
            (merged["close"] - merged["ema99_15m"]).abs() <= (merged["close"] - merged["ema99_1h"]).abs(),
            merged["ema99_15m"],
            merged["ema99_1h"],
        )
        merged["nearest_ema99"] = nearest_ema
        merged["near_ema"] = ((merged["close"] - merged["nearest_ema99"]).abs() / merged["close"]) <= self.touch_tolerance_pct
        merged["setup_side"] = np.where(merged["close"] <= merged["nearest_ema99"], 1.0, 0.0)

        merged["dist_ema99_15m"] = ((merged["close"] - merged["ema99_15m"]) / merged["close"]).replace(
            [np.inf, -np.inf], np.nan
        )
        merged["dist_ema99_1h"] = ((merged["close"] - merged["ema99_1h"]) / merged["close"]).replace(
            [np.inf, -np.inf], np.nan
        )

        return merged

    @staticmethod
    def _label_row(df: pd.DataFrame, idx: int, horizon: int, rr_ratio: float) -> float:
        row = df.iloc[idx]
        side = int(row["setup_side"])
        entry = float(row["close"])
        atr = max(float(row.get("atr14_15m") or 0.0), entry * 0.002)
        if entry <= 0 or atr <= 0:
            return np.nan

        sl_dist = atr
        tp_dist = atr * rr_ratio
        stop = entry - sl_dist if side == 1 else entry + sl_dist
        target = entry + tp_dist if side == 1 else entry - tp_dist
        future = df.iloc[idx + 1 : idx + 1 + horizon]
        if future.empty:
            return np.nan

        for _, nxt in future.iterrows():
            if side == 1:
                if float(nxt["low"]) <= stop:
                    return 0.0
                if float(nxt["high"]) >= target:
                    return 1.0
            else:
                if float(nxt["high"]) >= stop:
                    return 0.0
                if float(nxt["low"]) <= target:
                    return 1.0
        return np.nan

    def build_symbol_dataset(
        self,
        symbol: str,
        *,
        limit: int = 900,
        horizon: int = 16,
        rr_ratio: float = 1.5,
    ) -> LiquidPreparedData:
        frame = self._prepare_frame(symbol=symbol, limit=limit)
        labels = [self._label_row(frame, idx, horizon=horizon, rr_ratio=rr_ratio) for idx in range(len(frame))]
        frame["target"] = labels
        near = frame[frame["near_ema"] == True].copy()
        near_ema_samples = int(len(near))
        clean = near.dropna(subset=self.feature_columns + ["target"]).copy()
        if clean.empty:
            return LiquidPreparedData(
                features=pd.DataFrame(columns=self.feature_columns),
                labels=pd.Series(dtype=int),
                near_ema_samples=near_ema_samples,
            )
        return LiquidPreparedData(
            features=clean[self.feature_columns].astype(float),
            labels=clean["target"].astype(int),
            near_ema_samples=near_ema_samples,
        )

    def build_training_dataset(
        self,
        symbols: list[str],
        *,
        limit: int = 900,
        horizon: int = 16,
        rr_ratio: float = 1.5,
    ) -> LiquidPreparedData:
        feature_frames: list[pd.DataFrame] = []
        labels: list[pd.Series] = []
        near_ema_samples = 0
        for symbol in symbols:
            try:
                prepared = self.build_symbol_dataset(symbol=symbol, limit=limit, horizon=horizon, rr_ratio=rr_ratio)
            except Exception:
                continue
            near_ema_samples += prepared.near_ema_samples
            if prepared.features.empty:
                continue
            feature_frames.append(prepared.features)
            labels.append(prepared.labels)
        if not feature_frames:
            return LiquidPreparedData(
                features=pd.DataFrame(columns=self.feature_columns),
                labels=pd.Series(dtype=int),
                near_ema_samples=near_ema_samples,
            )
        return LiquidPreparedData(
            features=pd.concat(feature_frames, ignore_index=True),
            labels=pd.concat(labels, ignore_index=True),
            near_ema_samples=near_ema_samples,
        )

    def train(
        self,
        *,
        symbols: list[str],
        limit: int = 900,
        horizon: int = 16,
        rr_ratio: float = 1.5,
    ) -> dict:
        prepared = self.build_training_dataset(symbols, limit=limit, horizon=horizon, rr_ratio=rr_ratio)
        if prepared.features.empty or len(prepared.labels) < 120:
            return {
                "trained": False,
                "samples": int(len(prepared.labels)),
                "features": len(self.feature_columns),
                "accuracy": self.accuracy,
                "roc_auc": self.roc_auc,
                "trained_at": self.trained_at,
                "near_ema_samples": prepared.near_ema_samples,
            }

        X = prepared.features[self.feature_columns]
        y = prepared.labels
        try:
            X_train, X_test, y_train, y_test = train_test_split(
                X,
                y,
                test_size=0.2,
                random_state=42,
                stratify=y,
            )
        except ValueError:
            X_train, X_test, y_train, y_test = train_test_split(
                X,
                y,
                test_size=0.2,
                random_state=42,
                stratify=None,
            )

        model = RandomForestClassifier(
            n_estimators=320,
            random_state=42,
            max_depth=10,
            min_samples_leaf=5,
            n_jobs=-1,
            class_weight="balanced_subsample",
        )
        model.fit(X_train, y_train)
        pred = model.predict(X_test)
        prob = model.predict_proba(X_test)[:, 1]
        self.accuracy = float(accuracy_score(y_test, pred))
        self.roc_auc = float(roc_auc_score(y_test, prob))
        self.trained_at = datetime.now(timezone.utc)
        self.model = model
        self.last_near_ema_samples = prepared.near_ema_samples

        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "model": model,
                "feature_columns": self.feature_columns,
                "trained_at": self.trained_at.isoformat(),
                "accuracy": self.accuracy,
                "roc_auc": self.roc_auc,
                "touch_tolerance_pct": self.touch_tolerance_pct,
                "rr_ratio": rr_ratio,
            },
            self.model_path,
        )

        return {
            "trained": True,
            "samples": int(len(y)),
            "features": len(self.feature_columns),
            "accuracy": self.accuracy,
            "roc_auc": self.roc_auc,
            "trained_at": self.trained_at,
            "near_ema_samples": prepared.near_ema_samples,
        }

    def status(self) -> dict:
        return {
            "is_loaded": self.model is not None,
            "model_path": str(self.model_path),
            "trained_at": self.trained_at,
            "feature_count": len(self.feature_columns),
            "accuracy": self.accuracy,
            "roc_auc": self.roc_auc,
            "touch_tolerance_pct": self.touch_tolerance_pct,
            "rr_ratio": self.rr_ratio,
            "last_near_ema_samples": self.last_near_ema_samples,
        }

    def predict(self, symbol: str, mark_price: float) -> LiquidSignalResult:
        frame = self._prepare_frame(symbol=symbol, limit=520)
        if frame.empty:
            entry = float(mark_price)
            side = "LONG" if random() > 0.5 else "SHORT"
            atr = max(entry * 0.003, 0.001)
            tp = entry + atr * self.rr_ratio if side == "LONG" else entry - atr * self.rr_ratio
            sl = entry - atr if side == "LONG" else entry + atr
            return LiquidSignalResult(
                symbol=symbol,
                side=side,
                win_probability=0.5,
                predicted_entry_price=round(entry, 6),
                stop_loss=round(sl, 6),
                take_profit=round(tp, 6),
                ema99_15m=round(entry, 6),
                ema99_1h=round(entry, 6),
                near_ema=False,
            )

        row = frame.iloc[-1].copy()
        ema15 = float(row.get("ema99_15m") or mark_price)
        ema1h = float(row.get("ema99_1h") or mark_price)
        near_ema = bool(row.get("near_ema"))
        if abs(mark_price - ema15) <= abs(mark_price - ema1h):
            entry = ema15
        else:
            entry = ema1h
        entry = float(entry) if entry > 0 else float(mark_price)
        atr = max(float(row.get("atr14_15m") or 0.0), entry * 0.002)

        if self.model is not None:
            row_long = row.copy()
            row_short = row.copy()
            row_long["setup_side"] = 1.0
            row_short["setup_side"] = 0.0
            x = pd.DataFrame([row_long, row_short])[self.feature_columns].replace([np.inf, -np.inf], np.nan).fillna(0.0)
            probs = self.model.predict_proba(x)[:, 1]
            long_prob = float(probs[0])
            short_prob = float(probs[1])
            if long_prob >= short_prob:
                side = "LONG"
                win_prob = long_prob
            else:
                side = "SHORT"
                win_prob = short_prob
        else:
            side = "LONG" if mark_price <= entry else "SHORT"
            win_prob = 0.55 if near_ema else 0.5

        tp_dist = atr * self.rr_ratio
        if side == "LONG":
            sl = entry - atr
            tp = entry + tp_dist
        else:
            sl = entry + atr
            tp = entry - tp_dist
        return LiquidSignalResult(
            symbol=symbol,
            side=side,
            win_probability=float(np.clip(win_prob, 0.0, 1.0)),
            predicted_entry_price=round(float(entry), 6),
            stop_loss=round(float(sl), 6),
            take_profit=round(float(tp), 6),
            ema99_15m=round(ema15, 6),
            ema99_1h=round(ema1h, 6),
            near_ema=near_ema,
        )
