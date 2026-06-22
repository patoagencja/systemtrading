"""NYSE session management. Uses zoneinfo for proper DST handling."""
import datetime
from zoneinfo import ZoneInfo

from app.intraday.config import (
    INTRADAY_FIRST_ENTRY_TIME_ET,
    INTRADAY_LAST_ENTRY_TIME_ET,
    INTRADAY_FORCE_CLOSE_START_ET,
    INTRADAY_FORCE_CLOSE_DEADLINE_ET,
)

NY_TZ = ZoneInfo("America/New_York")

# Try pandas_market_calendars for accurate NYSE holidays
try:
    import pandas_market_calendars as mcal
    _NYSE = mcal.get_calendar("NYSE")
    _USE_PMC = True
except Exception:
    _USE_PMC = False

# Fallback hardcoded NYSE holidays 2020-2027 (observed dates)
_HARDCODED_HOLIDAYS = {
    datetime.date(2020, 1, 1), datetime.date(2020, 1, 20), datetime.date(2020, 2, 17),
    datetime.date(2020, 4, 10), datetime.date(2020, 5, 25), datetime.date(2020, 7, 3),
    datetime.date(2020, 9, 7), datetime.date(2020, 11, 26), datetime.date(2020, 12, 25),
    datetime.date(2021, 1, 1), datetime.date(2021, 1, 18), datetime.date(2021, 2, 15),
    datetime.date(2021, 4, 2), datetime.date(2021, 5, 31), datetime.date(2021, 7, 5),
    datetime.date(2021, 9, 6), datetime.date(2021, 11, 25), datetime.date(2021, 12, 24),
    datetime.date(2022, 1, 17), datetime.date(2022, 2, 21),
    datetime.date(2022, 4, 15), datetime.date(2022, 5, 30), datetime.date(2022, 6, 20),
    datetime.date(2022, 7, 4), datetime.date(2022, 9, 5), datetime.date(2022, 11, 24),
    datetime.date(2022, 12, 26),
    datetime.date(2023, 1, 2), datetime.date(2023, 1, 16), datetime.date(2023, 2, 20),
    datetime.date(2023, 4, 7), datetime.date(2023, 5, 29), datetime.date(2023, 6, 19),
    datetime.date(2023, 7, 4), datetime.date(2023, 9, 4), datetime.date(2023, 11, 23),
    datetime.date(2023, 12, 25),
    datetime.date(2024, 1, 1), datetime.date(2024, 1, 15), datetime.date(2024, 2, 19),
    datetime.date(2024, 3, 29), datetime.date(2024, 5, 27), datetime.date(2024, 6, 19),
    datetime.date(2024, 7, 4), datetime.date(2024, 9, 2), datetime.date(2024, 11, 28),
    datetime.date(2024, 12, 25),
    datetime.date(2025, 1, 1), datetime.date(2025, 1, 9),  # National Day of Mourning (Carter)
    datetime.date(2025, 1, 20), datetime.date(2025, 2, 17),
    datetime.date(2025, 4, 18), datetime.date(2025, 5, 26), datetime.date(2025, 6, 19),
    datetime.date(2025, 7, 4), datetime.date(2025, 9, 1), datetime.date(2025, 11, 27),
    datetime.date(2025, 12, 25),
    datetime.date(2026, 1, 1), datetime.date(2026, 1, 19), datetime.date(2026, 2, 16),
    datetime.date(2026, 4, 3), datetime.date(2026, 5, 25), datetime.date(2026, 6, 19),
    datetime.date(2026, 7, 3), datetime.date(2026, 9, 7), datetime.date(2026, 11, 26),
    datetime.date(2026, 12, 25),
    datetime.date(2027, 1, 1), datetime.date(2027, 1, 18), datetime.date(2027, 2, 15),
    datetime.date(2027, 3, 26), datetime.date(2027, 5, 31), datetime.date(2027, 6, 18),
    datetime.date(2027, 7, 5), datetime.date(2027, 9, 6), datetime.date(2027, 11, 25),
    datetime.date(2027, 12, 24),
}


def _parse_time(t_str: str) -> datetime.time:
    """Parse 'HH:MM' string to time object."""
    h, m = t_str.split(":")
    return datetime.time(int(h), int(m))


_FIRST_ENTRY = _parse_time(INTRADAY_FIRST_ENTRY_TIME_ET)
_LAST_ENTRY  = _parse_time(INTRADAY_LAST_ENTRY_TIME_ET)
_FORCE_START = _parse_time(INTRADAY_FORCE_CLOSE_START_ET)
_FORCE_DL    = _parse_time(INTRADAY_FORCE_CLOSE_DEADLINE_ET)
_OPEN_TIME   = datetime.time(9, 30)
_CLOSE_TIME  = datetime.time(16, 0)


