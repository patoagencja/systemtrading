"""System health and event-log endpoints for monitoring."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.schemas import HeartbeatOut, SystemEventOut, SystemHealth
from app.database import session_scope
from app.enums import RunMode, SessionStatus, Severity
from app.logging_config import get_logger
from app.models import Heartbeat, PortfolioSnapshot, SystemEvent, TradingSession

logger = get_logger(__name__)

router = APIRouter()

_RECENT_WINDOW_HOURS = 24


@router.get("/health", response_model=SystemHealth)
def system_health(
    run_mode: RunMode = Query(default=RunMode.LIVE_PAPER),
) -> SystemHealth:
    """Aggregate health view: DB reachability, heartbeats, data freshness,
    recent error/warning counts, last successful run and kill-switch state.

    Designed to never 500: any DB failure degrades to ``db_reachable=False``.
    """
    try:
        with session_scope() as session:
            # Latest heartbeat per service.
            sub = (
                select(
                    Heartbeat.service,
                    func.max(Heartbeat.timestamp).label("ts"),
                )
                .group_by(Heartbeat.service)
                .subquery()
            )
            hb_stmt = select(Heartbeat).join(
                sub,
                (Heartbeat.service == sub.c.service) & (Heartbeat.timestamp == sub.c.ts),
            )
            heartbeats = [
                HeartbeatOut.model_validate(h) for h in session.execute(hb_stmt).scalars().all()
            ]

            # Latest data time == newest portfolio snapshot timestamp.
            latest_data_time = session.execute(
                select(func.max(PortfolioSnapshot.timestamp))
            ).scalar_one_or_none()

            # Recent error / warning counts in the rolling window.
            since = datetime.now(UTC) - timedelta(hours=_RECENT_WINDOW_HOURS)
            recent_errors = (
                session.execute(
                    select(func.count())
                    .select_from(SystemEvent)
                    .where(
                        SystemEvent.timestamp >= since,
                        SystemEvent.severity.in_(
                            [Severity.ERROR.value, Severity.CRITICAL.value]
                        ),
                    )
                ).scalar_one()
                or 0
            )
            recent_warnings = (
                session.execute(
                    select(func.count())
                    .select_from(SystemEvent)
                    .where(
                        SystemEvent.timestamp >= since,
                        SystemEvent.severity == Severity.WARNING.value,
                    )
                ).scalar_one()
                or 0
            )

            # Last completed session and kill-switch state from latest session.
            last_run = session.execute(
                select(func.max(TradingSession.session_date)).where(
                    TradingSession.run_mode == run_mode.value,
                    TradingSession.status == SessionStatus.COMPLETED.value,
                )
            ).scalar_one_or_none()

            latest_session = session.execute(
                select(TradingSession)
                .where(TradingSession.run_mode == run_mode.value)
                .order_by(TradingSession.session_date.desc(), TradingSession.id.desc())
                .limit(1)
            ).scalars().first()

            kill_reason = latest_session.kill_switch_reason if latest_session else None
            kill_active = bool(kill_reason) or (
                latest_session is not None
                and latest_session.status == SessionStatus.HALTED.value
            )

            return SystemHealth(
                db_reachable=True,
                heartbeats=heartbeats,
                latest_data_time=latest_data_time,
                recent_errors=int(recent_errors),
                recent_warnings=int(recent_warnings),
                last_successful_run=last_run,
                kill_switch_active=kill_active,
                kill_switch_reason=kill_reason,
            )
    except Exception as exc:  # noqa: BLE001 - health must always answer
        logger.warning("system_health degraded (DB unreachable?): %s", exc)
        return SystemHealth(db_reachable=False)


@router.get("/events", response_model=list[SystemEventOut])
def system_events(
    severity: Severity | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=5000),
) -> list[SystemEventOut]:
    """Recent system events, newest first, optionally filtered by severity."""
    try:
        with session_scope() as session:
            stmt = select(SystemEvent)
            if severity is not None:
                stmt = stmt.where(SystemEvent.severity == severity.value)
            stmt = stmt.order_by(SystemEvent.timestamp.desc()).limit(limit)
            rows = session.execute(stmt).scalars().all()
            return [SystemEventOut.model_validate(r) for r in rows]
    except Exception as exc:  # noqa: BLE001 - never 500 the monitor
        logger.warning("system_events query failed: %s", exc)
        return []
