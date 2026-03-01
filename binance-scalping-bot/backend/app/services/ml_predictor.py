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
from app.services.data_pipeline import DEFAULT_SYMBOLS, FEATURE_COLUMNS, DataPipeline, PreparedData
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
    def __init__(self, model_path: str, pipeline: DataPipeline | None = None) -> None:
        self.model_path = Path(model_path)
        self.pipeline = pipeline or DataPipeline()
        self.model: RandomForestClassifier | None = None
        self.feature_columns = FEATURE_COLUMNS
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
            feedback_prepared = self._build_feedback_dataset(feedback_rows)
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
                }
                self._finish_train(
                    result="SKIPPED",
                    error=None,
                    started_at=started_at,
                    trigger=trigger,
                    payload=result,
                )
                return result

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
                    row_df = pd.DataFrame([row])[self.feature_columns]
                    win_prob = float(self.model.predict_proba(row_df)[0][1])
                    side = "LONG" if row.get("setup_side", 1.0) >= 0.5 else "SHORT"
                    entry_price = float(row.get("close_m5", mark_price))
                    atr = float(row.get("atr14_m5", atr))
                    return self._to_signal(symbol, side, win_prob, entry_price, atr)
            except Exception:
                pass

        # Fallback when no model or no data is available.
        win_prob = round(0.45 + random() * 0.25, 4)
        return self._to_signal(symbol, side, win_prob, entry_price, atr)

    @staticmethod
    def _to_signal(symbol: str, side: str, win_prob: float, entry: float, atr: float) -> SignalResult:
        rr = 1.5
        if side == "LONG":
            stop_loss = entry - atr
            take_profit = entry + (atr * rr)
        else:
            stop_loss = entry + atr
            take_profit = entry - (atr * rr)

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

    def _build_feedback_dataset(self, feedback_rows: list[dict]) -> PreparedData:
        if not feedback_rows:
            return PreparedData(features=pd.DataFrame(columns=self.feature_columns), labels=pd.Series(dtype=int))

        feature_rows: list[pd.Series] = []
        labels: list[int] = []
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

            if symbol not in symbol_cache:
                try:
                    symbol_cache[symbol] = self.pipeline.build_latest_feature_row(symbol=symbol, limit=400)
                except Exception:
                    symbol_cache[symbol] = None

            feature_row = symbol_cache[symbol]
            if feature_row is None:
                continue

            sample = feature_row.copy()
            if side == "LONG":
                sample["setup_side"] = 1.0
            elif side == "SHORT":
                sample["setup_side"] = 0.0
            feature_rows.append(sample[self.feature_columns].astype(float))
            labels.append(label)

        if not feature_rows:
            return PreparedData(features=pd.DataFrame(columns=self.feature_columns), labels=pd.Series(dtype=int))

        features = pd.DataFrame(feature_rows).reset_index(drop=True)
        labels_series = pd.Series(labels, dtype=int)
        return PreparedData(features=features, labels=labels_series)
