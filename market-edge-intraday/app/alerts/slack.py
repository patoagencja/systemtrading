"""Slack alert channel.

Posts plain-text alerts to an incoming-webhook URL. Optional: if the webhook
URL is not configured the channel disables itself, logs a single notice and
every :meth:`send` returns ``False`` without raising.
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

_TIMEOUT_SECONDS = 10


class SlackAlerter:
    """Sends plain-text alerts to a Slack incoming webhook."""

    def __init__(self, webhook_url: str | None = None) -> None:
        self._webhook = (
            webhook_url if webhook_url is not None else settings.slack_webhook_url
        ) or ""
        self._disabled_logged = False

    @property
    def enabled(self) -> bool:
        """True when a webhook URL is configured."""
        return bool(self._webhook)

    def _log_disabled_once(self) -> None:
        if not self._disabled_logged:
            logger.info("Slack alerts disabled (missing webhook URL).")
            self._disabled_logged = True

    def send(self, text: str) -> bool:
        """Post ``text`` to the configured webhook. Returns success as a bool.

        Never raises: configuration and network failures are logged and result
        in ``False``.
        """
        if not self.enabled:
            self._log_disabled_once()
            return False
        try:
            return self._send_with_retry(text)
        except Exception as exc:  # noqa: BLE001 - alerts must never crash callers
            logger.warning("Slack send failed after retries: %s", exc)
            return False

    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=5),
        retry=retry_if_exception_type(requests.RequestException),
    )
    def _send_with_retry(self, text: str) -> bool:
        resp = requests.post(self._webhook, json={"text": text}, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        # Slack webhooks return the literal body 'ok' on success.
        return resp.text.strip().lower() == "ok" or resp.ok


__all__ = ["SlackAlerter"]
