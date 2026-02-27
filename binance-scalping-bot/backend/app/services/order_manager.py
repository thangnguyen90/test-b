from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from app.models.orders import Order, OrderCreate, OrderStatus


class OrderManager:
    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._ensure_db_dir()
        self._init_schema()

    def _ensure_db_dir(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._get_conn() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    quantity REAL NOT NULL,
                    leverage INTEGER NOT NULL,
                    predicted_entry_price REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    win_probability REAL NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    opened_at TEXT,
                    closed_at TEXT,
                    close_price REAL,
                    pnl REAL,
                    expiration_time TEXT
                )
                """
            )

    def create_pending_order(self, order: OrderCreate) -> Order:
        now = datetime.now(timezone.utc)
        expiration = order.expiration_time or (now + timedelta(minutes=20))

        with self._get_conn() as conn:
            cursor = conn.execute(
                """
                INSERT INTO orders (
                    symbol, side, quantity, leverage,
                    predicted_entry_price, stop_loss, take_profit, win_probability,
                    status, created_at, updated_at, expiration_time
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order.symbol,
                    order.side,
                    order.quantity,
                    order.leverage,
                    order.predicted_entry_price,
                    order.stop_loss,
                    order.take_profit,
                    order.win_probability,
                    OrderStatus.PENDING.value,
                    now.isoformat(),
                    now.isoformat(),
                    expiration.isoformat(),
                ),
            )
            order_id = cursor.lastrowid

        return self.get_order_by_id(order_id)

    def get_order_by_id(self, order_id: int) -> Order:
        with self._get_conn() as conn:
            row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()

        if row is None:
            raise ValueError(f"Order {order_id} not found")

        return self._row_to_order(row)

    def list_orders_by_status(self, status: OrderStatus) -> list[Order]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM orders WHERE status = ? ORDER BY created_at DESC", (status.value,)
            ).fetchall()

        return self._rows_to_orders(rows)

    def list_pending(self) -> list[Order]:
        return self.list_orders_by_status(OrderStatus.PENDING)

    def list_open(self) -> list[Order]:
        return self.list_orders_by_status(OrderStatus.OPEN)

    def list_closed(self) -> list[Order]:
        with self._get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM orders WHERE status IN (?, ?) ORDER BY updated_at DESC",
                (OrderStatus.CLOSED.value, OrderStatus.CANCELED.value),
            ).fetchall()

        return self._rows_to_orders(rows)

    def _rows_to_orders(self, rows: Iterable[sqlite3.Row]) -> list[Order]:
        return [self._row_to_order(row) for row in rows]

    def _row_to_order(self, row: sqlite3.Row) -> Order:
        return Order(
            id=row["id"],
            symbol=row["symbol"],
            side=row["side"],
            quantity=row["quantity"],
            leverage=row["leverage"],
            predicted_entry_price=row["predicted_entry_price"],
            stop_loss=row["stop_loss"],
            take_profit=row["take_profit"],
            win_probability=row["win_probability"],
            status=OrderStatus(row["status"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            opened_at=datetime.fromisoformat(row["opened_at"]) if row["opened_at"] else None,
            closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
            close_price=row["close_price"],
            pnl=row["pnl"],
            expiration_time=(
                datetime.fromisoformat(row["expiration_time"]) if row["expiration_time"] else None
            ),
        )
