from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

from app.services.candle_pattern_analyzer import CandlePatternAnalyzer
from app.services.mysql_trade_repo import MySQLTradeRepository


def _parse_dt(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized)
    except Exception:
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(text, fmt)
            except Exception:
                continue
    return None


def _normalize_label(value: object) -> str:
    return str(value or "").strip().upper()


def _is_ml_basic_entry_type(value: object) -> bool:
    return _normalize_label(value) == "LIMIT"


class PatternPerformanceResolver:
    def __init__(
        self,
        *,
        analyzer: CandlePatternAnalyzer,
        repos: list[MySQLTradeRepository | None],
        lookback: int = 180,
        cache_ttl_sec: float = 60.0,
    ) -> None:
        self.analyzer = analyzer
        self.repos = [repo for repo in repos if repo is not None]
        self.lookback = max(20, int(lookback))
        self.cache_ttl_sec = max(5.0, float(cache_ttl_sec))
        self._cache_expiry_ts = 0.0
        self._exact_map: dict[str, dict[str, float | int | str]] = {}
        self._pattern_map: dict[str, dict[str, float | int | str]] = {}

    def resolve_live_pattern_stat(self, symbol: str) -> dict[str, float | int | str] | None:
        safe_symbol = str(symbol or "").strip()
        if not safe_symbol:
            return None
        try:
            pattern = _normalize_label(self.analyzer.current_symbol_pattern(safe_symbol))
        except Exception:
            pattern = ""
        try:
            btc_trend = _normalize_label(self.analyzer.btc_trend_now())
        except Exception:
            btc_trend = ""
        if not pattern:
            return None

        exact_map, pattern_map = self._get_stat_maps()
        if pattern and btc_trend:
            exact = exact_map.get(f"{pattern}|{btc_trend}")
            if exact is not None:
                return exact
        return pattern_map.get(pattern)

    def has_perfect_live_pattern(
        self,
        symbol: str,
        *,
        min_win_rate_pct: float = 100.0,
        min_trades: int = 1,
    ) -> bool:
        stat = self.resolve_live_pattern_stat(symbol)
        if stat is None:
            return False
        total_trades = int(stat.get("total_trades") or 0)
        losses = int(stat.get("losses") or 0)
        win_rate_pct = float(stat.get("win_rate_pct") or 0.0)
        return total_trades >= max(1, int(min_trades)) and losses == 0 and win_rate_pct >= float(min_win_rate_pct)

    def _get_stat_maps(self) -> tuple[dict[str, dict[str, float | int | str]], dict[str, dict[str, float | int | str]]]:
        now = time.time()
        if now < self._cache_expiry_ts and self._exact_map and self._pattern_map:
            return self._exact_map, self._pattern_map

        exact_map, pattern_map = self._build_stat_maps()
        self._exact_map = exact_map
        self._pattern_map = pattern_map
        self._cache_expiry_ts = now + self.cache_ttl_sec
        return exact_map, pattern_map

    def _build_stat_maps(self) -> tuple[dict[str, dict[str, float | int | str]], dict[str, dict[str, float | int | str]]]:
        rows: list[dict[str, Any]] = []
        for repo in self.repos:
            try:
                rows.extend(repo.list_recent_closed_trades(limit=self.lookback))
            except Exception:
                continue

        deduped_rows = self._dedupe_rows(rows)
        exact_map: dict[str, dict[str, float | int | str]] = {}
        pattern_map: dict[str, dict[str, float | int | str]] = {}

        for row in deduped_rows:
            if not _is_ml_basic_entry_type(row.get("entry_type")):
                continue
            closed_at = _parse_dt(row.get("closed_at")) or _parse_dt(row.get("opened_at"))
            if closed_at is None:
                continue
            pattern = _normalize_label(row.get("close_candle_pattern"))
            btc_trend = _normalize_label(row.get("btc_trend_at_close"))
            if not pattern:
                try:
                    pattern = _normalize_label(
                        self.analyzer.symbol_pattern_at(str(row.get("symbol") or ""), closed_at)
                    )
                except Exception:
                    pattern = ""
            if not btc_trend:
                try:
                    btc_trend = _normalize_label(self.analyzer.btc_trend_at(closed_at))
                except Exception:
                    btc_trend = ""
            if not pattern:
                continue
            if not btc_trend:
                btc_trend = "UNKNOWN"

            result = row.get("result")
            pnl = float(row.get("pnl") or 0.0)
            exact_key = f"{pattern}|{btc_trend}"
            exact_bucket = exact_map.setdefault(
                exact_key,
                {
                    "candle_pattern": pattern,
                    "btc_trend": btc_trend,
                    "total_trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "net_pnl": 0.0,
                    "avg_pnl": 0.0,
                    "win_rate_pct": 0.0,
                },
            )
            self._accumulate_bucket(exact_bucket, result=result, pnl=pnl)

            pattern_bucket = pattern_map.setdefault(
                pattern,
                {
                    "candle_pattern": pattern,
                    "btc_trend": "ALL",
                    "total_trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "net_pnl": 0.0,
                    "avg_pnl": 0.0,
                    "win_rate_pct": 0.0,
                },
            )
            self._accumulate_bucket(pattern_bucket, result=result, pnl=pnl)

        return exact_map, pattern_map

    @staticmethod
    def _accumulate_bucket(bucket: dict[str, float | int | str], *, result: object, pnl: float) -> None:
        total_trades = int(bucket.get("total_trades") or 0) + 1
        wins = int(bucket.get("wins") or 0)
        losses = int(bucket.get("losses") or 0)
        if result is not None:
            if int(result) == 1:
                wins += 1
            else:
                losses += 1
        net_pnl = float(bucket.get("net_pnl") or 0.0) + pnl
        bucket["total_trades"] = total_trades
        bucket["wins"] = wins
        bucket["losses"] = losses
        bucket["net_pnl"] = net_pnl
        bucket["avg_pnl"] = net_pnl / total_trades if total_trades > 0 else 0.0
        bucket["win_rate_pct"] = (wins / total_trades) * 100.0 if total_trades > 0 else 0.0

    @staticmethod
    def _dedupe_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str, str, str, str, str]] = set()
        out: list[dict[str, Any]] = []
        for row in rows:
            key = (
                str(row.get("symbol") or ""),
                str(row.get("side") or ""),
                str(row.get("entry_type") or ""),
                str(row.get("opened_at") or ""),
                str(row.get("closed_at") or ""),
                str(row.get("close_price") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(row)
        out.sort(
            key=lambda item: PatternPerformanceResolver._row_sort_ts(item),
            reverse=True,
        )
        return out[: max(1, len(out))]

    @staticmethod
    def _row_sort_ts(row: dict[str, Any]) -> float:
        dt = _parse_dt(row.get("closed_at")) or _parse_dt(row.get("opened_at"))
        if dt is None:
            return 0.0
        if dt.tzinfo is None:
            return dt.timestamp()
        return dt.astimezone(timezone.utc).timestamp()
