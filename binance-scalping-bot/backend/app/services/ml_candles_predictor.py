from __future__ import annotations

from pathlib import Path
from random import random
import time
from typing import Any

import joblib
import numpy as np
import pandas as pd

from app.core.config import settings
from app.services.data_pipeline import DEFAULT_SYMBOLS, FEATURE_COLUMNS
from app.services.ml_predictor import MLPredictor, SignalResult
from app.services.mysql_trade_repo import MySQLTradeRepository
from app.services.signal_candle_pattern_service import signal_candle_pattern_service


class MLCandlesPredictor(MLPredictor):
    ENTRY_OFFSET_GRID = (0.0, 0.15, 0.3, 0.45)
    TP_MULTIPLIER_GRID = (1.0, 1.3, 1.6, 2.0, 2.4)
    ENTRY_MAX_DEVIATION_PCT = 0.05
    ATR_BASE_PCT = 0.002
    ATR_FLOOR_PCT = 0.00025
    ATR_CAP_PCT = 0.12
    MIN_DISTANCE_PCT = 0.001
    MIN_PRICE = 1e-10
    SIGNAL_PROB_FLOOR = 0.5
    SIGNAL_PROB_CEIL = 0.8
    PROFILE_SCORE_FLOOR = 0.15
    PROFILE_SCORE_CEIL = 0.85
    TP_STRENGTH_SCALE_MIN = 0.85
    TP_STRENGTH_SCALE_MAX = 1.45
    SL_STRENGTH_SCALE_MIN = 0.9
    SL_STRENGTH_SCALE_MAX = 1.35
    DEFAULT_TP_ATR_MULTIPLIER = 1.25
    BTC_REGIME_SIDE_BIAS = 0.045
    BTC_REGIME_STRONG_SIDE_BIAS = 0.07
    BTC_REGIME_COUNTERTREND_SHORT_PENALTY = 0.09
    BTC_REGIME_COUNTERTREND_LONG_PENALTY = 0.09

    def __init__(
        self,
        model_path: str,
        repo_database: str | None = None,
        profile_min_samples: int | None = None,
        **kwargs: Any,
    ) -> None:
        self.repo_database = repo_database or settings.mysql_candle_database
        self.profile_min_samples = max(
            8,
            int(profile_min_samples or settings.ml_candles_profile_min_samples),
        )
        self.entry_profiles: dict[str, dict[str, float | int | str]] = {}
        self.last_profile_count: int = 0
        self.last_profile_samples: int = 0
        self._recent_win_reference_cache: dict[str, tuple[float, dict[str, str | None] | None]] = {}
        super().__init__(model_path=model_path, **kwargs)

    def _load_model_if_exists(self) -> None:
        if not Path(self.model_path).exists():
            return

        payload = joblib.load(self.model_path)
        self.model = payload.get("model")
        self.feature_columns = payload.get("feature_columns", FEATURE_COLUMNS)
        trained_at = payload.get("trained_at")
        self.trained_at = pd.Timestamp(trained_at).to_pydatetime() if trained_at else None
        self.accuracy = payload.get("accuracy")
        self.roc_auc = payload.get("roc_auc")
        self.entry_profiles = payload.get("entry_profiles", {}) or {}
        self.last_profile_count = int(payload.get("profile_count") or len(self.entry_profiles))
        self.last_profile_samples = int(payload.get("profile_samples") or 0)
        self.profile_min_samples = int(payload.get("profile_min_samples") or self.profile_min_samples)

    def _persist_model(self) -> None:
        if self.model is None:
            return
        self.model_path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "model": self.model,
                "feature_columns": self.feature_columns,
                "trained_at": self.trained_at.isoformat() if self.trained_at else None,
                "accuracy": self.accuracy,
                "roc_auc": self.roc_auc,
                "entry_profiles": self.entry_profiles,
                "profile_count": len(self.entry_profiles),
                "profile_samples": self.last_profile_samples,
                "profile_min_samples": self.profile_min_samples,
            },
            self.model_path,
        )

    def train(
        self,
        limit: int = 800,
        horizon: int = 4,
        rr_ratio: float = 1.5,
        symbols: list[str] | None = None,
        trigger: str = "manual_ml_candles",
    ) -> dict:
        result = super().train(
            limit=limit,
            horizon=horizon,
            rr_ratio=rr_ratio,
            symbols=symbols,
            trigger=trigger,
        )

        train_symbols = symbols or DEFAULT_SYMBOLS
        try:
            profiles, profile_samples = self._build_optimization_profiles(
                symbols=train_symbols,
                limit=limit,
                horizon=horizon,
            )
            if profiles:
                self.entry_profiles = profiles
                self.last_profile_count = len(profiles)
                self.last_profile_samples = profile_samples
            result["profile_count"] = int(self.last_profile_count)
            result["profile_samples"] = int(self.last_profile_samples)
        except Exception as exc:
            result["profile_count"] = int(self.last_profile_count)
            result["profile_samples"] = int(self.last_profile_samples)
            result["profile_error"] = str(exc)
        if self.model is not None:
            self._persist_model()
        return result

    def status(self) -> dict:
        payload = super().status()
        payload.update(
            {
                "profile_count": int(self.last_profile_count),
                "profile_samples": int(self.last_profile_samples),
                "profile_min_samples": int(self.profile_min_samples),
                "repo_database": self.repo_database,
            }
        )
        return payload

    def predict(self, symbol: str, mark_price: float) -> SignalResult:
        side = "LONG" if random() > 0.5 else "SHORT"
        mark = self._safe_positive(mark_price, fallback=1.0)
        entry_price = mark
        atr = self._normalize_atr(raw_atr=(mark * self.ATR_BASE_PCT), mark_price=mark)
        win_prob = round(0.48 + random() * 0.18, 4)
        row: pd.Series | None = None

        try:
            row = self.pipeline.build_latest_feature_row(symbol=symbol)
        except Exception:
            row = None

        if row is not None:
            row_close = self._safe_positive(row.get("close_m5"), fallback=mark)
            entry_price = self._resolve_entry_anchor(mark_price=mark, row_close=row_close)
            atr = self._normalize_atr(raw_atr=row.get("atr14_m5"), mark_price=mark)

        if self.model is not None and row is not None:
            try:
                row_long = row.copy()
                row_short = row.copy()
                row_long["setup_side"] = 1.0
                row_short["setup_side"] = 0.0
                row_df = pd.DataFrame([row_long, row_short])[self.feature_columns]
                probs = self.model.predict_proba(row_df)[:, 1]
                long_prob = float(probs[0])
                short_prob = float(probs[1])
                long_prob, short_prob = self._apply_btc_regime_side_bias(
                    row=row,
                    long_prob=long_prob,
                    short_prob=short_prob,
                )
                long_prob, short_prob = self._apply_bullish_volume_short_penalty(
                    row=row,
                    long_prob=long_prob,
                    short_prob=short_prob,
                )
                if long_prob >= short_prob:
                    side = "LONG"
                    win_prob = long_prob
                else:
                    side = "SHORT"
                    win_prob = short_prob
            except Exception:
                pass

        feature_snapshot = self._build_feature_snapshot(row=row, side=side) if row is not None else None
        profile = self._resolve_profile(row=row, side=side) if row is not None else None
        if not profile:
            signal_strength = self._normalize_strength_component(
                win_prob,
                floor=self.SIGNAL_PROB_FLOOR,
                ceil=self.SIGNAL_PROB_CEIL,
            )
            take_profit, stop_loss = self._build_profile_exit_targets(
                side=side,
                entry=entry_price,
                atr=atr,
                win_prob=win_prob,
                profile_tp_multiplier=self.DEFAULT_TP_ATR_MULTIPLIER,
                signal_strength=signal_strength,
            )
            return self._attach_reference_win(
                self._sanitize_signal(
                    symbol=symbol,
                    side=side,
                    win_prob=win_prob,
                    entry=entry_price,
                    stop_loss=stop_loss,
                    take_profit=take_profit,
                    mark_price=mark,
                    atr=atr,
                    feature_snapshot=feature_snapshot,
                ),
                profile=None,
                side=side,
            )

        entry_offset_atr = float(profile.get("entry_offset_atr") or 0.0)
        tp_atr_multiplier = float(profile.get("tp_atr_multiplier") or 1.5)
        profile_win_rate = float(profile.get("win_rate") or win_prob)
        signal_strength = self._resolve_signal_strength(
            raw_win_prob=win_prob,
            profile=profile,
            profile_win_rate=profile_win_rate,
        )

        entry_offset = atr * max(0.0, entry_offset_atr)
        entry = entry_price - entry_offset if side == "LONG" else entry_price + entry_offset
        win_prob = float(np.clip((win_prob * 0.7) + (profile_win_rate * 0.3), 0.0, 0.99))
        take_profit, stop_loss = self._build_profile_exit_targets(
            side=side,
            entry=entry,
            atr=atr,
            win_prob=win_prob,
            profile_tp_multiplier=tp_atr_multiplier,
            signal_strength=signal_strength,
        )
        return self._attach_reference_win(
            self._sanitize_signal(
                symbol=symbol,
                side=side,
                win_prob=win_prob,
                entry=entry,
                stop_loss=stop_loss,
                take_profit=take_profit,
                mark_price=mark,
                atr=atr,
                feature_snapshot=feature_snapshot,
            ),
            profile=profile,
            side=side,
        )

    def _apply_btc_regime_side_bias(
        self,
        *,
        row: pd.Series,
        long_prob: float,
        short_prob: float,
    ) -> tuple[float, float]:
        long_prob = float(np.clip(long_prob, 0.0, 0.99))
        short_prob = float(np.clip(short_prob, 0.0, 0.99))
        long_snapshot = self._build_feature_snapshot(row=row, side="LONG")
        short_snapshot = self._build_feature_snapshot(row=row, side="SHORT")
        bullish_btc = self._is_btc_bullish_regime_row(row)
        bearish_btc = self._is_btc_bearish_regime_row(row)
        strong_bullish_btc = bullish_btc and self._is_btc_strong_regime_row(row, side="LONG")
        strong_bearish_btc = bearish_btc and self._is_btc_strong_regime_row(row, side="SHORT")

        long_sample = (
            signal_candle_pattern_service.match_signal(
                signal_source="ML_CANDLES",
                side="LONG",
                feature_snapshot=long_snapshot,
            )
            if long_snapshot is not None
            else None
        )
        short_sample = (
            signal_candle_pattern_service.match_signal(
                signal_source="ML_CANDLES",
                side="SHORT",
                feature_snapshot=short_snapshot,
            )
            if short_snapshot is not None
            else None
        )

        if bullish_btc:
            long_prob += self.BTC_REGIME_SIDE_BIAS
            if strong_bullish_btc:
                long_prob += self.BTC_REGIME_STRONG_SIDE_BIAS
            if self._is_countertrend_short_pattern(short_sample):
                short_prob -= self.BTC_REGIME_COUNTERTREND_SHORT_PENALTY
            elif self._is_supportive_long_pattern(long_sample):
                long_prob += 0.02

        if bearish_btc:
            short_prob += self.BTC_REGIME_SIDE_BIAS
            if strong_bearish_btc:
                short_prob += self.BTC_REGIME_STRONG_SIDE_BIAS
            if self._is_countertrend_long_pattern(long_sample):
                long_prob -= self.BTC_REGIME_COUNTERTREND_LONG_PENALTY
            elif self._is_supportive_short_pattern(short_sample):
                short_prob += 0.02

        return float(np.clip(long_prob, 0.0, 0.99)), float(np.clip(short_prob, 0.0, 0.99))

    def _apply_bullish_volume_short_penalty(
        self,
        *,
        row: pd.Series,
        long_prob: float,
        short_prob: float,
    ) -> tuple[float, float]:
        long_prob = float(np.clip(long_prob, 0.0, 0.99))
        short_prob = float(np.clip(short_prob, 0.0, 0.99))
        if not settings.paper_trade_candles_bg_bullish_volume_short_penalty_enabled:
            return long_prob, short_prob
        if not self._has_bullish_volume_short_penalty_context(row):
            return long_prob, short_prob

        penalty = max(0.0, float(settings.paper_trade_candles_bg_bullish_volume_short_penalty))
        if penalty <= 0.0:
            return long_prob, short_prob
        return long_prob, float(np.clip(short_prob - penalty, 0.0, 0.99))

    def _has_bullish_volume_short_penalty_context(self, row: pd.Series | None) -> bool:
        close_h1 = self._safe_row_float(row, "close_h1")
        ema8_h1 = self._safe_row_float(row, "ema8_h1")
        ema13_h1 = self._safe_row_float(row, "ema13_h1")
        ema21_h1 = self._safe_row_float(row, "ema21_h1")
        close_m5 = self._safe_row_float(row, "close_m5")
        ema8_m5 = self._safe_row_float(row, "ema8_m5")
        ema13_m5 = self._safe_row_float(row, "ema13_m5")
        ema21_m5 = self._safe_row_float(row, "ema21_m5")
        macd_m5 = self._safe_row_float(row, "macd_m5")
        macd_signal_m5 = self._safe_row_float(row, "macd_signal_m5")
        rsi_h1 = self._safe_row_float(row, "rsi14_h1")
        vol_spike = self._safe_row_float(row, "vol_spike_z_m5")
        liq_imbalance = self._safe_row_float(row, "liq_imbalance_proxy_m5")

        bullish_h1 = close_h1 >= ema8_h1 >= ema13_h1 >= ema21_h1
        bullish_m5 = close_m5 >= ema8_m5 >= ema13_m5 and close_m5 >= ema21_m5
        bullish_momentum = macd_m5 >= macd_signal_m5 and (
            rsi_h1 >= float(settings.paper_trade_candles_bg_bullish_volume_short_min_rsi_h1)
        )
        expanding_volume = (
            vol_spike >= float(settings.paper_trade_candles_bg_bullish_volume_short_min_vol_spike)
        )
        no_clear_short_imbalance = (
            liq_imbalance <= float(settings.paper_trade_candles_bg_bullish_volume_short_max_liq_imbalance)
        )

        return bool(
            bullish_h1
            and bullish_m5
            and bullish_momentum
            and expanding_volume
            and no_clear_short_imbalance
        )

    @staticmethod
    def _safe_row_float(row: pd.Series | None, key: str) -> float:
        if row is None:
            return 0.0
        try:
            value = float(row.get(key) or 0.0)
        except Exception:
            value = 0.0
        if not np.isfinite(value):
            return 0.0
        return float(value)

    def _is_btc_bullish_regime_row(self, row: pd.Series | None) -> bool:
        close_h1 = self._safe_row_float(row, "close_h1")
        ema8_h1 = self._safe_row_float(row, "ema8_h1")
        ema13_h1 = self._safe_row_float(row, "ema13_h1")
        ema21_h1 = self._safe_row_float(row, "ema21_h1")
        close_m5 = self._safe_row_float(row, "close_m5")
        ema8_m5 = self._safe_row_float(row, "ema8_m5")
        macd_m5 = self._safe_row_float(row, "macd_m5")
        macd_signal_m5 = self._safe_row_float(row, "macd_signal_m5")
        rsi_h1 = self._safe_row_float(row, "rsi14_h1")
        return bool(
            close_h1 > ema8_h1 > ema13_h1 > ema21_h1
            and close_m5 >= ema8_m5
            and macd_m5 >= macd_signal_m5
            and rsi_h1 >= 54.0
        )

    def _is_btc_bearish_regime_row(self, row: pd.Series | None) -> bool:
        close_h1 = self._safe_row_float(row, "close_h1")
        ema8_h1 = self._safe_row_float(row, "ema8_h1")
        ema13_h1 = self._safe_row_float(row, "ema13_h1")
        ema21_h1 = self._safe_row_float(row, "ema21_h1")
        close_m5 = self._safe_row_float(row, "close_m5")
        ema8_m5 = self._safe_row_float(row, "ema8_m5")
        macd_m5 = self._safe_row_float(row, "macd_m5")
        macd_signal_m5 = self._safe_row_float(row, "macd_signal_m5")
        rsi_h1 = self._safe_row_float(row, "rsi14_h1")
        return bool(
            close_h1 < ema8_h1 < ema13_h1 < ema21_h1
            and close_m5 <= ema8_m5
            and macd_m5 <= macd_signal_m5
            and rsi_h1 <= 46.0
        )

    def _is_btc_strong_regime_row(self, row: pd.Series | None, *, side: str) -> bool:
        side_key = str(side or "").upper()
        rsi_h1 = self._safe_row_float(row, "rsi14_h1")
        vol_spike = self._safe_row_float(row, "vol_spike_z_m5")
        close_m5 = self._safe_row_float(row, "close_m5")
        ema8_m5 = self._safe_row_float(row, "ema8_m5")
        if side_key == "LONG":
            return bool(rsi_h1 >= 58.0 and close_m5 >= ema8_m5 and vol_spike >= 0.2)
        if side_key == "SHORT":
            return bool(rsi_h1 <= 42.0 and close_m5 <= ema8_m5 and vol_spike >= 0.2)
        return False

    @staticmethod
    def _sample_value(sample: dict[str, Any] | None, key: str) -> str:
        if not isinstance(sample, dict):
            return ""
        return str(sample.get(key) or "").strip().upper()

    def _is_countertrend_short_pattern(self, sample: dict[str, Any] | None) -> bool:
        market_phase = self._sample_value(sample, "market_phase")
        setup_kind = self._sample_value(sample, "setup_kind")
        volatility_kind = self._sample_value(sample, "volatility_kind")
        return (
            market_phase in {"RANGE_TRANSITION", "BULL_RETRACE", "BULL_PULLBACK"}
            and setup_kind == "EMA_RECLAIM"
            and volatility_kind == "CALM"
        )

    def _is_countertrend_long_pattern(self, sample: dict[str, Any] | None) -> bool:
        market_phase = self._sample_value(sample, "market_phase")
        setup_kind = self._sample_value(sample, "setup_kind")
        volatility_kind = self._sample_value(sample, "volatility_kind")
        return (
            market_phase in {"RANGE_TRANSITION", "BEAR_RETRACE", "BEAR_PULLBACK"}
            and setup_kind == "EMA_RECLAIM"
            and volatility_kind == "CALM"
        )

    def _is_supportive_long_pattern(self, sample: dict[str, Any] | None) -> bool:
        market_phase = self._sample_value(sample, "market_phase")
        setup_kind = self._sample_value(sample, "setup_kind")
        quality_tier = self._sample_value(sample, "quality_tier")
        return (
            market_phase in {"BULL_RETRACE", "BULL_PULLBACK", "RANGE_TRANSITION"}
            and setup_kind in {"EMA_RECLAIM", "STRUCTURED_CONTINUATION"}
            and quality_tier in {"A", "B"}
        )

    def _is_supportive_short_pattern(self, sample: dict[str, Any] | None) -> bool:
        market_phase = self._sample_value(sample, "market_phase")
        setup_kind = self._sample_value(sample, "setup_kind")
        quality_tier = self._sample_value(sample, "quality_tier")
        return (
            market_phase in {"BEAR_RETRACE", "BEAR_PULLBACK", "RANGE_TRANSITION"}
            and setup_kind in {"EMA_RECLAIM", "STRUCTURED_CONTINUATION"}
            and quality_tier in {"A", "B"}
        )

    def _resolve_entry_anchor(self, *, mark_price: float, row_close: float) -> float:
        mark = self._safe_positive(mark_price, fallback=1.0)
        close = self._safe_positive(row_close, fallback=mark)
        deviation_pct = abs(close - mark) / mark if mark > 0 else 0.0
        if deviation_pct <= self.ENTRY_MAX_DEVIATION_PCT:
            return close
        return mark

    def _normalize_atr(self, *, raw_atr: object, mark_price: float) -> float:
        mark = self._safe_positive(mark_price, fallback=1.0)
        min_atr = max(mark * self.ATR_FLOOR_PCT, self.MIN_PRICE)
        max_atr = max(mark * self.ATR_CAP_PCT, min_atr)
        fallback = max(mark * self.ATR_BASE_PCT, min_atr)
        atr = self._safe_positive(raw_atr, fallback=fallback)
        if atr < min_atr:
            atr = min_atr
        if atr > max_atr:
            atr = max_atr
        return atr

    def _resolve_signal_strength(
        self,
        *,
        raw_win_prob: float,
        profile: dict[str, float | int | str],
        profile_win_rate: float,
    ) -> float:
        model_prob_component = self._normalize_strength_component(
            raw_win_prob,
            floor=self.SIGNAL_PROB_FLOOR,
            ceil=self.SIGNAL_PROB_CEIL,
        )
        profile_win_component = self._normalize_strength_component(
            profile_win_rate,
            floor=self.SIGNAL_PROB_FLOOR,
            ceil=self.SIGNAL_PROB_CEIL,
        )
        profile_score_component = self._normalize_strength_component(
            profile.get("score"),
            floor=self.PROFILE_SCORE_FLOOR,
            ceil=self.PROFILE_SCORE_CEIL,
        )
        fill_rate_component = self._normalize_strength_component(
            profile.get("fill_rate"),
            floor=0.18,
            ceil=0.75,
        )
        samples = max(0.0, float(profile.get("fills") or profile.get("samples") or 0.0))
        sample_component = float(
            np.clip(samples / max(1.0, float(self.profile_min_samples) * 3.0), 0.0, 1.0)
        )
        strength = (
            (model_prob_component * 0.5)
            + (profile_win_component * 0.2)
            + (profile_score_component * 0.15)
            + (fill_rate_component * 0.1)
            + (sample_component * 0.05)
        )
        return float(np.clip(strength, 0.0, 1.0))

    @staticmethod
    def _normalize_strength_component(value: object, *, floor: float, ceil: float) -> float:
        try:
            numeric = float(value)
        except Exception:
            numeric = floor
        if not np.isfinite(numeric):
            numeric = floor
        if ceil <= floor:
            return float(np.clip(numeric, 0.0, 1.0))
        return float(np.clip((numeric - floor) / (ceil - floor), 0.0, 1.0))

    @classmethod
    def _build_profile_exit_targets(
        cls,
        *,
        side: str,
        entry: float,
        atr: float,
        win_prob: float,
        profile_tp_multiplier: float,
        signal_strength: float,
    ) -> tuple[float, float]:
        tp_scale = cls.TP_STRENGTH_SCALE_MIN + (
            (cls.TP_STRENGTH_SCALE_MAX - cls.TP_STRENGTH_SCALE_MIN) * signal_strength
        )
        sl_scale = cls.SL_STRENGTH_SCALE_MIN + (
            (cls.SL_STRENGTH_SCALE_MAX - cls.SL_STRENGTH_SCALE_MIN) * signal_strength
        )
        tp_distance = atr * max(0.9, float(profile_tp_multiplier) * tp_scale)
        base_sl_multiplier = max(0.75, 1.45 - (float(win_prob) * 0.25))
        sl_distance = atr * max(0.7, base_sl_multiplier * sl_scale)

        if side == "LONG":
            return entry + tp_distance, entry - sl_distance
        return entry - tp_distance, entry + sl_distance

    @staticmethod
    def _safe_positive(value: object, *, fallback: float) -> float:
        try:
            num = float(value)
        except Exception:
            return float(fallback)
        if not np.isfinite(num) or num <= 0:
            return float(fallback)
        return float(num)

    @classmethod
    def _round_price(cls, value: float) -> float:
        safe_value = max(float(value), cls.MIN_PRICE)
        if safe_value >= 1.0:
            decimals = 6
        elif safe_value >= 0.01:
            decimals = 8
        elif safe_value >= 0.0001:
            decimals = 10
        else:
            decimals = 12
        rounded = round(safe_value, decimals)
        if rounded <= 0:
            return safe_value
        return rounded

    def _sanitize_signal(
        self,
        *,
        symbol: str,
        side: str,
        win_prob: float,
        entry: float,
        stop_loss: float,
        take_profit: float,
        mark_price: float,
        atr: float,
        feature_snapshot: dict[str, float] | None = None,
    ) -> SignalResult:
        mark = self._safe_positive(mark_price, fallback=1.0)
        safe_entry = self._safe_positive(entry, fallback=mark)
        safe_atr = self._normalize_atr(raw_atr=atr, mark_price=mark)
        min_distance = max(mark * self.MIN_DISTANCE_PCT, safe_atr * 0.2, self.MIN_PRICE * 10)

        side_key = str(side or "LONG").upper()
        if side_key == "SHORT":
            tp = min(float(take_profit), safe_entry - min_distance)
            sl = max(float(stop_loss), safe_entry + min_distance)
            if tp <= 0:
                tp = max(safe_entry - min_distance, self.MIN_PRICE)
            if not (tp < safe_entry < sl):
                tp = max(safe_entry - min_distance, self.MIN_PRICE)
                sl = safe_entry + min_distance
            safe_side = "SHORT"
        else:
            sl = min(float(stop_loss), safe_entry - min_distance)
            tp = max(float(take_profit), safe_entry + min_distance)
            if sl <= 0:
                sl = max(safe_entry - min_distance, self.MIN_PRICE)
            if not (sl < safe_entry < tp):
                sl = max(safe_entry - min_distance, self.MIN_PRICE)
                tp = safe_entry + min_distance
            safe_side = "LONG"

        return SignalResult(
            symbol=symbol,
            side=safe_side,
            win_probability=float(np.clip(win_prob, 0.0, 0.99)),
            predicted_entry_price=self._round_price(safe_entry),
            stop_loss=self._round_price(sl),
            take_profit=self._round_price(tp),
            feature_snapshot=feature_snapshot,
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
                database=self.repo_database,
            )
            return repo.list_feedback(limit=limit)
        except Exception:
            return []

    def _load_hourly_profile_map(self) -> dict[str, dict[str, dict[int, dict[str, Any]]]]:
        if not settings.mysql_enabled:
            return {}
        try:
            repo = MySQLTradeRepository(
                host=settings.mysql_host,
                port=settings.mysql_port,
                user=settings.mysql_user,
                password=settings.mysql_password,
                database=self.repo_database,
            )
            repo.refresh_hourly_profiles(lookback_days=settings.paper_trade_hourly_profile_lookback_days)
            return repo.hourly_profile_map()
        except Exception:
            return {}

    def _build_optimization_profiles(
        self,
        *,
        symbols: list[str],
        limit: int,
        horizon: int,
    ) -> tuple[dict[str, dict[str, float | int | str]], int]:
        bucket_stats: dict[str, dict[str, dict[str, float | int | str]]] = {}
        sampled_rows = 0
        safe_horizon = max(2, int(horizon))
        frame_limit = max(320, int(limit))

        for symbol in symbols:
            try:
                frame = self.pipeline.build_symbol_frame(symbol=symbol, limit=frame_limit)
            except Exception:
                continue
            frame = frame.dropna(
                subset=[
                    "close_m5",
                    "high_m5",
                    "low_m5",
                    "atr14_m5",
                    "rsi14_m5",
                    "close_h1",
                    "ema8_h1",
                    "ema13_h1",
                    "ema21_h1",
                    "setup_side",
                ]
            ).reset_index(drop=True)
            if frame.empty:
                continue

            upper_bound = len(frame) - safe_horizon - 1
            if upper_bound <= 0:
                continue

            for idx in range(upper_bound):
                row = frame.iloc[idx]
                close_price = float(row.get("close_m5") or 0.0)
                atr = float(row.get("atr14_m5") or 0.0)
                setup_side = row.get("setup_side")
                if close_price <= 0 or atr <= 0 or pd.isna(setup_side):
                    continue

                side = "LONG" if float(setup_side) >= 0.5 else "SHORT"
                future = frame.iloc[idx + 1 : idx + 1 + safe_horizon]
                if future.empty:
                    continue

                sampled_rows += 1
                bucket = self._profile_bucket_from_row(row=row, side=side)
                bucket_map = bucket_stats.setdefault(bucket, {})

                for entry_offset_atr in self.ENTRY_OFFSET_GRID:
                    for tp_atr_multiplier in self.TP_MULTIPLIER_GRID:
                        candidate_key = f"{entry_offset_atr:.2f}|{tp_atr_multiplier:.2f}"
                        stats = bucket_map.setdefault(
                            candidate_key,
                            {
                                "side": side,
                                "entry_offset_atr": float(entry_offset_atr),
                                "tp_atr_multiplier": float(tp_atr_multiplier),
                                "samples": 0,
                                "fills": 0,
                                "wins": 0,
                                "losses": 0,
                                "score_sum": 0.0,
                                "latest_win_symbol": "",
                                "latest_win_at": "",
                            },
                        )
                        outcome = self._simulate_profile_candidate(
                            side=side,
                            close_price=close_price,
                            atr=atr,
                            future=future,
                            entry_offset_atr=float(entry_offset_atr),
                            tp_atr_multiplier=float(tp_atr_multiplier),
                        )
                        stats["samples"] = int(stats["samples"]) + 1
                        if outcome["filled"]:
                            stats["fills"] = int(stats["fills"]) + 1
                        if outcome["result"] == 1:
                            stats["wins"] = int(stats["wins"]) + 1
                            self._update_latest_win_reference(
                                stats=stats,
                                symbol=symbol,
                                row_timestamp=row.get("timestamp"),
                            )
                        elif outcome["result"] == 0:
                            stats["losses"] = int(stats["losses"]) + 1
                        stats["score_sum"] = float(stats["score_sum"]) + float(outcome["score"])

        profiles: dict[str, dict[str, float | int | str]] = {}
        total_profile_samples = 0
        min_fills = max(6, int(self.profile_min_samples))

        for bucket, candidates in bucket_stats.items():
            best_profile: dict[str, float | int | str] | None = None
            best_objective: float | None = None
            for stats in candidates.values():
                samples = int(stats["samples"])
                fills = int(stats["fills"])
                wins = int(stats["wins"])
                losses = int(stats["losses"])
                if samples <= 0 or fills < min_fills:
                    continue

                fill_rate = fills / samples
                win_rate = wins / fills if fills > 0 else 0.0
                avg_score = float(stats["score_sum"]) / samples
                objective = avg_score + (win_rate * 0.25) + (fill_rate * 0.15)
                if fill_rate < 0.18:
                    objective -= 0.2

                if best_objective is None or objective > best_objective:
                    best_objective = objective
                    best_profile = {
                        "side": str(stats["side"]),
                        "entry_offset_atr": round(float(stats["entry_offset_atr"]), 4),
                        "tp_atr_multiplier": round(float(stats["tp_atr_multiplier"]), 4),
                        "samples": samples,
                        "fills": fills,
                        "wins": wins,
                        "losses": losses,
                        "fill_rate": round(fill_rate, 4),
                        "win_rate": round(win_rate, 4),
                        "score": round(objective, 4),
                        "latest_win_symbol": str(stats.get("latest_win_symbol") or ""),
                        "latest_win_at": str(stats.get("latest_win_at") or ""),
                    }

            if best_profile is not None:
                profiles[bucket] = best_profile
                total_profile_samples += int(best_profile.get("samples") or 0)

        return profiles, total_profile_samples

    @staticmethod
    def _simulate_profile_candidate(
        *,
        side: str,
        close_price: float,
        atr: float,
        future: pd.DataFrame,
        entry_offset_atr: float,
        tp_atr_multiplier: float,
    ) -> dict[str, float | int | bool | None]:
        entry = close_price - (atr * entry_offset_atr) if side == "LONG" else close_price + (atr * entry_offset_atr)
        stop_loss = entry - atr if side == "LONG" else entry + atr
        take_profit = entry + (atr * tp_atr_multiplier) if side == "LONG" else entry - (atr * tp_atr_multiplier)

        filled = entry_offset_atr <= 1e-12
        for candle in future.itertuples(index=False):
            low = float(getattr(candle, "low_m5"))
            high = float(getattr(candle, "high_m5"))

            if not filled:
                if side == "LONG":
                    if low > entry:
                        continue
                else:
                    if high < entry:
                        continue
                filled = True

            if side == "LONG":
                if low <= stop_loss:
                    return {"filled": True, "result": 0, "score": -1.0}
                if high >= take_profit:
                    return {"filled": True, "result": 1, "score": float(tp_atr_multiplier)}
            else:
                if high >= stop_loss:
                    return {"filled": True, "result": 0, "score": -1.0}
                if low <= take_profit:
                    return {"filled": True, "result": 1, "score": float(tp_atr_multiplier)}

        if not filled:
            return {"filled": False, "result": None, "score": -0.04 - (entry_offset_atr * 0.05)}
        return {"filled": True, "result": None, "score": 0.04}

    def _resolve_profile(self, *, row: pd.Series | None, side: str) -> dict[str, float | int | str] | None:
        if row is None or not self.entry_profiles:
            return None

        bucket = self._profile_bucket_from_row(row=row, side=side)
        exact = self.entry_profiles.get(bucket)
        if exact:
            return exact

        side_key = f"{side}|"
        candidates = [item for key, item in self.entry_profiles.items() if key.startswith(side_key)]
        if not candidates:
            return None
        return max(
            candidates,
            key=lambda item: (
                float(item.get("score") or 0.0),
                float(item.get("fills") or 0.0),
                float(item.get("win_rate") or 0.0),
            ),
        )

    def _attach_reference_win(
        self,
        signal: SignalResult,
        *,
        profile: dict[str, float | int | str] | None,
        side: str,
    ) -> SignalResult:
        reference_symbol = str((profile or {}).get("latest_win_symbol") or "").strip() or None
        reference_at = str((profile or {}).get("latest_win_at") or "").strip() or None
        if reference_symbol:
            signal.reference_win_symbol = reference_symbol
            signal.reference_win_at = reference_at
            return signal

        fallback = self._latest_profitable_reference(side=side)
        if fallback:
            signal.reference_win_symbol = fallback.get("symbol")
            signal.reference_win_at = fallback.get("closed_at")
        return signal

    def _latest_profitable_reference(self, *, side: str) -> dict[str, str | None] | None:
        side_key = str(side or "").upper()
        if side_key not in {"LONG", "SHORT"} or not settings.mysql_enabled:
            return None

        now_ts = time.time()
        cached = self._recent_win_reference_cache.get(side_key)
        if cached and now_ts < cached[0]:
            return cached[1]

        payload: dict[str, str | None] | None = None
        try:
            repo = MySQLTradeRepository(
                host=settings.mysql_host,
                port=settings.mysql_port,
                user=settings.mysql_user,
                password=settings.mysql_password,
                database=self.repo_database,
            )
            row = repo.latest_profitable_trade_reference(
                side=side_key,
                entry_type_prefix="ML_CANDLES",
            )
            if row:
                payload = {
                    "symbol": str(row.get("symbol") or "") or None,
                    "closed_at": str(
                        row.get("closed_at")
                        or row.get("updated_at")
                        or row.get("opened_at")
                        or ""
                    ) or None,
                }
        except Exception:
            payload = None

        self._recent_win_reference_cache[side_key] = (now_ts + 60.0, payload)
        return payload

    @staticmethod
    def _update_latest_win_reference(
        *,
        stats: dict[str, float | int | str],
        symbol: str,
        row_timestamp: object,
    ) -> None:
        latest_ts = MLCandlesPredictor._coerce_reference_timestamp(stats.get("latest_win_at"))
        current_ts = MLCandlesPredictor._coerce_reference_timestamp(row_timestamp)
        if current_ts is None:
            return
        if latest_ts is not None and current_ts < latest_ts:
            return
        stats["latest_win_symbol"] = str(symbol)
        stats["latest_win_at"] = current_ts.isoformat()

    @staticmethod
    def _coerce_reference_timestamp(value: object) -> pd.Timestamp | None:
        if value is None:
            return None
        try:
            ts = pd.Timestamp(value)
        except Exception:
            return None
        if pd.isna(ts):
            return None
        return ts

    def _profile_bucket_from_row(self, *, row: pd.Series, side: str) -> str:
        close_m5 = float(row.get("close_m5") or 0.0)
        atr14_m5 = float(row.get("atr14_m5") or 0.0)
        rsi14_m5 = float(row.get("rsi14_m5") or 50.0)
        atr_pct = (atr14_m5 / close_m5) if close_m5 > 0 else 0.0

        if atr_pct < 0.0035:
            vol_bucket = "LOW"
        elif atr_pct < 0.008:
            vol_bucket = "MID"
        else:
            vol_bucket = "HIGH"

        if rsi14_m5 <= 42:
            rsi_bucket = "LOW"
        elif rsi14_m5 >= 58:
            rsi_bucket = "HIGH"
        else:
            rsi_bucket = "MID"

        trend_bucket = self._trend_bucket(row)
        return f"{side}|VOL:{vol_bucket}|RSI:{rsi_bucket}|TREND:{trend_bucket}"

    @staticmethod
    def _trend_bucket(row: pd.Series) -> str:
        close_h1 = float(row.get("close_h1") or 0.0)
        ema8_h1 = float(row.get("ema8_h1") or 0.0)
        ema13_h1 = float(row.get("ema13_h1") or 0.0)
        ema21_h1 = float(row.get("ema21_h1") or 0.0)

        if close_h1 >= ema21_h1 and ema8_h1 >= ema13_h1 >= ema21_h1:
            return "UP"
        if close_h1 <= ema21_h1 and ema8_h1 <= ema13_h1 <= ema21_h1:
            return "DOWN"
        return "FLAT"
