from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from typing import Any

import pymysql


_VN_TZ = timezone(timedelta(hours=7))


def _now_vn() -> datetime:
    # Persist timestamps in Vietnam local time (UTC+7), as requested.
    return datetime.now(_VN_TZ).replace(tzinfo=None)


class MySQLTradeRepository:
    def __init__(self, host: str, port: int, user: str, password: str, database: str) -> None:
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.database = database
        self._init_schema()

    def _conn(self) -> pymysql.connections.Connection:
        return pymysql.connect(
            host=self.host,
            port=self.port,
            user=self.user,
            password=self.password,
            database=self.database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=True,
        )

    def _init_schema(self) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS paper_trades (
                        id BIGINT PRIMARY KEY AUTO_INCREMENT,
                        symbol VARCHAR(64) NOT NULL,
                        side VARCHAR(10) NOT NULL,
                        btc_following TINYINT NULL,
                        entry_type VARCHAR(12) NOT NULL DEFAULT 'LIMIT',
                        signal_win_probability DOUBLE NOT NULL,
                        effective_win_probability DOUBLE NOT NULL,
                        entry_price DOUBLE NOT NULL,
                        take_profit DOUBLE NOT NULL,
                        stop_loss DOUBLE NOT NULL,
                        liq_ema99_15m DOUBLE NULL,
                        liq_ema99_1h DOUBLE NULL,
                        liq_zone_price DOUBLE NULL,
                        liq_zone_score DOUBLE NULL,
                        quantity DOUBLE NOT NULL,
                        margin_usdt DOUBLE NULL,
                        leverage INT NOT NULL,
                        status VARCHAR(20) NOT NULL,
                        opened_at DATETIME(6) NOT NULL,
                        closed_at DATETIME(6) NULL,
                        close_price DOUBLE NULL,
                        close_reason VARCHAR(32) NULL,
                        mae_pct DOUBLE NULL,
                        mfe_pct DOUBLE NULL,
                        feature_snapshot_json LONGTEXT NULL,
                        feature_captured_at DATETIME(6) NULL,
                        pnl DOUBLE NULL,
                        commission_usdt DOUBLE NULL,
                        result TINYINT NULL,
                        created_at DATETIME(6) NOT NULL,
                        updated_at DATETIME(6) NOT NULL,
                        INDEX idx_symbol_status(symbol, status),
                        INDEX idx_status_opened(status, opened_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                    """
                )
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='paper_trades' AND COLUMN_NAME='btc_following'
                    """,
                    (self.database,),
                )
                row = cur.fetchone() or {}
                if int(row.get("cnt") or 0) == 0:
                    cur.execute("ALTER TABLE paper_trades ADD COLUMN btc_following TINYINT NULL AFTER side")
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='paper_trades' AND COLUMN_NAME='margin_usdt'
                    """,
                    (self.database,),
                )
                row = cur.fetchone() or {}
                if int(row.get("cnt") or 0) == 0:
                    cur.execute("ALTER TABLE paper_trades ADD COLUMN margin_usdt DOUBLE NULL AFTER quantity")
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='paper_trades' AND COLUMN_NAME='close_reason'
                    """,
                    (self.database,),
                )
                row = cur.fetchone() or {}
                if int(row.get("cnt") or 0) == 0:
                    cur.execute("ALTER TABLE paper_trades ADD COLUMN close_reason VARCHAR(32) NULL AFTER close_price")
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='paper_trades' AND COLUMN_NAME='entry_type'
                    """,
                    (self.database,),
                )
                row = cur.fetchone() or {}
                if int(row.get("cnt") or 0) == 0:
                    cur.execute(
                        "ALTER TABLE paper_trades ADD COLUMN entry_type VARCHAR(12) NOT NULL DEFAULT 'LIMIT' AFTER side"
                    )
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='paper_trades' AND COLUMN_NAME='mae_pct'
                    """,
                    (self.database,),
                )
                row = cur.fetchone() or {}
                if int(row.get("cnt") or 0) == 0:
                    cur.execute(
                        "ALTER TABLE paper_trades ADD COLUMN mae_pct DOUBLE NULL AFTER close_reason"
                    )
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='paper_trades' AND COLUMN_NAME='mfe_pct'
                    """,
                    (self.database,),
                )
                row = cur.fetchone() or {}
                if int(row.get("cnt") or 0) == 0:
                    cur.execute(
                        "ALTER TABLE paper_trades ADD COLUMN mfe_pct DOUBLE NULL AFTER mae_pct"
                    )
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='paper_trades' AND COLUMN_NAME='liq_ema99_15m'
                    """,
                    (self.database,),
                )
                row = cur.fetchone() or {}
                if int(row.get("cnt") or 0) == 0:
                    cur.execute(
                        "ALTER TABLE paper_trades ADD COLUMN liq_ema99_15m DOUBLE NULL AFTER stop_loss"
                    )
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='paper_trades' AND COLUMN_NAME='liq_ema99_1h'
                    """,
                    (self.database,),
                )
                row = cur.fetchone() or {}
                if int(row.get("cnt") or 0) == 0:
                    cur.execute(
                        "ALTER TABLE paper_trades ADD COLUMN liq_ema99_1h DOUBLE NULL AFTER liq_ema99_15m"
                    )
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='paper_trades' AND COLUMN_NAME='liq_zone_price'
                    """,
                    (self.database,),
                )
                row = cur.fetchone() or {}
                if int(row.get("cnt") or 0) == 0:
                    cur.execute(
                        "ALTER TABLE paper_trades ADD COLUMN liq_zone_price DOUBLE NULL AFTER liq_ema99_1h"
                    )
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='paper_trades' AND COLUMN_NAME='liq_zone_score'
                    """,
                    (self.database,),
                )
                row = cur.fetchone() or {}
                if int(row.get("cnt") or 0) == 0:
                    cur.execute(
                        "ALTER TABLE paper_trades ADD COLUMN liq_zone_score DOUBLE NULL AFTER liq_zone_price"
                    )
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='paper_trades' AND COLUMN_NAME='feature_snapshot_json'
                    """,
                    (self.database,),
                )
                row = cur.fetchone() or {}
                if int(row.get("cnt") or 0) == 0:
                    cur.execute(
                        "ALTER TABLE paper_trades ADD COLUMN feature_snapshot_json LONGTEXT NULL AFTER mfe_pct"
                    )
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='paper_trades' AND COLUMN_NAME='feature_captured_at'
                    """,
                    (self.database,),
                )
                row = cur.fetchone() or {}
                if int(row.get("cnt") or 0) == 0:
                    cur.execute(
                        "ALTER TABLE paper_trades ADD COLUMN feature_captured_at DATETIME(6) NULL AFTER feature_snapshot_json"
                    )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS ml_feedback (
                        id BIGINT PRIMARY KEY AUTO_INCREMENT,
                        paper_trade_id BIGINT NOT NULL,
                        symbol VARCHAR(64) NOT NULL,
                        side VARCHAR(10) NOT NULL,
                        signal_win_probability DOUBLE NOT NULL,
                        effective_win_probability DOUBLE NOT NULL,
                        mae_pct DOUBLE NULL,
                        mfe_pct DOUBLE NULL,
                        feature_snapshot_json LONGTEXT NULL,
                        feature_captured_at DATETIME(6) NULL,
                        result TINYINT NOT NULL,
                        pnl DOUBLE NOT NULL,
                        pnl_pct DOUBLE NULL,
                        created_at DATETIME(6) NOT NULL,
                        INDEX idx_symbol_created(symbol, created_at),
                        INDEX idx_result_created(result, created_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                    """
                )
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='ml_feedback' AND COLUMN_NAME='mae_pct'
                    """,
                    (self.database,),
                )
                row = cur.fetchone() or {}
                if int(row.get("cnt") or 0) == 0:
                    cur.execute(
                        "ALTER TABLE ml_feedback ADD COLUMN mae_pct DOUBLE NULL AFTER effective_win_probability"
                    )
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='ml_feedback' AND COLUMN_NAME='mfe_pct'
                    """,
                    (self.database,),
                )
                row = cur.fetchone() or {}
                if int(row.get("cnt") or 0) == 0:
                    cur.execute(
                        "ALTER TABLE ml_feedback ADD COLUMN mfe_pct DOUBLE NULL AFTER mae_pct"
                    )
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='ml_feedback' AND COLUMN_NAME='feature_snapshot_json'
                    """,
                    (self.database,),
                )
                row = cur.fetchone() or {}
                if int(row.get("cnt") or 0) == 0:
                    cur.execute(
                        "ALTER TABLE ml_feedback ADD COLUMN feature_snapshot_json LONGTEXT NULL AFTER mfe_pct"
                    )
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='ml_feedback' AND COLUMN_NAME='feature_captured_at'
                    """,
                    (self.database,),
                )
                row = cur.fetchone() or {}
                if int(row.get("cnt") or 0) == 0:
                    cur.execute(
                        "ALTER TABLE ml_feedback ADD COLUMN feature_captured_at DATETIME(6) NULL AFTER feature_snapshot_json"
                    )
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='ml_feedback' AND COLUMN_NAME='pnl_pct'
                    """,
                    (self.database,),
                )
                row = cur.fetchone() or {}
                if int(row.get("cnt") or 0) == 0:
                    cur.execute(
                        "ALTER TABLE ml_feedback ADD COLUMN pnl_pct DOUBLE NULL AFTER pnl"
                    )
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='paper_trades' AND COLUMN_NAME='commission_usdt'
                    """,
                    (self.database,),
                )
                row = cur.fetchone() or {}
                if int(row.get("cnt") or 0) == 0:
                    cur.execute(
                        "ALTER TABLE paper_trades ADD COLUMN commission_usdt DOUBLE NULL AFTER pnl"
                    )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS trade_hourly_profiles (
                        scope VARCHAR(96) NOT NULL,
                        side_key VARCHAR(10) NOT NULL,
                        hour_vn TINYINT UNSIGNED NOT NULL,
                        total_orders INT NOT NULL,
                        wins INT NOT NULL,
                        losses INT NOT NULL,
                        breakeven INT NOT NULL,
                        win_rate_pct DOUBLE NOT NULL,
                        loss_rate_pct DOUBLE NOT NULL,
                        net_pnl DOUBLE NOT NULL,
                        avg_pnl DOUBLE NOT NULL,
                        updated_at DATETIME(6) NOT NULL,
                        PRIMARY KEY (scope, side_key, hour_vn),
                        INDEX idx_hour_scope (hour_vn, scope, side_key)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                    """
                )
                cur.execute(
                    """
                    SELECT CHARACTER_MAXIMUM_LENGTH AS max_len
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='trade_hourly_profiles' AND COLUMN_NAME='scope'
                    """,
                    (self.database,),
                )
                scope_len = int((cur.fetchone() or {}).get("max_len") or 0)
                if 0 < scope_len < 96:
                    cur.execute("ALTER TABLE trade_hourly_profiles MODIFY COLUMN scope VARCHAR(96) NOT NULL")

    def create_open_trade(self, payload: dict[str, Any]) -> int:
        now = _now_vn()
        feature_snapshot_json = payload.get("feature_snapshot_json")
        if feature_snapshot_json is None and payload.get("feature_snapshot") is not None:
            raw = payload.get("feature_snapshot")
            if isinstance(raw, str):
                feature_snapshot_json = raw
            else:
                try:
                    feature_snapshot_json = json.dumps(raw, ensure_ascii=True, allow_nan=False)
                except Exception:
                    feature_snapshot_json = None
        feature_captured_at = payload.get("feature_captured_at")
        if feature_snapshot_json is not None and feature_captured_at is None:
            feature_captured_at = now
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO paper_trades (
                        symbol, side, btc_following, entry_type, signal_win_probability, effective_win_probability,
                        entry_price, take_profit, stop_loss, liq_ema99_15m, liq_ema99_1h, liq_zone_price, liq_zone_score,
                        quantity, margin_usdt, leverage, mae_pct, mfe_pct,
                        feature_snapshot_json, feature_captured_at,
                        status, opened_at, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, %s, %s)
                    """,
                    (
                        payload["symbol"],
                        payload["side"],
                        payload.get("btc_following"),
                        payload.get("entry_type", "LIMIT"),
                        payload["signal_win_probability"],
                        payload["effective_win_probability"],
                        payload["entry_price"],
                        payload["take_profit"],
                        payload["stop_loss"],
                        payload.get("liq_ema99_15m"),
                        payload.get("liq_ema99_1h"),
                        payload.get("liq_zone_price"),
                        payload.get("liq_zone_score"),
                        payload["quantity"],
                        payload.get("margin_usdt"),
                        payload["leverage"],
                        payload.get("mae_pct", 0.0),
                        payload.get("mfe_pct", 0.0),
                        feature_snapshot_json,
                        feature_captured_at,
                        now,
                        now,
                        now,
                    ),
                )
                return int(cur.lastrowid)

    def has_open_trade(self, symbol: str, side: str, entry_type: str | None = None) -> bool:
        with self._conn() as conn:
            with conn.cursor() as cur:
                if entry_type:
                    cur.execute(
                        """
                        SELECT id
                        FROM paper_trades
                        WHERE symbol=%s AND side=%s AND entry_type=%s AND status='OPEN'
                        LIMIT 1
                        """,
                        (symbol, side, entry_type),
                    )
                else:
                    cur.execute(
                        "SELECT id FROM paper_trades WHERE symbol=%s AND side=%s AND status='OPEN' LIMIT 1",
                        (symbol, side),
                    )
                row = cur.fetchone()
                return row is not None

    def latest_trade(self, symbol: str, side: str, entry_type: str | None = None) -> dict[str, Any] | None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                if entry_type:
                    cur.execute(
                        """
                        SELECT id, symbol, side, entry_type, status, close_reason, opened_at, closed_at, updated_at
                        FROM paper_trades
                        WHERE symbol=%s AND side=%s AND entry_type=%s
                        ORDER BY updated_at DESC
                        LIMIT 1
                        """,
                        (symbol, side, entry_type),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, symbol, side, entry_type, status, close_reason, opened_at, closed_at, updated_at
                        FROM paper_trades
                        WHERE symbol=%s AND side=%s
                        ORDER BY updated_at DESC
                        LIMIT 1
                        """,
                        (symbol, side),
                    )
                return cur.fetchone()

    def list_open_trades(self) -> list[dict[str, Any]]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM paper_trades WHERE status='OPEN' ORDER BY opened_at DESC"
                )
                return list(cur.fetchall())

    def close_trade(
        self,
        trade_id: int,
        close_price: float,
        pnl: float,
        result: int,
        close_reason: str | None = None,
        commission_usdt: float | None = None,
    ) -> None:
        now = _now_vn()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE paper_trades
                    SET status='CLOSED', closed_at=%s, close_price=%s, close_reason=%s,
                        pnl=%s, commission_usdt=%s, result=%s, updated_at=%s
                    WHERE id=%s AND status='OPEN'
                    """,
                    (now, close_price, close_reason, pnl, commission_usdt, result, now, trade_id),
                )
                cur.execute(
                    "SELECT * FROM paper_trades WHERE id=%s LIMIT 1",
                    (trade_id,),
                )
                row = cur.fetchone()
                if row:
                    pnl_value = float(row.get("pnl") if row.get("pnl") is not None else pnl)
                    try:
                        margin_base = float(row.get("margin_usdt") or 0.0)
                    except Exception:
                        margin_base = 0.0
                    if margin_base <= 0:
                        try:
                            entry = float(row.get("entry_price") or 0.0)
                            qty = float(row.get("quantity") or 0.0)
                            lev = float(row.get("leverage") or 0.0)
                            if entry > 0 and qty > 0 and lev > 0:
                                margin_base = (entry * qty) / lev
                        except Exception:
                            margin_base = 0.0
                    pnl_pct = (pnl_value / margin_base) * 100.0 if margin_base > 0 else None
                    cur.execute(
                        """
                        INSERT INTO ml_feedback (
                            paper_trade_id, symbol, side, signal_win_probability,
                            effective_win_probability, mae_pct, mfe_pct,
                            feature_snapshot_json, feature_captured_at,
                            result, pnl, pnl_pct, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            row["id"],
                            row["symbol"],
                            row["side"],
                            row["signal_win_probability"],
                            row["effective_win_probability"],
                            row.get("mae_pct"),
                            row.get("mfe_pct"),
                            row.get("feature_snapshot_json"),
                            row.get("feature_captured_at"),
                            result,
                            pnl_value,
                            pnl_pct,
                            now,
                        ),
                    )

    def update_trade_excursions(self, trade_id: int, mae_pct: float, mfe_pct: float) -> None:
        now = _now_vn()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE paper_trades
                    SET
                        mae_pct = LEAST(COALESCE(mae_pct, %s), %s),
                        mfe_pct = GREATEST(COALESCE(mfe_pct, %s), %s),
                        updated_at=%s
                    WHERE id=%s AND status='OPEN'
                    """,
                    (mae_pct, mae_pct, mfe_pct, mfe_pct, now, trade_id),
                )

    def update_take_profit(self, trade_id: int, take_profit: float) -> None:
        now = _now_vn()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE paper_trades
                    SET take_profit=%s, updated_at=%s
                    WHERE id=%s AND status='OPEN'
                    """,
                    (take_profit, now, trade_id),
                )

    def update_stop_loss(self, trade_id: int, stop_loss: float) -> None:
        now = _now_vn()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE paper_trades
                    SET stop_loss=%s, updated_at=%s
                    WHERE id=%s AND status='OPEN'
                    """,
                    (stop_loss, now, trade_id),
                )

    def list_recent_trades(self, limit: int = 200) -> list[dict[str, Any]]:
        safe_limit = max(1, min(limit, 2000))
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT * FROM paper_trades ORDER BY opened_at DESC LIMIT {safe_limit}"
                )
                return list(cur.fetchall())

    def list_recent_trades_paged(self, page: int = 1, page_size: int = 50) -> tuple[list[dict[str, Any]], int]:
        safe_page = max(1, int(page))
        safe_page_size = max(1, min(int(page_size), 200))
        offset = (safe_page - 1) * safe_page_size
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS cnt FROM paper_trades")
                total_row = cur.fetchone() or {}
                total = int(total_row.get("cnt") or 0)
                cur.execute(
                    "SELECT * FROM paper_trades ORDER BY opened_at DESC LIMIT %s OFFSET %s",
                    (safe_page_size, offset),
                )
                rows = list(cur.fetchall())
        return rows, total

    def list_recent_closed_trades_by_side(self, side: str, limit: int = 80) -> list[dict[str, Any]]:
        side_key = str(side or "").upper()
        if side_key not in {"LONG", "SHORT"}:
            return []
        safe_limit = max(1, min(int(limit), 500))
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT id, side, close_reason, updated_at
                    FROM paper_trades
                    WHERE status='CLOSED' AND side=%s
                    ORDER BY id DESC
                    LIMIT {safe_limit}
                    """,
                    (side_key,),
                )
                return list(cur.fetchall() or [])

    def stats(self) -> dict[str, Any]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*) AS total_trades,
                        SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) AS open_trades,
                        SUM(CASE WHEN status='CLOSED' THEN 1 ELSE 0 END) AS closed_trades,
                        SUM(CASE WHEN status='CLOSED' AND result=1 THEN 1 ELSE 0 END) AS win_trades,
                        SUM(CASE WHEN status='CLOSED' AND result=0 THEN 1 ELSE 0 END) AS loss_trades,
                        SUM(CASE WHEN status='CLOSED' AND entry_type='MARKET' THEN 1 ELSE 0 END) AS market_closed_trades,
                        SUM(CASE WHEN status='CLOSED' AND entry_type='MARKET' AND result=1 THEN 1 ELSE 0 END) AS market_win_trades,
                        SUM(CASE WHEN status='CLOSED' AND entry_type='MARKET' AND result=0 THEN 1 ELSE 0 END) AS market_loss_trades,
                        COALESCE(SUM(CASE WHEN status='CLOSED' AND entry_type='MARKET' THEN pnl ELSE 0 END), 0) AS market_total_pnl,
                        COALESCE(AVG(CASE WHEN status='CLOSED' AND entry_type='MARKET' THEN pnl ELSE NULL END), 0) AS market_avg_pnl,
                        COALESCE(
                            SUM(
                                CASE
                                    WHEN status='CLOSED' AND entry_type='MARKET' AND pnl IS NOT NULL THEN
                                        CASE
                                            WHEN COALESCE(margin_usdt, (entry_price * quantity) / NULLIF(leverage, 0)) > 0
                                                THEN (pnl / COALESCE(margin_usdt, (entry_price * quantity) / NULLIF(leverage, 0))) * 100
                                            ELSE 0
                                        END
                                    ELSE 0
                                END
                            ),
                            0
                        ) AS market_total_pnl_pct,
                        COALESCE(
                            AVG(
                                CASE
                                    WHEN status='CLOSED' AND entry_type='MARKET' AND pnl IS NOT NULL THEN
                                        CASE
                                            WHEN COALESCE(margin_usdt, (entry_price * quantity) / NULLIF(leverage, 0)) > 0
                                                THEN (pnl / COALESCE(margin_usdt, (entry_price * quantity) / NULLIF(leverage, 0))) * 100
                                            ELSE NULL
                                        END
                                    ELSE NULL
                                END
                            ),
                            0
                        ) AS market_avg_pnl_pct,
                        SUM(CASE WHEN status='CLOSED' AND entry_type='LIMIT' THEN 1 ELSE 0 END) AS limit_closed_trades,
                        SUM(CASE WHEN status='CLOSED' AND entry_type='LIMIT' AND result=1 THEN 1 ELSE 0 END) AS limit_win_trades,
                        SUM(CASE WHEN status='CLOSED' AND entry_type='LIMIT' AND result=0 THEN 1 ELSE 0 END) AS limit_loss_trades,
                        COALESCE(SUM(CASE WHEN status='CLOSED' AND entry_type='LIMIT' THEN pnl ELSE 0 END), 0) AS limit_total_pnl,
                        COALESCE(AVG(CASE WHEN status='CLOSED' AND entry_type='LIMIT' THEN pnl ELSE NULL END), 0) AS limit_avg_pnl,
                        COALESCE(
                            SUM(
                                CASE
                                    WHEN status='CLOSED' AND entry_type='LIMIT' AND pnl IS NOT NULL THEN
                                        CASE
                                            WHEN COALESCE(margin_usdt, (entry_price * quantity) / NULLIF(leverage, 0)) > 0
                                                THEN (pnl / COALESCE(margin_usdt, (entry_price * quantity) / NULLIF(leverage, 0))) * 100
                                            ELSE 0
                                        END
                                    ELSE 0
                                END
                            ),
                            0
                        ) AS limit_total_pnl_pct,
                        COALESCE(
                            AVG(
                                CASE
                                    WHEN status='CLOSED' AND entry_type='LIMIT' AND pnl IS NOT NULL THEN
                                        CASE
                                            WHEN COALESCE(margin_usdt, (entry_price * quantity) / NULLIF(leverage, 0)) > 0
                                                THEN (pnl / COALESCE(margin_usdt, (entry_price * quantity) / NULLIF(leverage, 0))) * 100
                                            ELSE NULL
                                        END
                                    ELSE NULL
                                END
                            ),
                            0
                        ) AS limit_avg_pnl_pct,
                        COALESCE(SUM(CASE WHEN status='CLOSED' THEN pnl ELSE 0 END), 0) AS total_pnl,
                        COALESCE(AVG(CASE WHEN status='CLOSED' THEN pnl ELSE NULL END), 0) AS avg_pnl,
                        COALESCE(
                            SUM(
                                CASE
                                    WHEN status='CLOSED' AND pnl IS NOT NULL THEN
                                        CASE
                                            WHEN COALESCE(margin_usdt, (entry_price * quantity) / NULLIF(leverage, 0)) > 0
                                                THEN (pnl / COALESCE(margin_usdt, (entry_price * quantity) / NULLIF(leverage, 0))) * 100
                                            ELSE 0
                                        END
                                    ELSE 0
                                END
                            ),
                            0
                        ) AS total_pnl_pct,
                        COALESCE(
                            AVG(
                                CASE
                                    WHEN status='CLOSED' AND pnl IS NOT NULL THEN
                                        CASE
                                            WHEN COALESCE(margin_usdt, (entry_price * quantity) / NULLIF(leverage, 0)) > 0
                                                THEN (pnl / COALESCE(margin_usdt, (entry_price * quantity) / NULLIF(leverage, 0))) * 100
                                            ELSE NULL
                                        END
                                    ELSE NULL
                                END
                            ),
                            0
                        ) AS avg_pnl_pct
                    FROM paper_trades
                    """
                )
                row = cur.fetchone() or {}

        closed = int(row.get("closed_trades") or 0)
        wins = int(row.get("win_trades") or 0)
        market_closed = int(row.get("market_closed_trades") or 0)
        market_wins = int(row.get("market_win_trades") or 0)
        limit_closed = int(row.get("limit_closed_trades") or 0)
        limit_wins = int(row.get("limit_win_trades") or 0)
        win_rate = (wins / closed) if closed > 0 else 0.0
        market_win_rate = (market_wins / market_closed) if market_closed > 0 else 0.0
        limit_win_rate = (limit_wins / limit_closed) if limit_closed > 0 else 0.0

        return {
            "total_trades": int(row.get("total_trades") or 0),
            "open_trades": int(row.get("open_trades") or 0),
            "closed_trades": closed,
            "win_trades": wins,
            "loss_trades": int(row.get("loss_trades") or 0),
            "win_rate": float(win_rate),
            "total_pnl": float(row.get("total_pnl") or 0.0),
            "avg_pnl": float(row.get("avg_pnl") or 0.0),
            "total_pnl_pct": float(row.get("total_pnl_pct") or 0.0),
            "avg_pnl_pct": float(row.get("avg_pnl_pct") or 0.0),
            "market_closed_trades": market_closed,
            "market_win_trades": market_wins,
            "market_win_rate": float(market_win_rate),
            "market_loss_trades": int(row.get("market_loss_trades") or 0),
            "market_total_pnl": float(row.get("market_total_pnl") or 0.0),
            "market_avg_pnl": float(row.get("market_avg_pnl") or 0.0),
            "market_total_pnl_pct": float(row.get("market_total_pnl_pct") or 0.0),
            "market_avg_pnl_pct": float(row.get("market_avg_pnl_pct") or 0.0),
            "limit_closed_trades": limit_closed,
            "limit_win_trades": limit_wins,
            "limit_win_rate": float(limit_win_rate),
            "limit_loss_trades": int(row.get("limit_loss_trades") or 0),
            "limit_total_pnl": float(row.get("limit_total_pnl") or 0.0),
            "limit_avg_pnl": float(row.get("limit_avg_pnl") or 0.0),
            "limit_total_pnl_pct": float(row.get("limit_total_pnl_pct") or 0.0),
            "limit_avg_pnl_pct": float(row.get("limit_avg_pnl_pct") or 0.0),
        }

    def symbol_accuracy(self, symbol: str, lookback: int = 200) -> float | None:
        safe_lookback = max(20, min(lookback, 2000))
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT result
                    FROM ml_feedback f
                    WHERE symbol=%s
                    ORDER BY f.created_at DESC
                    LIMIT {safe_lookback}
                    """,
                    (symbol,),
                )
                rows = cur.fetchall()

        if not rows:
            return None
        wins = sum(1 for row in rows if int(row.get("result", 0)) == 1)
        return wins / len(rows)

    def list_feedback(self, limit: int = 1000) -> list[dict[str, Any]]:
        safe_limit = max(10, min(limit, 5000))
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"""
                    SELECT
                        f.symbol,
                        f.side,
                        f.result,
                        p.entry_type,
                        p.btc_following,
                        p.close_reason,
                        p.opened_at,
                        p.closed_at,
                        f.mae_pct,
                        f.mfe_pct,
                        f.created_at,
                        f.feature_snapshot_json,
                        f.feature_captured_at,
                        f.pnl,
                        f.pnl_pct
                    FROM ml_feedback f
                    LEFT JOIN paper_trades p ON p.id = f.paper_trade_id
                    ORDER BY f.created_at DESC
                    LIMIT {safe_limit}
                    """
                )
                return list(cur.fetchall())

    @staticmethod
    def _coerce_profile_datetime(value: object) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, str):
            text = value.strip()
            if not text:
                return None
            normalized = text.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(normalized)
            except Exception:
                for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
                    try:
                        dt = datetime.strptime(text, fmt)
                        break
                    except Exception:
                        continue
                else:
                    return None
        else:
            return None
        if dt.tzinfo is not None:
            return dt.astimezone(_VN_TZ).replace(tzinfo=None)
        return dt

    @classmethod
    def _coerce_hour_vn(cls, value: object) -> int | None:
        dt = cls._coerce_profile_datetime(value)
        if dt is None:
            return None
        return int(dt.hour)

    @classmethod
    def _coerce_weekday_vn(cls, value: object) -> int | None:
        dt = cls._coerce_profile_datetime(value)
        if dt is None:
            return None
        # Monday=0 ... Sunday=6
        return int(dt.weekday())

    @staticmethod
    def _coerce_bool(value: object) -> bool | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            try:
                return int(value) != 0
            except Exception:
                return None
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "y", "on"}:
            return True
        if text in {"0", "false", "no", "n", "off"}:
            return False
        return None

    @staticmethod
    def _expand_profile_scopes(base_scope: str, weekday_vn: int | None, trend_key: str) -> list[str]:
        scopes: list[str] = [str(base_scope).upper()]
        if weekday_vn is not None:
            scopes.append(f"{base_scope}|DOW:{int(weekday_vn)}".upper())
        trend = str(trend_key or "ALL").upper()
        if trend in {"LONG", "SHORT", "NEUTRAL"}:
            scopes.append(f"{base_scope}|TREND:{trend}".upper())
            if weekday_vn is not None:
                scopes.append(f"{base_scope}|DOW:{int(weekday_vn)}|TREND:{trend}".upper())
        # Keep order stable but remove duplicates.
        out: list[str] = []
        seen: set[str] = set()
        for item in scopes:
            if item in seen:
                continue
            seen.add(item)
            out.append(item)
        return out

    @classmethod
    def _infer_trade_trend_key(
        cls,
        *,
        symbol: str,
        side: str,
        pnl: float,
        btc_following: object,
    ) -> str:
        follows = cls._coerce_bool(btc_following)
        symbol_key = str(symbol or "").upper().replace(":USDT", "")
        if follows is None and symbol_key.startswith("BTC/USDT"):
            follows = True
        if not follows:
            return "NEUTRAL"
        side_key = str(side or "").upper()
        if side_key not in {"LONG", "SHORT"}:
            return "NEUTRAL"
        if abs(float(pnl)) <= 1e-12:
            return "NEUTRAL"
        if side_key == "LONG":
            return "LONG" if pnl > 0 else "SHORT"
        return "SHORT" if pnl > 0 else "LONG"

    def refresh_hourly_profiles(self, lookback_days: int = 60) -> int:
        safe_days = max(1, min(int(lookback_days), 3650))
        from_dt = _now_vn() - timedelta(days=safe_days)
        now = _now_vn()
        aggregates: dict[tuple[str, str, int], dict[str, float]] = {}

        def touch(scope: str, side_key: str, hour_vn: int, pnl: float) -> None:
            key = (scope, side_key, hour_vn)
            state = aggregates.get(key)
            if state is None:
                state = {"total": 0.0, "wins": 0.0, "losses": 0.0, "breakeven": 0.0, "net_pnl": 0.0}
                aggregates[key] = state
            state["total"] += 1.0
            state["net_pnl"] += float(pnl)
            if pnl > 0:
                state["wins"] += 1.0
            elif pnl < 0:
                state["losses"] += 1.0
            else:
                state["breakeven"] += 1.0

        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.COLUMNS
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='paper_trades' AND COLUMN_NAME='opened_at'
                    """,
                    (self.database,),
                )
                trade_time_col = "opened_at" if int((cur.fetchone() or {}).get("cnt") or 0) > 0 else "updated_at"
                cur.execute(
                    f"""
                    SELECT {trade_time_col} AS profile_time_at, symbol, side, btc_following, entry_type, pnl
                    FROM paper_trades
                    WHERE status='CLOSED' AND pnl IS NOT NULL AND {trade_time_col} >= %s
                    """,
                    (from_dt,),
                )
                trade_rows = cur.fetchall() or []
                cur.execute(
                    """
                    SELECT COUNT(*) AS cnt
                    FROM information_schema.TABLES
                    WHERE TABLE_SCHEMA=%s AND TABLE_NAME='paper_trades_liq'
                    """,
                    (self.database,),
                )
                liq_exists = int((cur.fetchone() or {}).get("cnt") or 0) > 0
                liq_rows: list[dict[str, Any]] = []
                if liq_exists:
                    cur.execute(
                        """
                        SELECT COUNT(*) AS cnt
                        FROM information_schema.COLUMNS
                        WHERE TABLE_SCHEMA=%s AND TABLE_NAME='paper_trades_liq' AND COLUMN_NAME='opened_at'
                        """,
                        (self.database,),
                    )
                    liq_time_col = "opened_at" if int((cur.fetchone() or {}).get("cnt") or 0) > 0 else "updated_at"
                    cur.execute(
                        f"""
                        SELECT {liq_time_col} AS profile_time_at, side, pnl
                        FROM paper_trades_liq
                        WHERE status='CLOSED' AND pnl IS NOT NULL AND {liq_time_col} >= %s
                        """,
                        (from_dt,),
                    )
                    liq_rows = cur.fetchall() or []

                for row in trade_rows:
                    hour_vn = self._coerce_hour_vn(row.get("profile_time_at"))
                    weekday_vn = self._coerce_weekday_vn(row.get("profile_time_at"))
                    if hour_vn is None:
                        continue
                    pnl = float(row.get("pnl") or 0.0)
                    side_key = str(row.get("side") or "UNKNOWN").upper()
                    entry_scope = f"ENTRY:{str(row.get('entry_type') or 'UNKNOWN').upper()}"
                    trend_key = self._infer_trade_trend_key(
                        symbol=str(row.get("symbol") or ""),
                        side=side_key,
                        pnl=pnl,
                        btc_following=row.get("btc_following"),
                    )

                    all_scopes = self._expand_profile_scopes("ALL", weekday_vn, trend_key)
                    entry_scopes = self._expand_profile_scopes(entry_scope, weekday_vn, trend_key)
                    for scope in all_scopes:
                        touch(scope, "ALL", hour_vn, pnl)
                        touch(scope, side_key, hour_vn, pnl)
                    for scope in entry_scopes:
                        touch(scope, "ALL", hour_vn, pnl)
                        touch(scope, side_key, hour_vn, pnl)

                for row in liq_rows:
                    hour_vn = self._coerce_hour_vn(row.get("profile_time_at"))
                    weekday_vn = self._coerce_weekday_vn(row.get("profile_time_at"))
                    if hour_vn is None:
                        continue
                    pnl = float(row.get("pnl") or 0.0)
                    side_key = str(row.get("side") or "UNKNOWN").upper()

                    all_scopes = self._expand_profile_scopes("ALL", weekday_vn, "ALL")
                    liq_scopes = self._expand_profile_scopes("LIQ_TABLE", weekday_vn, "ALL")
                    for scope in all_scopes:
                        touch(scope, "ALL", hour_vn, pnl)
                        touch(scope, side_key, hour_vn, pnl)
                    for scope in liq_scopes:
                        touch(scope, "ALL", hour_vn, pnl)
                        touch(scope, side_key, hour_vn, pnl)

                rows_to_upsert: list[tuple[Any, ...]] = []
                for (scope, side_key, hour_vn), state in aggregates.items():
                    total = int(state["total"])
                    wins = int(state["wins"])
                    losses = int(state["losses"])
                    breakeven = int(state["breakeven"])
                    net_pnl = float(state["net_pnl"])
                    avg_pnl = net_pnl / total if total > 0 else 0.0
                    win_rate_pct = (wins / total) * 100.0 if total > 0 else 0.0
                    loss_rate_pct = (losses / total) * 100.0 if total > 0 else 0.0
                    rows_to_upsert.append(
                        (
                            scope,
                            side_key,
                            int(hour_vn),
                            total,
                            wins,
                            losses,
                            breakeven,
                            win_rate_pct,
                            loss_rate_pct,
                            net_pnl,
                            avg_pnl,
                            now,
                        )
                    )

                cur.execute("DELETE FROM trade_hourly_profiles")
                if rows_to_upsert:
                    cur.executemany(
                        """
                        INSERT INTO trade_hourly_profiles (
                            scope, side_key, hour_vn, total_orders, wins, losses, breakeven,
                            win_rate_pct, loss_rate_pct, net_pnl, avg_pnl, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        rows_to_upsert,
                    )
                return len(rows_to_upsert)

    def list_hourly_profiles(self, scope: str | None = None, side_key: str | None = None) -> list[dict[str, Any]]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                if scope is not None and side_key is not None:
                    cur.execute(
                        """
                        SELECT scope, side_key, hour_vn, total_orders, wins, losses, breakeven,
                               win_rate_pct, loss_rate_pct, net_pnl, avg_pnl, updated_at
                        FROM trade_hourly_profiles
                        WHERE scope=%s AND side_key=%s
                        ORDER BY hour_vn ASC
                        """,
                        (scope, side_key),
                    )
                elif scope is not None:
                    cur.execute(
                        """
                        SELECT scope, side_key, hour_vn, total_orders, wins, losses, breakeven,
                               win_rate_pct, loss_rate_pct, net_pnl, avg_pnl, updated_at
                        FROM trade_hourly_profiles
                        WHERE scope=%s
                        ORDER BY side_key ASC, hour_vn ASC
                        """,
                        (scope,),
                    )
                elif side_key is not None:
                    cur.execute(
                        """
                        SELECT scope, side_key, hour_vn, total_orders, wins, losses, breakeven,
                               win_rate_pct, loss_rate_pct, net_pnl, avg_pnl, updated_at
                        FROM trade_hourly_profiles
                        WHERE side_key=%s
                        ORDER BY scope ASC, hour_vn ASC
                        """,
                        (side_key,),
                    )
                else:
                    cur.execute(
                        """
                        SELECT scope, side_key, hour_vn, total_orders, wins, losses, breakeven,
                               win_rate_pct, loss_rate_pct, net_pnl, avg_pnl, updated_at
                        FROM trade_hourly_profiles
                        ORDER BY scope ASC, side_key ASC, hour_vn ASC
                        """
                    )
                return list(cur.fetchall() or [])

    def hourly_profile_map(self) -> dict[str, dict[str, dict[int, dict[str, Any]]]]:
        rows = self.list_hourly_profiles()
        out: dict[str, dict[str, dict[int, dict[str, Any]]]] = {}
        for row in rows:
            scope = str(row.get("scope") or "ALL")
            side_key = str(row.get("side_key") or "ALL")
            hour_vn = int(row.get("hour_vn") or 0)
            out.setdefault(scope, {}).setdefault(side_key, {})[hour_vn] = {
                "total_orders": int(row.get("total_orders") or 0),
                "wins": int(row.get("wins") or 0),
                "losses": int(row.get("losses") or 0),
                "breakeven": int(row.get("breakeven") or 0),
                "win_rate_pct": float(row.get("win_rate_pct") or 0.0),
                "loss_rate_pct": float(row.get("loss_rate_pct") or 0.0),
                "net_pnl": float(row.get("net_pnl") or 0.0),
                "avg_pnl": float(row.get("avg_pnl") or 0.0),
            }
        return out

    def daily_summary(self, days: int = 30) -> list[dict[str, Any]]:
        safe_days = max(1, min(days, 365))
        now = _now_vn()
        from_dt = now - timedelta(days=safe_days - 1)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        DATE(closed_at) AS trade_date,
                        COUNT(*) AS total_trades,
                        SUM(CASE WHEN COALESCE(pnl, 0) > 0 THEN 1 ELSE 0 END) AS win_trades,
                        SUM(CASE WHEN COALESCE(pnl, 0) <= 0 THEN 1 ELSE 0 END) AS loss_trades,
                        COALESCE(SUM(pnl), 0) AS total_pnl,
                        COALESCE(AVG(pnl), 0) AS avg_pnl
                    FROM paper_trades
                    WHERE status='CLOSED' AND closed_at >= %s
                    GROUP BY DATE(closed_at)
                    ORDER BY trade_date DESC
                    """,
                    (from_dt,),
                )
                rows = cur.fetchall()

        out: list[dict[str, Any]] = []
        for row in rows:
            total = int(row.get("total_trades") or 0)
            wins = int(row.get("win_trades") or 0)
            win_rate = (wins / total) if total > 0 else 0.0
            trade_date = row.get("trade_date")
            out.append(
                {
                    "trade_date": str(trade_date),
                    "total_trades": total,
                    "win_trades": wins,
                    "loss_trades": int(row.get("loss_trades") or 0),
                    "win_rate": float(win_rate),
                    "total_pnl": float(row.get("total_pnl") or 0.0),
                    "avg_pnl": float(row.get("avg_pnl") or 0.0),
                }
            )
        return out
