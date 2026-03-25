from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.signal_candle_pattern_service import signal_candle_pattern_service

logger = logging.getLogger(__name__)


class CandlePatternRefresher:
    def __init__(
        self,
        *,
        enabled: bool,
        interval_minutes: int,
        startup_delay_sec: int,
    ) -> None:
        self.enabled = enabled
        self.interval_minutes = max(1, int(interval_minutes))
        self.startup_delay_sec = max(0, int(startup_delay_sec))

        self._task: asyncio.Task[None] | None = None
        self._last_run_started_at: datetime | None = None
        self._last_run_finished_at: datetime | None = None
        self._next_run_at: datetime | None = None
        self._last_result: str | None = None
        self._last_error: str | None = None

    def status(self) -> dict[str, Any]:
        return {
            "pattern_refresh_enabled": self.enabled,
            "pattern_refresh_running": bool(self._task and not self._task.done()),
            "pattern_refresh_interval_minutes": self.interval_minutes,
            "pattern_refresh_next_run_at": self._next_run_at,
            "pattern_refresh_last_run_started_at": self._last_run_started_at,
            "pattern_refresh_last_run_finished_at": self._last_run_finished_at,
            "pattern_refresh_last_result": self._last_result,
            "pattern_refresh_last_error": self._last_error,
        }

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Candle pattern refresher disabled")
            return
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run_loop(), name="candle-pattern-refresher-loop")
        logger.info(
            "Candle pattern refresher started: interval=%sm, startup_delay=%ss",
            self.interval_minutes,
            self.startup_delay_sec,
        )

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _run_loop(self) -> None:
        if self.startup_delay_sec > 0:
            self._next_run_at = datetime.now(timezone.utc) + timedelta(seconds=self.startup_delay_sec)
            await asyncio.sleep(self.startup_delay_sec)

        while True:
            await self._run_once()
            self._next_run_at = datetime.now(timezone.utc) + timedelta(minutes=self.interval_minutes)
            await asyncio.sleep(self.interval_minutes * 60)

    async def _run_once(self) -> None:
        self._last_run_started_at = datetime.now(timezone.utc)
        self._last_result = "RUNNING"
        self._last_error = None
        try:
            await asyncio.to_thread(signal_candle_pattern_service.get_catalog, force_refresh=True)
            self._last_result = "SUCCESS"
        except Exception as exc:
            self._last_result = "FAILED"
            self._last_error = str(exc)
            logger.exception("Candle pattern refresh failed: %s", exc)
        finally:
            self._last_run_finished_at = datetime.now(timezone.utc)
