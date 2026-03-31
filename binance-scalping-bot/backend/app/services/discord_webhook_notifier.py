from __future__ import annotations

import json
import logging
from typing import Any
from urllib import error, request


logger = logging.getLogger(__name__)


class DiscordWebhookNotifier:
    def __init__(
        self,
        *,
        webhook_url: str = "",
        enabled: bool = False,
        username: str = "ML Candles BG Bot",
    ) -> None:
        self.webhook_url = str(webhook_url or "").strip()
        self.enabled = bool(enabled) and bool(self.webhook_url)
        self.username = str(username or "ML Candles BG Bot").strip() or "ML Candles BG Bot"

    @property
    def is_enabled(self) -> bool:
        return self.enabled and bool(self.webhook_url)

    def send(self, *, content: str | None = None, embeds: list[dict[str, Any]] | None = None) -> bool:
        if not self.is_enabled:
            return False
        payload: dict[str, Any] = {"username": self.username}
        if content:
            payload["content"] = str(content)
        if embeds:
            payload["embeds"] = embeds
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.webhook_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=8.0) as response:
                return 200 <= int(getattr(response, "status", 0)) < 300
        except error.HTTPError as exc:
            logger.warning("Discord webhook HTTP error: %s", exc.code)
        except Exception as exc:
            logger.warning("Discord webhook send failed: %s", exc)
        return False