class SessionManager:
    """NYSE session utilities. All times in America/New_York."""

    @staticmethod
    def get_current_et_time() -> datetime.datetime:
        """Return current wall-clock time in ET."""
        return datetime.datetime.now(tz=NY_TZ)

    @staticmethod
    def is_trading_day(date: datetime.date = None) -> bool:
        """Return True if date is a NYSE trading day (weekday, not holiday)."""
        if date is None:
            date = SessionManager.get_current_et_time().date()
        if date.weekday() >= 5:  # Saturday=5, Sunday=6
            return False
        if _USE_PMC:
            try:
                import pandas as pd
                sched = _NYSE.schedule(
                    start_date=date.isoformat(),
                    end_date=date.isoformat(),
                )
                return not sched.empty
            except Exception:
                pass
        return date not in _HARDCODED_HOLIDAYS

    @staticmethod
    def is_market_open(dt: datetime.datetime = None) -> bool:
        """Return True if regular session (09:30-16:00 ET) is open."""
        if dt is None:
            dt = SessionManager.get_current_et_time()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=NY_TZ)
        else:
            dt = dt.astimezone(NY_TZ)
        if not SessionManager.is_trading_day(dt.date()):
            return False
        t = dt.time()
        return _OPEN_TIME <= t < _CLOSE_TIME

    @staticmethod
    def get_session_date(dt: datetime.datetime = None) -> datetime.date:
        """Return the current (or most recent) session date in ET."""
        if dt is None:
            dt = SessionManager.get_current_et_time()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=NY_TZ)
        else:
            dt = dt.astimezone(NY_TZ)
        return dt.date()

    @staticmethod
    def can_enter_new_position(dt: datetime.datetime = None) -> bool:
        """Return True if within allowed entry window (10:00-14:30 ET)."""
        if dt is None:
            dt = SessionManager.get_current_et_time()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=NY_TZ)
        else:
            dt = dt.astimezone(NY_TZ)
        if not SessionManager.is_trading_day(dt.date()):
            return False
        t = dt.time()
        return _FIRST_ENTRY <= t <= _LAST_ENTRY

    @staticmethod
    def should_force_close(dt: datetime.datetime = None) -> bool:
        """Return True if >= 15:40 ET — begin forced close."""
        if dt is None:
            dt = SessionManager.get_current_et_time()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=NY_TZ)
        else:
            dt = dt.astimezone(NY_TZ)
        return dt.time() >= _FORCE_START

    @staticmethod
    def is_past_deadline(dt: datetime.datetime = None) -> bool:
        """Return True if >= 15:50 ET — hard deadline."""
        if dt is None:
            dt = SessionManager.get_current_et_time()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=NY_TZ)
        else:
            dt = dt.astimezone(NY_TZ)
        return dt.time() >= _FORCE_DL

    @staticmethod
    def time_to_forced_close_minutes(dt: datetime.datetime = None) -> float:
        """Return minutes until force-close window opens (negative if already past)."""
        if dt is None:
            dt = SessionManager.get_current_et_time()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=NY_TZ)
        else:
            dt = dt.astimezone(NY_TZ)
        force_dt = dt.replace(
            hour=_FORCE_START.hour, minute=_FORCE_START.minute,
            second=0, microsecond=0
        )
        delta = (force_dt - dt).total_seconds() / 60.0
        return delta

    @staticmethod
    def get_market_status(dt: datetime.datetime = None) -> str:
        """Return PRE-MARKET, OPEN, CLOSING_WINDOW, or CLOSED."""
        if dt is None:
            dt = SessionManager.get_current_et_time()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=NY_TZ)
        else:
            dt = dt.astimezone(NY_TZ)
        if not SessionManager.is_trading_day(dt.date()):
            return "CLOSED"
        t = dt.time()
        if t < _OPEN_TIME:
            return "PRE-MARKET"
        if t >= _CLOSE_TIME:
            return "CLOSED"
        if t >= _FORCE_START:
            return "CLOSING_WINDOW"
        return "OPEN"

    @staticmethod
    def get_next_bar_time(bar_time: datetime.datetime, interval_minutes: int = 30) -> datetime.datetime:
        """Return the start of the next bar after bar_time."""
        if bar_time.tzinfo is None:
            bar_time = bar_time.replace(tzinfo=NY_TZ)
        return bar_time + datetime.timedelta(minutes=interval_minutes)

    @staticmethod
    def is_complete_bar(bar_time: datetime.datetime, interval_minutes: int = 30,
                        dt: datetime.datetime = None) -> bool:
        """Return True if the bar ending at bar_time+interval_minutes is fully in the past."""
        if dt is None:
            dt = SessionManager.get_current_et_time()
        if bar_time.tzinfo is None:
            bar_time = bar_time.replace(tzinfo=NY_TZ)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=NY_TZ)
        bar_end = bar_time + datetime.timedelta(minutes=interval_minutes)
        return dt >= bar_end
