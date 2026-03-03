from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from random import random
from threading import Lock
import json

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.model_selection import train_test_split

from app.core.config import BASE_DIR, settings
from app.services.data_pipeline import (
    BASE_FEATURE_COLUMNS,
    DEFAULT_SYMBOLS,
    FEATURE_COLUMNS,
    DataPipeline,
    PreparedData,
)
from app.services.mysql_trade_repo import MySQLTradeRepository


@dataclass
class SignalResult:
    symbol: str
    side: str
    win_probability: float
    predicted_entry_price: float
    stop_loss: float
    take_profit: float


class MLPredictor:
    def __init__(
        self,
        model_path: str,
        pipeline: DataPipeline | None = None,
        use_liquidation_features: bool | None = None,
    ) -> None:
        self.model_path = Path(model_path)
        self.pipeline = pipeline or DataPipeline()
        self.model: RandomForestClassifier | None = None
        self.use_liquidation_features = (
            settings.ml_use_liquidation_features
            if use_liquidation_features is None
            else bool(use_liquidation_features)
        )
        self.preferred_feature_columns = FEATURE_COLUMNS if self.use_liquidation_features else BASE_FEATURE_COLUMNS
        self.feature_columns = list(self.preferred_feature_columns)
        self.trained_at: datetime | None = None
        self.accuracy: float | None = None
        self.roc_auc: float | None = None
        self._train_lock = Lock()
        self._training_in_progress = False
        self.last_train_trigger: str | None = None
        self.last_train_started_at: datetime | None = None
        self.last_train_finished_at: datetime | None = None
        self.last_train_duration_sec: float | None = None
        self.last_train_result: str | None = None
        self.last_train_error: str | None = None
        self.last_side_long_samples: int = 0
        self.last_side_short_samples: int = 0
        self.last_side_balanced: bool = False
        self.last_feedback_penalized_samples: int = 0
        self.train_log_path = BASE_DIR / ".runtime" / "ml_train.log"
        self.train_log_path.parent.mkdir(parents=True, exist_ok=True)
        self._load_model_if_exists()

    def _load_model_if_exists(self) -> None:
        if not self.model_path.exists():
            return

        payload = joblib.load(self.model_path)
        self.model = payload.get("model")
        self.feature_columns = payload.get("feature_columns", FEATURE_COLUMNS)
        trained_at = payload.get("trained_at")
        self.trained_at = datetime.fromisoformat(trained_at) if trained_at else None
        self.accuracy = payload.get("accuracy")
        self.roc_auc = payload.get("roc_auc")

    def train(
        self,
        limit: int = 800,
        horizon: int = 4,
        rr_ratio: float = 1.5,
        symbols: list[str] | None = None,
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
                    "feedback_samples": 0,
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
                "symbols_count": len(symbols or DEFAULT_SYMBOLS),
            }
        )

        try:
            train_symbols = symbols or DEFAULT_SYMBOLS
            prepared = self.pipeline.build_training_dataset(
                symbols=train_symbols,
                limit=limit,
                horizon=horizon,
                rr_ratio=rr_ratio,
            )

            feedback_rows = self._load_feedback_rows(limit=settings.ml_feedback_train_limit)
            feedback_prepared, feedback_penalized = self._build_feedback_dataset(feedback_rows)
            self.last_feedback_penalized_samples = int(feedback_penalized)
            if not feedback_prepared.features.empty:
                prepared = PreparedData(
                    features=pd.concat([prepared.features, feedback_prepared.features], ignore_index=True),
                    labels=pd.concat([prepared.labels, feedback_prepared.labels], ignore_index=True),
                )

            if prepared.features.empty or prepared.labels.nunique() < 2:
                result = {
                    "trained": False,
                    "samples": int(len(prepared.labels)),
                    "features": len(self.feature_columns),
                    "accuracy": None,
                    "roc_auc": None,
                    "trained_at": None,
                    "feedback_samples": int(len(feedback_prepared.labels)),
                    "feedback_penalized_samples": int(feedback_penalized),
                }
                self._finish_train(
                    result="SKIPPED",
                    error=None,
                    started_at=started_at,
                    trigger=trigger,
                    payload=result,
                )
                return result

            train_feature_columns = [c for c in self.preferred_feature_columns if c in prepared.features.columns]
            if not train_feature_columns:
                train_feature_columns = [c for c in self.feature_columns if c in prepared.features.columns]
            if not train_feature_columns:
                raise RuntimeError("No valid feature columns for training")
            self.feature_columns = list(train_feature_columns)
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
                n_estimators=350,
                random_state=42,
                max_depth=12,
                min_samples_leaf=4,
                n_jobs=-1,
            )
            model.fit(X_train, y_train)

            pred = model.predict(X_test)
            prob = model.predict_proba(X_test)[:, 1]

            self.accuracy = float(accuracy_score(y_test, pred))
            self.roc_auc = float(roc_auc_score(y_test, prob))
            self.model = model
            self.trained_at = datetime.now(timezone.utc)
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
                "feedback_samples": int(len(feedback_prepared.labels)),
                "feedback_penalized_samples": int(feedback_penalized),
                "side_long_samples_raw": int(side_long_raw),
                "side_short_samples_raw": int(side_short_raw),
                "side_long_samples_used": int(side_long_used),
                "side_short_samples_used": int(side_short_used),
                "side_balanced": side_balanced,
            }
            self._finish_train(
                result="SUCCESS",
                error=None,
                started_at=started_at,
                trigger=trigger,
                payload=result,
            )
            return result
        except Exception as exc:
            self._finish_train(
                result="FAILED",
                error=str(exc),
                started_at=started_at,
                trigger=trigger,
                payload=None,
            )
            raise
        finally:
            with self._train_lock:
                self._training_in_progress = False

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
            "last_feedback_penalized_samples": self.last_feedback_penalized_samples,
            "liquidation_features_enabled": self.use_liquidation_features,
            "preferred_feature_count": len(self.preferred_feature_columns),
            "train_log_path": str(self.train_log_path),
        }

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
            # Never fail business flow because of log write.
            pass

    def predict(self, symbol: str, mark_price: float) -> SignalResult:
        side = "LONG" if random() > 0.5 else "SHORT"
        entry_price = mark_price
        atr = max(mark_price * 0.002, 0.001)

        if self.model is not None:
            try:
                row = self.pipeline.build_latest_feature_row(symbol=symbol)
                if row is not None:
                    # Evaluate both LONG and SHORT setup_side so side selection is model-driven,
                    # instead of being locked by the current heuristic setup_side.
                    row_long = row.copy()
                    row_short = row.copy()
                    row_long["setup_side"] = 1.0
                    row_short["setup_side"] = 0.0
                    row_df = pd.DataFrame([row_long, row_short])[self.feature_columns]
                    probs = self.model.predict_proba(row_df)[:, 1]
                    long_prob = float(probs[0])
                    short_prob = float(probs[1])
                    if long_prob >= short_prob:
                        side = "LONG"
                        win_prob = long_prob
                    else:
                        side = "SHORT"
                        win_prob = short_prob
                    entry_price = float(row.get("close_m5", mark_price))
                    atr = float(row.get("atr14_m5", atr))
                    return self._to_signal(symbol, side, win_prob, entry_price, atr)
            except Exception:
                pass

        # Fallback when no model or no data is available.
        win_prob = round(0.45 + random() * 0.25, 4)
        return self._to_signal(symbol, side, win_prob, entry_price, atr)

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
        re_features = features.loc[final_idx].reset_index(drop=True)
        re_labels = labels.loc[final_idx].reset_index(drop=True)
        return re_features, re_labels, True

    def _to_signal(self, symbol: str, side: str, win_prob: float, entry: float, atr: float) -> SignalResult:
        rr = 1.5
        max_tp_distance = entry * (max(0.0, settings.paper_trade_max_tp_pct) / 100.0)
        base_tp_distance = atr * rr
        tp_distance = min(base_tp_distance, max_tp_distance) if max_tp_distance > 0 else base_tp_distance
        if side == "LONG":
            stop_loss = entry - atr
            take_profit = entry + tp_distance
        else:
            stop_loss = entry + atr
            take_profit = entry - tp_distance

        return SignalResult(
            symbol=symbol,
            side=side,
            win_probability=float(np.clip(win_prob, 0.0, 1.0)),
            predicted_entry_price=round(float(entry), 6),
            stop_loss=round(float(stop_loss), 6),
            take_profit=round(float(take_profit), 6),
        )

    def _load_feedback_rows(self, limit: int) -> list[dict]:
        if not settings.mysql_enabled:
            return []
        try:
            repo = MySQLTradeRepository(
                host=settings.mysql_host,
                port=settings.mysql_port,
                user=settings.mysql_user,
                password=settings.mysql_password,
                database=settings.mysql_database,
            )
            return repo.list_feedback(limit=limit)
        except Exception:
            return []

    def _build_feedback_dataset(self, feedback_rows: list[dict]) -> tuple[PreparedData, int]:
        if not feedback_rows:
            return PreparedData(features=pd.DataFrame(columns=self.feature_columns), labels=pd.Series(dtype=int)), 0

        feature_rows: list[pd.Series] = []
        labels: list[int] = []
        penalized_count = 0
        symbol_cache: dict[str, pd.Series | None] = {}

        for row in feedback_rows:
            symbol = str(row.get("symbol") or "")
            side = str(row.get("side") or "")
            result = row.get("result")
            if not symbol or result is None:
                continue

            label = int(result)
            if label not in (0, 1):
                continue

            if settings.ml_feedback_flip_win_on_deep_mae and label == 1:
                mae_pct = row.get("mae_pct")
                try:
                    mae_value = float(mae_pct) if mae_pct is not None else 0.0
                except Exception:
                    mae_value = 0.0
                if mae_value <= -abs(settings.ml_feedback_mae_penalty_pct):
                    label = 0
                    penalized_count += 1

            feature_snapshot = self._parse_feature_snapshot(row.get("feature_snapshot_json"))
            if feature_snapshot is None:
                if symbol not in symbol_cache:
                    try:
                        symbol_cache[symbol] = self.pipeline.build_latest_feature_row(symbol=symbol, limit=400)
                    except Exception:
                        symbol_cache[symbol] = None

                feature_row = symbol_cache[symbol]
                if feature_row is None:
                    continue
                sample_dict = feature_row.to_dict()
            else:
                sample_dict = dict(feature_snapshot)

            if side == "LONG":
                sample_dict["setup_side"] = 1.0
            elif side == "SHORT":
                sample_dict["setup_side"] = 0.0

            sample = self._coerce_feature_row(sample_dict)
            if sample is None:
                continue
            feature_rows.append(sample)
            labels.append(label)

        if not feature_rows:
            return PreparedData(features=pd.DataFrame(columns=self.feature_columns), labels=pd.Series(dtype=int)), penalized_count

        features = pd.DataFrame(feature_rows).reset_index(drop=True)
        labels_series = pd.Series(labels, dtype=int)
        return PreparedData(features=features, labels=labels_series), penalized_count

    @staticmethod
    def _parse_feature_snapshot(raw: object) -> dict[str, float] | None:
        if raw is None:
            return None
        payload: object = raw
        if isinstance(raw, (bytes, bytearray)):
            payload = raw.decode("utf-8", errors="ignore")
        if isinstance(payload, str):
            text = payload.strip()
            if not text:
                return None
            try:
                payload = json.loads(text)
            except Exception:
                return None
        if not isinstance(payload, dict):
            return None

        out: dict[str, float] = {}
        for key, value in payload.items():
            if not isinstance(key, str):
                continue
            try:
                parsed = float(value)
            except Exception:
                continue
            if not np.isfinite(parsed):
                continue
            out[key] = parsed
        return out or None

    def _coerce_feature_row(self, sample: dict[str, object]) -> pd.Series | None:
        out: dict[str, float] = {}
        for col in self.feature_columns:
            raw = sample.get(col)
            try:
                value = float(raw) if raw is not None else 0.0
            except Exception:
                value = 0.0
            if not np.isfinite(value):
                value = 0.0
            out[col] = value
        if not out:
            return None
        return pd.Series(out, dtype=float)
