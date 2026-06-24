"""Signal read endpoint with status / run-mode / date filters."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.schemas import SignalOut
from app.database import session_scope
from app.enums import RunMode, SignalStatus
from app.logging_config import get_logger
from app.models import Signal

logger = get_logger(__name__)

router = APIRouter()


@router.get("", response_model=list[SignalOut])
def list_signals(
    run_mode: RunMode = Query(default=RunMode.LIVE_PAPER),
    status: SignalStatus | None = Query(
        default=None, description="pending / filled / rejected / expired / cancelled"
    ),
    signal_date: date | None = Query(default=None, alias="date"),
    limit: int = Query(default=500, ge=1, le=10_000),
) -> list[SignalOut]:
    """List signals for ``run_mode`` with optional status / date filters.

    Includes ``rejection_reason`` for rejected signals.
    """
    with session_scope() as session:
        stmt = select(Signal).where(Signal.run_mode == run_mode.value)
        if status is not None:
            stmt = stmt.where(Signal.status == status.value)
        if signal_date is not None:
            stmt = stmt.where(Signal.session_date == signal_date)
        stmt = stmt.order_by(Signal.signal_time.desc()).limit(limit)
        rows = session.execute(stmt).scalars().all()
        return [SignalOut.model_validate(r) for r in rows]
