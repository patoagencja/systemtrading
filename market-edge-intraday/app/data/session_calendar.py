"""US-equity (XNYS) session calendar utilities.

Wraps :mod:`pandas_market_calendars` to answer trading-day, session-bounds and
expected-bar-grid questions, correctly handling weekends, holidays, half-days
(early closes) and US daylight-saving transitions.

If ``pandas_market_calendars`` cannot be imported, we fall back to a naive
Monday-Friday 09:30-16:00 ET calendar (no holidays, no early closes) and log a
clear warning — callers should treat this as degraded.
"""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from app.logging_config import get_logger

logger = get_logger(__name__)

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

_REGULAR_OPEN = time(9, 30)
_REGULAR_CLOSE = time(16, 0)
_EARLY_CLOSE = time(13, 0)

try:  # pragma: no cover - exercised indirectly
    import pandas_market_calendars as mcal

    _CAL = mcal.get_calendar("XNYS")
    _HAVE_MCAL = True
except Exception as exc:  # pragma: no cover - defensive fallback
    _CAL = None
    _HAVE_MCAL = False
    logger.warning(
        "pandas_market_calendars unavailable (%s); falling back to naive "
        "Mon-Fri 09:30-16:00 ET calendar (no holidays / early closes).",
        exc,
    )


def _as_date(d: date | datetime) -> date:
    return d.date() if isinstance(d, datetime) else d


def now_et() -> datetime:
    """Current wall-clock time in America/New_York (tz-aware)."""
    return datetime.now(tz=ET)


def _schedule(d: date) -> pd.DataFrame | None:
    if not _HAVE_MCAL:
        return None
    sched = _CAL.schedule(start_date=d.isoformat(), end_date=d.isoformat())
    return sched if not sched.empty else None


def is_trading_day(d: date | datetime) -> bool:
    """True if ``d`` is a regular US-equity trading day."""
    d = _as_date(d)
    if _HAVE_MCAL:
        return _schedule(d) is not None
    return d.weekday() < 5


def is_early_close(d: date | datetime) -> bool:
    """True if ``d`` is a trading day with an early (half-day) close."""
    d = _as_date(d)
    if not is_trading_day(d):
        return False
    if not _HAVE_MCAL:
        return False
    sched = _schedule(d)
    if sched is None:
        return False
    close_utc = sched.iloc[0]["market_close"].to_pydatetime().astimezone(ET)
    return close_utc.time() < _REGULAR_CLOSE


def session_bounds_utc(d: date | datetime) -> tuple[datetime, datetime] | None:
    """Regular-session open/close for ``d`` as tz-aware UTC datetimes.

    Returns ``None`` if ``d`` is not a trading day. Honors early closes.
    """
    d = _as_date(d)
    if not is_trading_day(d):
        return None
    if _HAVE_MCAL:
        sched = _schedule(d)
        if sched is None:
            return None
        row = sched.iloc[0]
        open_utc = row["market_open"].to_pydatetime().astimezone(UTC)
        close_utc = row["market_close"].to_pydatetime().astimezone(UTC)
        return open_utc, close_utc
    open_et = datetime.combine(d, _REGULAR_OPEN, tzinfo=ET)
    close_et = datetime.combine(d, _REGULAR_CLOSE, tzinfo=ET)
    return open_et.astimezone(UTC), close_et.astimezone(UTC)


def previous_session(d: date | datetime) -> date | None:
    """The most recent trading day strictly before ``d``."""
    d = _as_date(d)
    cursor = d
    for _ in range(15):  # ample to skip long holiday weekends
        cursor = cursor.fromordinal(cursor.toordinal() - 1)
        if is_trading_day(cursor):
            return cursor
    return None


def _interval_minutes(interval: str) -> int:
    raw = interval.lower().replace("min", "").replace("m", "").strip()
    try:
        return int(raw)
    except ValueError:
        return 15


def expected_bar_starts(d: date | datetime, interval: str = "15Min") -> list[datetime]:
    """UTC bar-open timestamps for the regular session on ``d``.

    Bars are aligned to the session open and span ``[open, close)`` so the last
    bar's open is one ``interval`` before the close. Returns ``[]`` for
    non-trading days. Handles early-close days via :func:`session_bounds_utc`.
    """
    bounds = session_bounds_utc(d)
    if bounds is None:
        return []
    open_utc, close_utc = bounds
    step = pd.Timedelta(minutes=_interval_minutes(interval))
    starts: list[datetime] = []
    cursor = pd.Timestamp(open_utc)
    end = pd.Timestamp(close_utc)
    while cursor + step <= end:
        starts.append(cursor.to_pydatetime())
        cursor = cursor + step
    return starts
