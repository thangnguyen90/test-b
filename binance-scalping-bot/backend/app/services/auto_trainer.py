from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.ml_predictor import MLPredictor

logger = logging.getLogger(__name__)


class AutoTrainer:
    def __init__(
        self,
        predictor: MLPredictor,
        *,
        enabled: bool,
        interval_minutes: int,
        startup_delay_sec: int,
        limit: int,
        horizon: int,
        rr_ratio: float,
        symbols: list[str],
    ) -> None:
        self.predictor = predictor
        self.enabled = enabled
        self.interval_minutes = max(5, interval_minutes)
        self.startup_delay_sec = max(0, startup_delay_sec)
        self.limit = limit
        self.horizon = horizon
        self.rr_ratio = rr_ratio
        self.symbols = symbols

        self._task: asyncio.Task[None] | None = None
        self._last_run_started_at: datetime | None = None
        self._last_run_finished_at: datetime | None = None
        self._next_run_at: datetime | None = None
        self._last_result: str | None = None
        self._last_error: str | None = None

    async def start(self) -> None:
        if not self.enabled:
            logger.info("Auto trainer disabled")
            return
        if self._task is not None and not self._task.done():
            return

        self._task = asyncio.create_task(self._run_loop(), name="auto-trainer-loop")
        logger.info(
            "Auto trainer started: interval=%sm, startup_delay=%ss",
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

    def status(self) -> dict[str, Any]:
        return {
            "auto_train_enabled": self.enabled,
            "auto_train_running": bool(self._task and not self._task.done()),
            "auto_train_interval_minutes": self.interval_minutes,
            "auto_train_next_run_at": self._next_run_at,
            "auto_train_last_run_started_at": self._last_run_started_at,
            "auto_train_last_run_finished_at": self._last_run_finished_at,
            "auto_train_last_result": self._last_result,
            "auto_train_last_error": self._last_error,
        }

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
            result = await asyncio.to_thread(
                self.predictor.train,
                limit=self.limit,
                horizon=self.horizon,
                rr_ratio=self.rr_ratio,
                symbols=self.symbols,
                trigger="auto",
            )
            self._last_result = "SUCCESS" if result.get("trained") else "SKIPPED"
            logger.info("Auto train finished: result=%s payload=%s", self._last_result, result)
        except Exception as exc:
            self._last_result = "FAILED"
            self._last_error = str(exc)
            logger.exception("Auto train failed: %s", exc)
        finally:
            self._last_run_finished_at = datetime.now(timezone.utc)
