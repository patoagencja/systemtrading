"""Alert fan-out.

:class:`AlertManager` formats domain events into text blocks and dispatches
them to every *enabled* channel (Telegram, Slack, ...). It is always safe to
instantiate with no credentials: in that case all channels are disabled and
every method is a graceful no-op that returns an empty result map.
"""
from __future__ import annotations

from typing import Any

from app.alerts.alert_formatter import (
    format_daily_summary,
    format_kill_switch,
    format_trade_closed,
    format_trade_opened,
)
from app.alerts.slack import SlackAlerter
from app.alerts.telegram import TelegramAlerter
from app.logging_config import get_logger

logger = get_logger(__name__)


class AlertManager:
    """Dispatches formatted alerts to all configured channels."""

    def __init__(
        self,
        telegram: TelegramAlerter | None = None,
        slack: SlackAlerter | None = None,
    ) -> None:
        self.telegram = telegram if telegram is not None else TelegramAlerter()
        self.slack = slack if slack is not None else SlackAlerter()

    @property
    def channels(self) -> list[Any]:
        """All configured channel objects (enabled or not)."""
        return [self.telegram, self.slack]

    @property
    def enabled(self) -> bool:
        """True when at least one channel is enabled."""
        return any(getattr(c, "enabled", False) for c in self.channels)

    def dispatch(self, text: str) -> dict[str, bool]:
        """Send ``text`` to every enabled channel; return per-channel success.

        Disabled channels are skipped silently. Never raises.
        """
        results: dict[str, bool] = {}
        for channel in self.channels:
            name = type(channel).__name__
            if not getattr(channel, "enabled", False):
                continue
            try:
                results[name] = bool(channel.send(text))
            except Exception as exc:  # noqa: BLE001 - never crash the caller
                logger.warning("Alert dispatch to %s failed: %s", name, exc)
                results[name] = False
        return results

    # ------------------------------------------------------------------ events
    def trade_opened(self, **kwargs: Any) -> dict[str, bool]:
        """Format and dispatch a trade-opened alert."""
        return self.dispatch(format_trade_opened(**kwargs))

    def trade_closed(self, **kwargs: Any) -> dict[str, bool]:
        """Format and dispatch a trade-closed alert."""
        return self.dispatch(format_trade_closed(**kwargs))

    def kill_switch(self, reason: str, positions_closed: bool) -> dict[str, bool]:
        """Format and dispatch a kill-switch alert."""
        return self.dispatch(format_kill_switch(reason, positions_closed))

    def daily_summary(self, **kwargs: Any) -> dict[str, bool]:
        """Format and dispatch an end-of-day summary alert."""
        return self.dispatch(format_daily_summary(**kwargs))


__all__ = ["AlertManager", "TelegramAlerter", "SlackAlerter"]
