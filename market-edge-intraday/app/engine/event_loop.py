"""Scheduler that drives the live engine on 15-minute boundaries.

Uses APScheduler with America/New_York cron triggers. Ticks fire a minute after
each quarter-hour so the bar has fully closed and the data provider has the
completed bar. The worker is restart-safe: on start it re-prepares the current
session, and ``docker-compose`` restarts it automatically after a reboot.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.engine.session_manager import save_heartbeat
from app.logging_config import get_logger

logger = get_logger(__name__)
ET = ZoneInfo("America/New_York")


class EventLoop:
    def __init__(self, engine):
        self.engine = engine

    def _safe(self, fn, label: str):
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - the loop must never die on one tick
            logger.exception("Error during %s: %s", label, exc)
            save_heartbeat("worker", "ERROR", {"where": label, "error": str(exc)})

    def start_blocking(self) -> None:
        try:
            from apscheduler.schedulers.blocking import BlockingScheduler
            from apscheduler.triggers.cron import CronTrigger
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("APScheduler is required to run the live worker") from exc

        sched = BlockingScheduler(timezone=ET)

        # Pre-session preparation (08:35 ET, Mon-Fri).
        sched.add_job(lambda: self._safe(self.engine.prepare_session, "prepare_session"),
                      CronTrigger(day_of_week="mon-fri", hour=8, minute=35))

        # Trading ticks: one minute past each quarter hour, 09:45–15:46 ET.
        sched.add_job(lambda: self._safe(self.engine.on_tick, "on_tick"),
                      CronTrigger(day_of_week="mon-fri", hour="9-15", minute="1,16,31,46"))

        # End-of-day finalisation (16:05 ET).
        sched.add_job(lambda: self._safe(self.engine.finalize_session, "finalize_session"),
                      CronTrigger(day_of_week="mon-fri", hour=16, minute=5))

        # Liveness heartbeat every 5 minutes.
        sched.add_job(lambda: save_heartbeat("scheduler", "OK", {"at": datetime.now(ET).isoformat()}),
                      CronTrigger(minute="*/5"))

        logger.info("Live paper scheduler starting (America/New_York). Ctrl-C to stop.")
        save_heartbeat("worker", "STARTING", {"mode": "LIVE_PAPER"})
        # On boot, prepare the current session so a mid-day restart resumes cleanly.
        self._safe(self.engine.prepare_session, "prepare_session(boot)")
        try:
            sched.start()
        except (KeyboardInterrupt, SystemExit):  # pragma: no cover
            logger.info("Scheduler stopped.")
