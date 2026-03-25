from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import math
import threading
import time
from typing import Any

import pymysql

_VN_TZ = timezone(timedelta(hours=7))

from app.core.config import settings
from app.services.ml_predictor import MLPredictor


class SignalCandlePatternService:
    def __init__(self) -> None:
        self.enabled = bool(settings.mysql_enabled)
        self._lock = threading.Lock()
        self._cache_expires_at = 0.0
        self._catalog_cache: list[dict[str, Any]] = []
        self.cache_ttl_sec = max(60, int(getattr(settings, "signal_candle_pattern_cache_sec", 300)))
        self.lookback_limit = max(200, int(getattr(settings, "signal_candle_pattern_refresh_limit", 4000)))
        self.min_samples = max(2, int(getattr(settings, "signal_candle_pattern_min_samples", 3)))
        self.example_limit = max(2, int(getattr(settings, "signal_candle_pattern_example_limit", 4)))
        if self.enabled:
            try:
                self._ensure_schema()
            except Exception:
                self.enabled = False

    def _conn(self) -> pymysql.connections.Connection:
        return pymysql.connect(
            host=settings.mysql_host,
            port=settings.mysql_port,
            user=settings.mysql_user,
            password=settings.mysql_password,
            database=settings.mysql_database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )

    def _ensure_schema(self) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS signal_candle_pattern_samples (
                        sample_key VARCHAR(191) PRIMARY KEY,
                        sample_code VARCHAR(32) NOT NULL,
                        signal_source VARCHAR(32) NOT NULL,
                        source_scope VARCHAR(32) NOT NULL,
                        side VARCHAR(10) NOT NULL,
                        market_phase VARCHAR(64) NOT NULL,
                        btc_alignment VARCHAR(24) NOT NULL,
                        setup_kind VARCHAR(64) NOT NULL,
                        volatility_kind VARCHAR(32) NOT NULL,
                        quality_tier VARCHAR(12) NOT NULL,
                        good_pattern TINYINT(1) NOT NULL DEFAULT 0,
                        total_signals INT NOT NULL,
                        wins INT NOT NULL,
                        losses INT NOT NULL,
                        win_rate_pct DOUBLE NOT NULL,
                        avg_pnl_pct DOUBLE NULL,
                        avg_mae_pct DOUBLE NULL,
                        avg_mfe_pct DOUBLE NULL,
                        sample_notes VARCHAR(255) NULL,
                        example_symbols_json LONGTEXT NULL,
                        updated_at DATETIME(6) NOT NULL,
                        INDEX idx_signal_source_side (signal_source, side, good_pattern, win_rate_pct),
                        INDEX idx_quality (quality_tier, total_signals)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                    """
                )

    @staticmethod
    def _safe_float(value: object) -> float | None:
        try:
            if value is None:
                return None
            parsed = float(value)
        except Exception:
            return None
        if not math.isfinite(parsed):
            return None
        return float(parsed)

    @staticmethod
    def _normalize_side(value: object) -> str:
        side = str(value or "").strip().upper()
        return side if side in {"LONG", "SHORT"} else "LONG"

    @staticmethod
    def _normalize_signal_source(value: object) -> str:
        source = str(value or "").strip().upper()
        if source.startswith("ML_CANDLES"):
            return "ML_CANDLES"
        if source == "ML":
            return "ML"
        return source or "ML"

    @staticmethod
    def _normalize_source_scope(value: object, signal_source: str) -> str:
        scope = str(value or "").strip().upper()
        if scope:
            return scope
        return signal_source

    @staticmethod
    def _normalize_btc_alignment(value: object) -> str:
        if value is None:
            return "UNKNOWN"
        if isinstance(value, bool):
            return "FOLLOWING" if value else "NON_FOLLOWING"
        try:
            num = int(value)
            return "FOLLOWING" if num != 0 else "NON_FOLLOWING"
        except Exception:
            pass
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return "FOLLOWING"
        if text in {"0", "false", "no", "n", "off"}:
            return "NON_FOLLOWING"
        return "UNKNOWN"

    @staticmethod
    def _parse_feature_snapshot(raw: object) -> dict[str, float] | None:
        return MLPredictor._parse_feature_snapshot(raw)

    @classmethod
    def _derive_market_phase(cls, snapshot: dict[str, float]) -> str:
        close_h1 = cls._safe_float(snapshot.get("close_h1")) or 0.0
        ema8_h1 = cls._safe_float(snapshot.get("ema8_h1")) or close_h1
        ema13_h1 = cls._safe_float(snapshot.get("ema13_h1")) or close_h1
        ema21_h1 = cls._safe_float(snapshot.get("ema21_h1")) or close_h1
        close_m5 = cls._safe_float(snapshot.get("close_m5")) or 0.0
        ema8_m5 = cls._safe_float(snapshot.get("ema8_m5")) or close_m5
        ema13_m5 = cls._safe_float(snapshot.get("ema13_m5")) or close_m5
        ema21_m5 = cls._safe_float(snapshot.get("ema21_m5")) or close_m5
        rsi_h1 = cls._safe_float(snapshot.get("rsi14_h1")) or 50.0
        rsi_m5 = cls._safe_float(snapshot.get("rsi14_m5")) or 50.0

        h1_bull = close_h1 > ema8_h1 > ema13_h1 > ema21_h1
        h1_bear = close_h1 < ema8_h1 < ema13_h1 < ema21_h1
        m5_bull = close_m5 > ema8_m5 > ema13_m5 > ema21_m5
        m5_bear = close_m5 < ema8_m5 < ema13_m5 < ema21_m5
        close_base = abs(close_m5) if abs(close_m5) > 1e-12 else 1.0
        pullback_pct = abs(close_m5 - ema8_m5) / close_base

        if h1_bull and m5_bull:
            if pullback_pct <= 0.004:
                return "BULL_PULLBACK"
            if rsi_h1 >= 62 or rsi_m5 >= 64:
                return "BULL_EXPANSION"
            return "BULL_CONTINUATION"
        if h1_bear and m5_bear:
            if pullback_pct <= 0.004:
                return "BEAR_PULLBACK"
            if rsi_h1 <= 38 or rsi_m5 <= 36:
                return "BEAR_EXPANSION"
            return "BEAR_CONTINUATION"
        if h1_bull and not m5_bull:
            return "BULL_RETRACE"
        if h1_bear and not m5_bear:
            return "BEAR_RETRACE"
        return "RANGE_TRANSITION"

    @classmethod
    def _derive_setup_kind(cls, snapshot: dict[str, float]) -> str:
        liq_imbalance = cls._safe_float(snapshot.get("liq_imbalance_proxy_m5")) or 0.0
        vol_spike = cls._safe_float(snapshot.get("vol_spike_z_m5")) or 0.0
        close_m5 = cls._safe_float(snapshot.get("close_m5")) or 0.0
        ema8_m5 = cls._safe_float(snapshot.get("ema8_m5")) or close_m5
        bb_upper_m5 = cls._safe_float(snapshot.get("bb_upper_m5")) or close_m5
        bb_lower_m5 = cls._safe_float(snapshot.get("bb_lower_m5")) or close_m5
        macd = cls._safe_float(snapshot.get("macd_m5")) or 0.0
        macd_signal = cls._safe_float(snapshot.get("macd_signal_m5")) or 0.0
        macd_delta = macd - macd_signal
        close_base = abs(close_m5) if abs(close_m5) > 1e-12 else 1.0
        ema_gap_pct = abs(close_m5 - ema8_m5) / close_base

        if liq_imbalance >= 0.02:
            return "SHORT_SQUEEZE"
        if liq_imbalance <= -0.02:
            return "LONG_WASH"
        if vol_spike >= 1.5 and abs(macd_delta) >= 0.0001:
            return "VOLUME_EXPANSION"
        if ema_gap_pct <= 0.0035:
            return "EMA_RECLAIM"
        if close_m5 >= bb_upper_m5 * 0.995 or close_m5 <= bb_lower_m5 * 1.005:
            return "BAND_PRESSURE"
        return "STRUCTURED_CONTINUATION"

    @classmethod
    def _derive_volatility_kind(cls, snapshot: dict[str, float]) -> str:
        close_m5 = cls._safe_float(snapshot.get("close_m5")) or 0.0
        atr_m5 = cls._safe_float(snapshot.get("atr14_m5")) or 0.0
        vol_spike = cls._safe_float(snapshot.get("vol_spike_z_m5")) or 0.0
        close_base = abs(close_m5) if abs(close_m5) > 1e-12 else 1.0
        atr_pct = atr_m5 / close_base
        if atr_pct >= 0.02 or vol_spike >= 1.8:
            return "HOT"
        if atr_pct >= 0.01 or vol_spike >= 0.8:
            return "ACTIVE"
        return "CALM"

    @classmethod
    def _sample_key(cls, *, signal_source: str, side: str, market_phase: str, setup_kind: str, volatility_kind: str) -> str:
        return "|".join([signal_source, side, market_phase, setup_kind, volatility_kind])

    @classmethod
    def _quality_tier(cls, total_signals: int, win_rate_pct: float, avg_pnl_pct: float) -> tuple[str, int]:
        if total_signals >= 6 and win_rate_pct >= 70.0 and avg_pnl_pct > 0.2:
            return "A", 1
        if total_signals >= 4 and win_rate_pct >= 60.0 and avg_pnl_pct >= 0.0:
            return "B", 1
        return "C", 0

    @classmethod
    def _build_sample_notes(
        cls,
        *,
        market_phase: str,
        setup_kind: str,
        volatility_kind: str,
        btc_alignment: str,
        win_rate_pct: float,
        total_signals: int,
    ) -> str:
        return (
            f"{market_phase} | {setup_kind} | {volatility_kind} | BTC {btc_alignment} | "
            f"win {win_rate_pct:.1f}% over {total_signals} signals"
        )

    @classmethod
    def _row_pnl_pct(cls, row: dict[str, Any]) -> float:
        explicit = cls._safe_float(row.get("pnl_pct"))
        if explicit is not None:
            return explicit
        entry = cls._safe_float(row.get("entry_price"))
        close = cls._safe_float(row.get("close_price"))
        leverage = cls._safe_float(row.get("leverage")) or 1.0
        side = cls._normalize_side(row.get("side"))
        if entry is None or close is None or entry <= 0:
            pnl_value = cls._safe_float(row.get("pnl"))
            margin = cls._safe_float(row.get("margin_usdt"))
            if pnl_value is not None and margin is not None and margin > 0:
                return (pnl_value / margin) * 100.0
            return 0.0
        move = ((close - entry) / entry) if side == "LONG" else ((entry - close) / entry)
        return move * leverage * 100.0

    @classmethod
    def _row_is_win(cls, row: dict[str, Any]) -> bool:
        close_reason = str(row.get("close_reason") or "").strip().upper()
        if close_reason == "TP":
            return True
        if close_reason == "SL":
            return False
        result = row.get("result")
        try:
            return int(result) == 1
        except Exception:
            return cls._row_pnl_pct(row) > 0

    def _load_source_rows(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        rows: list[dict[str, Any]] = []
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        'ML' AS signal_source,
                        'ML' AS source_scope,
                        f.symbol,
                        f.side,
                        p.btc_following,
                        p.close_reason,
                        f.result,
                        f.pnl,
                        f.pnl_pct,
                        f.mae_pct,
                        f.mfe_pct,
                        p.entry_price,
                        p.close_price,
                        p.margin_usdt,
                        p.leverage,
                        f.feature_snapshot_json,
                        COALESCE(f.created_at, p.closed_at, p.updated_at, p.opened_at) AS sample_time
                    FROM ml_feedback f
                    LEFT JOIN paper_trades p ON p.id = f.paper_trade_id
                    WHERE f.feature_snapshot_json IS NOT NULL
                    ORDER BY COALESCE(f.created_at, p.closed_at, p.updated_at, p.opened_at) DESC
                    LIMIT %s
                    """,
                    (self.lookback_limit,),
                )
                rows.extend(list(cur.fetchall() or []))
                cur.execute(
                    """
                    SELECT
                        'ML_CANDLES' AS signal_source,
                        COALESCE(NULLIF(entry_type, ''), 'ML_CANDLES') AS source_scope,
                        symbol,
                        side,
                        btc_following,
                        close_reason,
                        result,
                        pnl,
                        NULL AS pnl_pct,
                        mae_pct,
                        mfe_pct,
                        entry_price,
                        close_price,
                        margin_usdt,
                        leverage,
                        feature_snapshot_json,
                        COALESCE(closed_at, updated_at, opened_at) AS sample_time
                    FROM paper_trades_ml_candles
                    WHERE feature_snapshot_json IS NOT NULL
                      AND status = 'CLOSED'
                    ORDER BY COALESCE(closed_at, updated_at, opened_at) DESC
                    LIMIT %s
                    """,
                    (self.lookback_limit,),
                )
                rows.extend(list(cur.fetchall() or []))
        return rows

    def _refresh_catalog_locked(self) -> list[dict[str, Any]]:
        rows = self._load_source_rows()
        aggregates: dict[str, dict[str, Any]] = {}

        for row in rows:
            snapshot = self._parse_feature_snapshot(row.get("feature_snapshot_json"))
            if not snapshot:
                continue
            signal_source = self._normalize_signal_source(row.get("signal_source"))
            side = self._normalize_side(row.get("side"))
            source_scope = self._normalize_source_scope(row.get("source_scope"), signal_source)
            btc_alignment = self._normalize_btc_alignment(row.get("btc_following"))
            market_phase = self._derive_market_phase(snapshot)
            setup_kind = self._derive_setup_kind(snapshot)
            volatility_kind = self._derive_volatility_kind(snapshot)
            sample_key = self._sample_key(
                signal_source=signal_source,
                side=side,
                market_phase=market_phase,
                setup_kind=setup_kind,
                volatility_kind=volatility_kind,
            )
            sample = aggregates.get(sample_key)
            if sample is None:
                sample = {
                    "sample_key": sample_key,
                    "sample_code": "",
                    "signal_source": signal_source,
                    "source_scope": source_scope,
                    "side": side,
                    "market_phase": market_phase,
                    "btc_alignment": btc_alignment,
                    "setup_kind": setup_kind,
                    "volatility_kind": volatility_kind,
                    "total_signals": 0,
                    "wins": 0,
                    "losses": 0,
                    "pnl_sum": 0.0,
                    "mae_sum": 0.0,
                    "mfe_sum": 0.0,
                    "mae_count": 0,
                    "mfe_count": 0,
                    "example_symbols": [],
                    "_example_seen": set(),
                }
                aggregates[sample_key] = sample
            sample["total_signals"] += 1
            is_win = self._row_is_win(row)
            if is_win:
                sample["wins"] += 1
            else:
                sample["losses"] += 1
            pnl_pct = self._row_pnl_pct(row)
            mae_pct = self._safe_float(row.get("mae_pct"))
            mfe_pct = self._safe_float(row.get("mfe_pct"))
            sample["pnl_sum"] += pnl_pct
            if mae_pct is not None:
                sample["mae_sum"] += mae_pct
                sample["mae_count"] += 1
            if mfe_pct is not None:
                sample["mfe_sum"] += mfe_pct
                sample["mfe_count"] += 1
            symbol = str(row.get("symbol") or "").strip().upper()
            if symbol and symbol not in sample["_example_seen"] and len(sample["example_symbols"]) < self.example_limit:
                sample["_example_seen"].add(symbol)
                sample["example_symbols"].append(symbol)

        filtered: list[dict[str, Any]] = []
        for sample in aggregates.values():
            total_signals = int(sample["total_signals"])
            if total_signals < self.min_samples:
                continue
            wins = int(sample["wins"])
            losses = int(sample["losses"])
            avg_pnl_pct = float(sample["pnl_sum"]) / total_signals if total_signals else 0.0
            avg_mae_pct = (float(sample["mae_sum"]) / int(sample["mae_count"])) if int(sample["mae_count"]) else None
            avg_mfe_pct = (float(sample["mfe_sum"]) / int(sample["mfe_count"])) if int(sample["mfe_count"]) else None
            win_rate_pct = (wins / total_signals) * 100.0 if total_signals else 0.0
            quality_tier, good_pattern = self._quality_tier(total_signals, win_rate_pct, avg_pnl_pct)
            filtered.append(
                {
                    "sample_key": sample["sample_key"],
                    "sample_code": "",
                    "signal_source": sample["signal_source"],
                    "source_scope": sample["source_scope"],
                    "side": sample["side"],
                    "market_phase": sample["market_phase"],
                    "btc_alignment": sample["btc_alignment"],
                    "setup_kind": sample["setup_kind"],
                    "volatility_kind": sample["volatility_kind"],
                    "quality_tier": quality_tier,
                    "good_pattern": good_pattern,
                    "total_signals": total_signals,
                    "wins": wins,
                    "losses": losses,
                    "win_rate_pct": round(win_rate_pct, 2),
                    "avg_pnl_pct": round(avg_pnl_pct, 4),
                    "avg_mae_pct": None if avg_mae_pct is None else round(avg_mae_pct, 4),
                    "avg_mfe_pct": None if avg_mfe_pct is None else round(avg_mfe_pct, 4),
                    "sample_notes": self._build_sample_notes(
                        market_phase=sample["market_phase"],
                        setup_kind=sample["setup_kind"],
                        volatility_kind=sample["volatility_kind"],
                        btc_alignment=sample["btc_alignment"],
                        win_rate_pct=win_rate_pct,
                        total_signals=total_signals,
                    ),
                    "example_symbols": list(sample["example_symbols"]),
                }
            )

        filtered.sort(
            key=lambda item: (
                0 if item["signal_source"] == "ML" else 1,
                -int(item["good_pattern"]),
                item["quality_tier"],
                -float(item["win_rate_pct"]),
                -int(item["total_signals"]),
                item["sample_key"],
            )
        )
        source_counter: defaultdict[str, int] = defaultdict(int)
        for item in filtered:
            source_counter[item["signal_source"]] += 1
            item["sample_code"] = f"sample{source_counter[item['signal_source']]}"

        self._persist_catalog(filtered)
        self._catalog_cache = filtered
        self._cache_expires_at = time.time() + self.cache_ttl_sec
        return filtered

    def _persist_catalog(self, rows: list[dict[str, Any]]) -> None:
        if not self.enabled:
            return
        now = datetime.now(_VN_TZ).replace(tzinfo=None)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM signal_candle_pattern_samples")
                if not rows:
                    return
                cur.executemany(
                    """
                    INSERT INTO signal_candle_pattern_samples (
                        sample_key, sample_code, signal_source, source_scope, side,
                        market_phase, btc_alignment, setup_kind, volatility_kind,
                        quality_tier, good_pattern, total_signals, wins, losses,
                        win_rate_pct, avg_pnl_pct, avg_mae_pct, avg_mfe_pct,
                        sample_notes, example_symbols_json, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s, %s, %s,
                        %s, %s, %s, %s,
                        %s, %s, %s
                    )
                    """,
                    [
                        (
                            row["sample_key"],
                            row["sample_code"],
                            row["signal_source"],
                            row["source_scope"],
                            row["side"],
                            row["market_phase"],
                            row["btc_alignment"],
                            row["setup_kind"],
                            row["volatility_kind"],
                            row["quality_tier"],
                            int(row["good_pattern"]),
                            int(row["total_signals"]),
                            int(row["wins"]),
                            int(row["losses"]),
                            float(row["win_rate_pct"]),
                            row["avg_pnl_pct"],
                            row["avg_mae_pct"],
                            row["avg_mfe_pct"],
                            row["sample_notes"],
                            json.dumps(row.get("example_symbols") or [], ensure_ascii=True),
                            now,
                        )
                        for row in rows
                    ],
                )

    def _load_catalog_from_db(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        sample_key, sample_code, signal_source, source_scope, side,
                        market_phase, btc_alignment, setup_kind, volatility_kind,
                        quality_tier, good_pattern, total_signals, wins, losses,
                        win_rate_pct, avg_pnl_pct, avg_mae_pct, avg_mfe_pct,
                        sample_notes, example_symbols_json, updated_at
                    FROM signal_candle_pattern_samples
                    ORDER BY signal_source ASC, sample_code ASC
                    """
                )
                rows = list(cur.fetchall() or [])
        for row in rows:
            try:
                row["example_symbols"] = json.loads(row.get("example_symbols_json") or "[]")
            except Exception:
                row["example_symbols"] = []
            row.pop("example_symbols_json", None)
        return rows

    def get_catalog(self, *, signal_source: str | None = None, force_refresh: bool = False) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        normalized_source = self._normalize_signal_source(signal_source) if signal_source else None
        with self._lock:
            needs_refresh = force_refresh or not self._catalog_cache or time.time() >= self._cache_expires_at
            if needs_refresh:
                try:
                    self._refresh_catalog_locked()
                except Exception:
                    self._catalog_cache = self._load_catalog_from_db()
                    self._cache_expires_at = time.time() + self.cache_ttl_sec
            rows = list(self._catalog_cache)
        if normalized_source:
            rows = [row for row in rows if str(row.get("signal_source") or "").upper() == normalized_source]
        return rows

    def match_signal(
        self,
        *,
        signal_source: str,
        side: str,
        feature_snapshot: dict[str, float] | None,
        live_btc_phase: str | None = None,
    ) -> dict[str, Any] | None:
        if not feature_snapshot:
            return None
        normalized_source = self._normalize_signal_source(signal_source)
        normalized_side = self._normalize_side(side)
        market_phase = self._derive_market_phase(feature_snapshot)
        setup_kind = self._derive_setup_kind(feature_snapshot)
        volatility_kind = self._derive_volatility_kind(feature_snapshot)
        rows = self.get_catalog(signal_source=normalized_source)
        if not rows:
            return None
        same_side = [row for row in rows if str(row.get("side") or "").upper() == normalized_side]
        if not same_side:
            return None

        def _pick(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
            if not candidates:
                return None
            return max(
                candidates,
                key=lambda item: (
                    int(item.get("good_pattern") or 0),
                    float(item.get("win_rate_pct") or 0.0),
                    int(item.get("total_signals") or 0),
                ),
            )

        exact = _pick(
            [
                row for row in same_side
                if row.get("market_phase") == market_phase
                and row.get("setup_kind") == setup_kind
                and row.get("volatility_kind") == volatility_kind
            ]
        )
        match = exact
        match_strength = "EXACT"
        if match is None:
            match = _pick(
                [
                    row for row in same_side
                    if row.get("market_phase") == market_phase
                    and row.get("setup_kind") == setup_kind
                ]
            )
            match_strength = "PHASE_SETUP"
        if match is None:
            match = _pick([row for row in same_side if row.get("market_phase") == market_phase])
            match_strength = "PHASE"
        if match is None:
            return None
        payload = dict(match)
        payload["match_strength"] = match_strength
        payload["live_market_phase"] = market_phase
        payload["live_setup_kind"] = setup_kind
        payload["live_volatility_kind"] = volatility_kind
        payload["live_btc_phase"] = live_btc_phase
        return payload


signal_candle_pattern_service = SignalCandlePatternService()