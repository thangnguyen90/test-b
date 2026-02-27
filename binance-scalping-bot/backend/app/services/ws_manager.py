from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from random import uniform

from fastapi import WebSocket

from app.core.config import settings


class WSManager:
    def __init__(self) -> None:
        self.connections: set[WebSocket] = set()
        self._task: asyncio.Task | None = None
        self._last_price = 100.0

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.connections.discard(websocket)

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._broadcast_loop())

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _broadcast_loop(self) -> None:
        while True:
            await asyncio.sleep(settings.websocket_ping_interval_sec)
            if not self.connections:
                continue

            self._last_price = max(1.0, self._last_price + uniform(-0.3, 0.3))
            payload = {
                "type": "ticker",
                "symbol": "BTC/USDT",
                "price": round(self._last_price, 2),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            stale_connections = []
            for ws in self.connections:
                try:
                    await ws.send_json(payload)
                except Exception:
                    stale_connections.append(ws)

            for ws in stale_connections:
                self.disconnect(ws)
