"""Telegram alert channel.

Wraps the Bot API ``sendMessage`` endpoint. The channel is *optional*: if the
bot token or chat id are not configured it disables itself, logs a single
notice and every :meth:`send` returns ``False`` without raising. Transient
network errors are retried with exponential backoff.
"""
from __future__ import annotations

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

_API_BASE = "https://api.telegram.org"
_TIMEOUT_SECONDS = 10


class TelegramAlerter:
    """Sends plain-text alerts to a Telegram chat via the Bot API."""

    def __init__(
        self,
        bot_token: str | None = None,
        chat_id: str | None = None,
    ) -> None:
        self._token = (bot_token if bot_token is not None else settings.telegram_bot_token) or ""
        self._chat_id = (chat_id if chat_id is not None else settings.telegram_chat_id) or ""
        self._disabled_logged = False

    @property
    def enabled(self) -> bool:
        """True when both a bot token and a chat id are configured."""
        return bool(self._token and self._chat_id)

    def _log_disabled_once(self) -> None:
        if not self._disabled_logged:
            logger.info("Telegram alerts disabled (missing bot token / chat id).")
            self._disabled_logged = True

    def send(self, text: str) -> bool:
        """Send ``text`` to the configured chat. Returns success as a bool.

        Never raises: configuration and network failures are logged and result
        in ``False``.
        """
        if not self.enabled:
            self._log_disabled_once()
            return False
        try:
            return self._send_with_retry(text)
        except Exception as exc:  # noqa: BLE001 - alerts must never crash callers
            logger.warning("Telegram send failed after retries: %s", exc)
            return False

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        retry=retry_if_exception_type(requests.RequestException),
    )
    def _send_with_retry(self, text: str) -> bool:
        url = f"{_API_BASE}/bot{self._token}/sendMessage"
        resp = requests.post(
            url,
            json={"chat_id": self._chat_id, "text": text, "disable_web_page_preview": True},
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        payload = resp.json()
        ok = bool(payload.get("ok", False))
        if not ok:
            logger.warning("Telegram API returned not-ok: %s", payload.get("description"))
        return ok


__all__ = ["TelegramAlerter"]
