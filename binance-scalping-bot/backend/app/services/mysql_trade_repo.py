from __future__ import annotations

from datetime import datetime, timedelta, timezone
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
                        entry_type VARCHAR(12) NOT NULL DEFAULT 'LIMIT',
                        signal_win_probability DOUBLE NOT NULL,
                        effective_win_probability DOUBLE NOT NULL,
                        entry_price DOUBLE NOT NULL,
                        take_profit DOUBLE NOT NULL,
                        stop_loss DOUBLE NOT NULL,
                        quantity DOUBLE NOT NULL,
                        margin_usdt DOUBLE NULL,
                        leverage INT NOT NULL,
                        status VARCHAR(20) NOT NULL,
                        opened_at DATETIME(6) NOT NULL,
                        closed_at DATETIME(6) NULL,
                        close_price DOUBLE NULL,
                        close_reason VARCHAR(32) NULL,
                        pnl DOUBLE NULL,
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
                    CREATE TABLE IF NOT EXISTS ml_feedback (
                        id BIGINT PRIMARY KEY AUTO_INCREMENT,
                        paper_trade_id BIGINT NOT NULL,
                        symbol VARCHAR(64) NOT NULL,
                        side VARCHAR(10) NOT NULL,
                        signal_win_probability DOUBLE NOT NULL,
                        effective_win_probability DOUBLE NOT NULL,
                        result TINYINT NOT NULL,
                        pnl DOUBLE NOT NULL,
                        created_at DATETIME(6) NOT NULL,
                        INDEX idx_symbol_created(symbol, created_at),
                        INDEX idx_result_created(result, created_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                    """
                )

    def create_open_trade(self, payload: dict[str, Any]) -> int:
        now = _now_vn()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO paper_trades (
                        symbol, side, entry_type, signal_win_probability, effective_win_probability,
                        entry_price, take_profit, stop_loss, quantity, margin_usdt, leverage,
                        status, opened_at, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'OPEN', %s, %s, %s)
                    """,
                    (
                        payload["symbol"],
                        payload["side"],
                        payload.get("entry_type", "LIMIT"),
                        payload["signal_win_probability"],
                        payload["effective_win_probability"],
                        payload["entry_price"],
                        payload["take_profit"],
                        payload["stop_loss"],
                        payload["quantity"],
                        payload.get("margin_usdt"),
                        payload["leverage"],
                        now,
                        now,
                        now,
                    ),
                )
                return int(cur.lastrowid)

    def has_open_trade(self, symbol: str, side: str) -> bool:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM paper_trades WHERE symbol=%s AND side=%s AND status='OPEN' LIMIT 1",
                    (symbol, side),
                )
                row = cur.fetchone()
                return row is not None

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
    ) -> None:
        now = _now_vn()
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE paper_trades
                    SET status='CLOSED', closed_at=%s, close_price=%s, close_reason=%s, pnl=%s, result=%s, updated_at=%s
                    WHERE id=%s AND status='OPEN'
                    """,
                    (now, close_price, close_reason, pnl, result, now, trade_id),
                )
                cur.execute(
                    "SELECT * FROM paper_trades WHERE id=%s LIMIT 1",
                    (trade_id,),
                )
                row = cur.fetchone()
                if row:
                    cur.execute(
                        """
                        INSERT INTO ml_feedback (
                            paper_trade_id, symbol, side, signal_win_probability,
                            effective_win_probability, result, pnl, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            row["id"],
                            row["symbol"],
                            row["side"],
                            row["signal_win_probability"],
                            row["effective_win_probability"],
                            result,
                            pnl,
                            now,
                        ),
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
                    FROM ml_feedback
                    WHERE symbol=%s
                    ORDER BY created_at DESC
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
                    SELECT symbol, side, result, created_at
                    FROM ml_feedback
                    ORDER BY created_at DESC
                    LIMIT {safe_limit}
                    """
                )
                return list(cur.fetchall())

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
