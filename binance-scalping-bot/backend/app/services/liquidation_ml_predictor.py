from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from random import random
from threading import Lock

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import train_test_split

from app.core.config import BASE_DIR
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
    "long_short_ratio",
    "long_short_delta",
    "funding_rate",
    "funding_bias",
    "open_interest_log",
    "open_interest_change_pct",
    "sentiment_bias",
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
    liq_zone_price: float
    liq_zone_score: float
    near_liq_zone: bool
    long_short_ratio: float
    funding_rate: float
    open_interest_notional: float
    sentiment_bias: float


class LiquidationMLPredictor:
    def __init__(
        self,
        model_path: str,
        *,
        client: BinanceFuturesClient | None = None,
        touch_tolerance_pct: float = 0.004,
        short_zone_min_score: float = 0.012,
        short_zone_touch_multiplier: float = 2.0,
        rr_ratio: float = 1.5,
    ) -> None:
        self.client = client or BinanceFuturesClient()
        self.model_path = Path(model_path)
        self.feature_columns = list(LIQUID_FEATURE_COLUMNS)
        self.touch_tolerance_pct = max(0.0005, float(touch_tolerance_pct))
        self.short_zone_min_score = max(0.0001, float(short_zone_min_score))
        self.short_zone_touch_multiplier = max(1.0, float(short_zone_touch_multiplier))
        self.rr_ratio = max(1.0, float(rr_ratio))
        self.model: RandomForestClassifier | None = None
        self.trained_at: datetime | None = None
        self.accuracy: float | None = None
        self.roc_auc: float | None = None
        self.last_near_ema_samples: int = 0
        self.last_side_long_samples: int = 0
        self.last_side_short_samples: int = 0
        self.last_side_balanced: bool = False
        self._train_lock = Lock()
        self._training_in_progress = False
        self.last_train_trigger: str | None = None
        self.last_train_started_at: datetime | None = None
        self.last_train_finished_at: datetime | None = None
        self.last_train_duration_sec: float | None = None
        self.last_train_result: str | None = None
        self.last_train_error: str | None = None
        self.train_log_path = BASE_DIR / ".runtime" / "liquid_train.log"
        self.train_log_path.parent.mkdir(parents=True, exist_ok=True)
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
        self.short_zone_min_score = float(payload.get("short_zone_min_score", self.short_zone_min_score))
        self.short_zone_touch_multiplier = float(payload.get("short_zone_touch_multiplier", self.short_zone_touch_multiplier))
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

    @staticmethod
    def _clip(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @classmethod
    def _sentiment_bias_from_values(
        cls,
        *,
        long_short_ratio: float,
        funding_rate: float,
        open_interest_change_pct: float,
    ) -> float:
        ls_crowded_long = cls._clip((long_short_ratio - 1.0) / 0.35, -1.0, 1.0)
        funding_crowded_long = cls._clip(funding_rate / 0.0008, -1.0, 1.0)
        oi_heat = cls._clip(open_interest_change_pct / 0.03, -1.0, 1.0)
        # Contrarian crowding logic for liquidation hunting:
        # too many longs + high positive funding -> prefer SHORT, and vice-versa.
        bias = (-(ls_crowded_long * 0.45) - (funding_crowded_long * 0.35) + (oi_heat * 0.20))
        return float(cls._clip(bias, -1.0, 1.0))

    def _sentiment_frame(self, symbol: str, limit: int) -> pd.DataFrame:
        rows_ratio = self.client.fetch_global_long_short_ratio(symbol=symbol, period="5m", limit=max(120, min(500, limit)))
        rows_funding = self.client.fetch_funding_rate_series(symbol=symbol, limit=max(40, min(200, limit // 3)))
        rows_oi = self.client.fetch_open_interest_hist(symbol=symbol, period="5m", limit=max(120, min(500, limit)))

        ratio_df: pd.DataFrame | None = None
        if rows_ratio:
            ratio_df = pd.DataFrame(rows_ratio)
            if "timestamp" in ratio_df.columns:
                ratio_df["timestamp"] = pd.to_datetime(ratio_df["timestamp"], unit="ms", utc=True)
                ratio_df["long_short_ratio"] = pd.to_numeric(ratio_df.get("longShortRatio"), errors="coerce")
                ratio_df = ratio_df[["timestamp", "long_short_ratio"]].dropna(subset=["timestamp"]).sort_values("timestamp")
            else:
                ratio_df = None

        funding_df: pd.DataFrame | None = None
        if rows_funding:
            funding_df = pd.DataFrame(rows_funding)
            ts_col = "fundingTime" if "fundingTime" in funding_df.columns else "time"
            if ts_col in funding_df.columns:
                funding_df["timestamp"] = pd.to_datetime(funding_df[ts_col], unit="ms", utc=True)
                funding_df["funding_rate"] = pd.to_numeric(funding_df.get("fundingRate"), errors="coerce")
                funding_df = funding_df[["timestamp", "funding_rate"]].dropna(subset=["timestamp"]).sort_values("timestamp")
            else:
                funding_df = None

        oi_df: pd.DataFrame | None = None
        if rows_oi:
            oi_df = pd.DataFrame(rows_oi)
            if "timestamp" in oi_df.columns:
                oi_df["timestamp"] = pd.to_datetime(oi_df["timestamp"], unit="ms", utc=True)
                oi_df["open_interest_notional"] = pd.to_numeric(oi_df.get("sumOpenInterestValue"), errors="coerce")
                oi_df = oi_df[["timestamp", "open_interest_notional"]].dropna(subset=["timestamp"]).sort_values("timestamp")
            else:
                oi_df = None

        base: pd.DataFrame | None = None
        for frame in (ratio_df, funding_df, oi_df):
            if frame is None or frame.empty:
                continue
            if base is None:
                base = frame.copy()
                continue
            base = pd.merge_asof(
                base.sort_values("timestamp"),
                frame.sort_values("timestamp"),
                on="timestamp",
                direction="backward",
            )

        if base is None:
            return pd.DataFrame(columns=["timestamp", "long_short_ratio", "funding_rate", "open_interest_notional"])

        base["long_short_ratio"] = pd.to_numeric(base.get("long_short_ratio"), errors="coerce").ffill().fillna(1.0)
        base["funding_rate"] = pd.to_numeric(base.get("funding_rate"), errors="coerce").ffill().fillna(0.0)
        base["open_interest_notional"] = pd.to_numeric(base.get("open_interest_notional"), errors="coerce").ffill().fillna(0.0)
        base["open_interest_notional"] = base["open_interest_notional"].clip(lower=0.0)
        return base

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

        sentiment = self._sentiment_frame(symbol=symbol, limit=limit)
        if not sentiment.empty:
            merged = pd.merge_asof(
                merged.sort_values("timestamp"),
                sentiment.sort_values("timestamp"),
                on="timestamp",
                direction="backward",
            )

        if "long_short_ratio" not in merged.columns:
            merged["long_short_ratio"] = 1.0
        if "funding_rate" not in merged.columns:
            merged["funding_rate"] = 0.0
        if "open_interest_notional" not in merged.columns:
            merged["open_interest_notional"] = 0.0

        merged["long_short_ratio"] = pd.to_numeric(merged["long_short_ratio"], errors="coerce").ffill().fillna(1.0)
        merged["funding_rate"] = pd.to_numeric(merged["funding_rate"], errors="coerce").ffill().fillna(0.0)
        merged["open_interest_notional"] = pd.to_numeric(merged["open_interest_notional"], errors="coerce").ffill().fillna(0.0)
        merged["open_interest_notional"] = merged["open_interest_notional"].clip(lower=0.0)

        merged["long_short_delta"] = merged["long_short_ratio"].diff().replace([np.inf, -np.inf], np.nan).fillna(0.0)
        merged["open_interest_change_pct"] = merged["open_interest_notional"].pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
        merged["open_interest_change_pct"] = merged["open_interest_change_pct"].clip(lower=-0.4, upper=0.4)
        merged["open_interest_log"] = np.log1p(merged["open_interest_notional"].clip(lower=0.0)).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        funding_long_bias = (-(merged["funding_rate"] / 0.0008)).clip(lower=-1.0, upper=1.0)
        merged["funding_bias"] = funding_long_bias.fillna(0.0)
        merged["sentiment_bias"] = merged.apply(
            lambda row: self._sentiment_bias_from_values(
                long_short_ratio=float(row.get("long_short_ratio") or 1.0),
                funding_rate=float(row.get("funding_rate") or 0.0),
                open_interest_change_pct=float(row.get("open_interest_change_pct") or 0.0),
            ),
            axis=1,
        )

        merged["dist_ema99_15m"] = ((merged["close"] - merged["ema99_15m"]) / merged["close"]).replace(
            [np.inf, -np.inf], np.nan
        )
        merged["dist_ema99_1h"] = ((merged["close"] - merged["ema99_1h"]) / merged["close"]).replace(
            [np.inf, -np.inf], np.nan
        )

        ema_bias = ((merged["nearest_ema99"] - merged["close"]) / merged["close"]).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        setup_score = ema_bias + (merged["sentiment_bias"] * 0.18)
        merged["setup_side"] = np.where(setup_score >= 0.0, 1.0, 0.0)

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

    @staticmethod
    def _count_side_samples(features: pd.DataFrame) -> tuple[int, int]:
        if "setup_side" not in features.columns:
            return 0, 0
        side = pd.to_numeric(features["setup_side"], errors="coerce")
        long_count = int((side >= 0.5).sum())
        short_count = int((side < 0.5).sum())
        return long_count, short_count

    @staticmethod
    def _rebalance_side_samples(
        features: pd.DataFrame,
        labels: pd.Series,
    ) -> tuple[pd.DataFrame, pd.Series, bool]:
        if "setup_side" not in features.columns:
            return features, labels, False

        side = pd.to_numeric(features["setup_side"], errors="coerce")
        long_idx = features.index[side >= 0.5].to_numpy()
        short_idx = features.index[side < 0.5].to_numpy()
        if len(long_idx) == 0 or len(short_idx) == 0:
            return features, labels, False
        if len(long_idx) == len(short_idx):
            return features, labels, False

        rng = np.random.default_rng(42)
        if len(long_idx) > len(short_idx):
            extra = rng.choice(short_idx, size=len(long_idx) - len(short_idx), replace=True)
        else:
            extra = rng.choice(long_idx, size=len(short_idx) - len(long_idx), replace=True)

        final_idx = np.concatenate([features.index.to_numpy(), extra])
        rng.shuffle(final_idx)
        return (
            features.loc[final_idx].reset_index(drop=True),
            labels.loc[final_idx].reset_index(drop=True),
            True,
        )

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

        near_ema_samples = int((frame["near_ema"] == True).sum())
        setup_mask = (frame["near_ema"] == True) | (frame["vol_spike_z_15m"].abs() >= 0.8)
        setup_rows = frame[setup_mask].copy()
        clean = setup_rows.dropna(subset=self.feature_columns + ["target"]).copy()

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
        trigger: str = "manual",
    ) -> dict:
        started_at = datetime.now(timezone.utc)
        with self._train_lock:
            if self._training_in_progress:
                self._append_train_log(
                    {
                        "event": "SKIP_BUSY",
                        "trigger": trigger,
                        "timestamp": started_at.isoformat(),
                    }
                )
                return {
                    "trained": False,
                    "samples": 0,
                    "features": len(self.feature_columns),
                    "accuracy": self.accuracy,
                    "roc_auc": self.roc_auc,
                    "trained_at": self.trained_at,
                    "near_ema_samples": self.last_near_ema_samples,
                }
            self._training_in_progress = True
            self.last_train_trigger = trigger
            self.last_train_started_at = started_at
            self.last_train_finished_at = None
            self.last_train_duration_sec = None
            self.last_train_result = "RUNNING"
            self.last_train_error = None

        self._append_train_log(
            {
                "event": "START",
                "trigger": trigger,
                "started_at": started_at.isoformat(),
                "limit": limit,
                "horizon": horizon,
                "rr_ratio": rr_ratio,
                "symbols_count": len(symbols),
            }
        )

        try:
            prepared = self.build_training_dataset(symbols, limit=limit, horizon=horizon, rr_ratio=rr_ratio)
            if prepared.features.empty or prepared.labels.nunique() < 2 or len(prepared.labels) < 120:
                result = {
                    "trained": False,
                    "samples": int(len(prepared.labels)),
                    "features": len(self.feature_columns),
                    "accuracy": self.accuracy,
                    "roc_auc": self.roc_auc,
                    "trained_at": self.trained_at,
                    "near_ema_samples": prepared.near_ema_samples,
                }
                self._finish_train(result="SKIPPED", error=None, started_at=started_at, trigger=trigger, payload=result)
                return result

            X = prepared.features[self.feature_columns]
            y = prepared.labels
            side_long_raw, side_short_raw = self._count_side_samples(X)
            X, y, side_balanced = self._rebalance_side_samples(X, y)
            side_long_used, side_short_used = self._count_side_samples(X)

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
                n_estimators=360,
                random_state=42,
                max_depth=12,
                min_samples_leaf=4,
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
            self.last_side_long_samples = side_long_used
            self.last_side_short_samples = side_short_used
            self.last_side_balanced = side_balanced

            self.model_path.parent.mkdir(parents=True, exist_ok=True)
            joblib.dump(
                {
                    "model": model,
                    "feature_columns": self.feature_columns,
                    "trained_at": self.trained_at.isoformat(),
                    "accuracy": self.accuracy,
                    "roc_auc": self.roc_auc,
                    "touch_tolerance_pct": self.touch_tolerance_pct,
                    "short_zone_min_score": self.short_zone_min_score,
                    "short_zone_touch_multiplier": self.short_zone_touch_multiplier,
                    "rr_ratio": rr_ratio,
                },
                self.model_path,
            )

            result = {
                "trained": True,
                "samples": int(len(y)),
                "features": len(self.feature_columns),
                "accuracy": self.accuracy,
                "roc_auc": self.roc_auc,
                "trained_at": self.trained_at,
                "near_ema_samples": prepared.near_ema_samples,
                "side_long_samples_raw": int(side_long_raw),
                "side_short_samples_raw": int(side_short_raw),
                "side_long_samples_used": int(side_long_used),
                "side_short_samples_used": int(side_short_used),
                "side_balanced": side_balanced,
            }
            self._finish_train(result="SUCCESS", error=None, started_at=started_at, trigger=trigger, payload=result)
            return result
        except Exception as exc:
            self._finish_train(result="FAILED", error=str(exc), started_at=started_at, trigger=trigger, payload=None)
            raise
        finally:
            with self._train_lock:
                self._training_in_progress = False

    def _finish_train(
        self,
        *,
        result: str,
        error: str | None,
        started_at: datetime,
        trigger: str,
        payload: dict | None,
    ) -> None:
        finished_at = datetime.now(timezone.utc)
        duration_sec = max(0.0, (finished_at - started_at).total_seconds())

        with self._train_lock:
            self.last_train_trigger = trigger
            self.last_train_started_at = started_at
            self.last_train_finished_at = finished_at
            self.last_train_duration_sec = duration_sec
            self.last_train_result = result
            self.last_train_error = error

        self._append_train_log(
            {
                "event": "FINISH",
                "trigger": trigger,
                "result": result,
                "error": error,
                "started_at": started_at.isoformat(),
                "finished_at": finished_at.isoformat(),
                "duration_sec": round(duration_sec, 3),
                "payload": payload,
            }
        )

    def _append_train_log(self, item: dict) -> None:
        try:
            line = json.dumps(item, ensure_ascii=True)
            with self.train_log_path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    def status(self) -> dict:
        with self._train_lock:
            training_in_progress = self._training_in_progress
        return {
            "is_loaded": self.model is not None,
            "model_path": str(self.model_path),
            "trained_at": self.trained_at,
            "feature_count": len(self.feature_columns),
            "accuracy": self.accuracy,
            "roc_auc": self.roc_auc,
            "touch_tolerance_pct": self.touch_tolerance_pct,
            "short_zone_min_score": self.short_zone_min_score,
            "short_zone_touch_multiplier": self.short_zone_touch_multiplier,
            "rr_ratio": self.rr_ratio,
            "last_near_ema_samples": self.last_near_ema_samples,
            "training_in_progress": training_in_progress,
            "last_train_trigger": self.last_train_trigger,
            "last_train_started_at": self.last_train_started_at,
            "last_train_finished_at": self.last_train_finished_at,
            "last_train_duration_sec": self.last_train_duration_sec,
            "last_train_result": self.last_train_result,
            "last_train_error": self.last_train_error,
            "last_side_long_samples": self.last_side_long_samples,
            "last_side_short_samples": self.last_side_short_samples,
            "last_side_balanced": self.last_side_balanced,
            "liquidation_features_enabled": True,
            "preferred_feature_count": len(self.feature_columns),
            "train_log_path": str(self.train_log_path),
        }

    @staticmethod
    def _extract_liq_zone(
        frame: pd.DataFrame,
        *,
        side: str,
        window: int = 96,
    ) -> tuple[float, float]:
        if frame.empty:
            return 0.0, 0.0
        view = frame.tail(max(30, window)).copy()
        vol_spike = view["vol_spike_z_15m"].clip(lower=0).fillna(0.0)
        if side == "SHORT":
            score = (view["wick_up_15m"].fillna(0.0) * (1.0 + vol_spike)).astype(float)
            zone_price_series = view["high"].astype(float)
        else:
            score = (view["wick_down_15m"].fillna(0.0) * (1.0 + vol_spike)).astype(float)
            zone_price_series = view["low"].astype(float)
        if score.empty:
            return 0.0, 0.0
        idx = score.idxmax()
        zone_score = float(score.loc[idx])
        zone_price = float(zone_price_series.loc[idx])
        return zone_price, zone_score

    def build_latest_feature_row(
        self,
        symbol: str,
        *,
        side: str | None = None,
        limit: int = 520,
    ) -> pd.Series | None:
        frame = self._prepare_frame(symbol=symbol, limit=limit)
        if frame.empty:
            return None
        clean = frame.dropna(subset=self.feature_columns).copy()
        if clean.empty:
            return None
        row = clean.iloc[-1].copy()
        if side == "LONG":
            row["setup_side"] = 1.0
        elif side == "SHORT":
            row["setup_side"] = 0.0
        return row[self.feature_columns].astype(float)

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
                liq_zone_price=round(entry, 6),
                liq_zone_score=0.0,
                near_liq_zone=False,
                long_short_ratio=1.0,
                funding_rate=0.0,
                open_interest_notional=0.0,
                sentiment_bias=0.0,
            )

        row = frame.iloc[-1].copy()
        ema15 = float(row.get("ema99_15m") or mark_price)
        ema1h = float(row.get("ema99_1h") or mark_price)
        near_ema = bool(row.get("near_ema"))
        short_zone_price, short_zone_score = self._extract_liq_zone(frame, side="SHORT")
        long_zone_price, long_zone_score = self._extract_liq_zone(frame, side="LONG")
        long_short_ratio = float(row.get("long_short_ratio") or 1.0)
        funding_rate = float(row.get("funding_rate") or 0.0)
        open_interest_notional = float(row.get("open_interest_notional") or 0.0)
        sentiment_bias = float(row.get("sentiment_bias") or 0.0)

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
            sentiment_adjust = float(np.clip(sentiment_bias, -1.0, 1.0)) * 0.08
            long_score = float(np.clip(long_prob + sentiment_adjust, 0.0, 1.0))
            short_score = float(np.clip(short_prob - sentiment_adjust, 0.0, 1.0))
            if long_score >= short_score:
                side = "LONG"
                win_prob = long_score
            else:
                side = "SHORT"
                win_prob = short_score
        else:
            side = "LONG" if sentiment_bias >= 0 else "SHORT"
            win_prob = float(np.clip(0.52 + abs(sentiment_bias) * 0.12 + (0.03 if near_ema else 0.0), 0.45, 0.72))

        if side == "SHORT":
            liq_zone_price = short_zone_price if short_zone_price > 0 else entry
            liq_zone_score = short_zone_score
            touch_dist = self.touch_tolerance_pct * self.short_zone_touch_multiplier
        else:
            liq_zone_price = long_zone_price if long_zone_price > 0 else entry
            liq_zone_score = long_zone_score
            touch_dist = self.touch_tolerance_pct

        near_liq_zone = False
        if mark_price > 0 and liq_zone_price > 0:
            near_liq_zone = abs(float(mark_price) - liq_zone_price) / float(mark_price) <= touch_dist

        # Prefer liquidation zone as confirmation anchor when score is meaningful.
        if liq_zone_score >= self.short_zone_min_score and liq_zone_price > 0:
            entry = liq_zone_price

        # Clamp entry to mark_price if mark_price is better, avoiding bad limit fill calculations
        if entry > 0 and mark_price > 0:
            if side == "LONG" and mark_price < entry:
                entry = float(mark_price)
            elif side == "SHORT" and mark_price > entry:
                entry = float(mark_price)

        atr = max(float(row.get("atr14_15m") or 0.0), entry * 0.002)

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
            liq_zone_price=round(float(liq_zone_price), 6),
            liq_zone_score=float(np.clip(liq_zone_score, 0.0, 10.0)),
            near_liq_zone=near_liq_zone,
            long_short_ratio=long_short_ratio,
            funding_rate=funding_rate,
            open_interest_notional=open_interest_notional,
            sentiment_bias=sentiment_bias,
        )
