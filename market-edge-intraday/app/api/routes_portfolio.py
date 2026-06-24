"""Portfolio read endpoints: latest snapshot, equity curve and open positions."""
from __future__ import annotations

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.api.schemas import EquityCurve, EquityPoint, PortfolioSummary, PositionOut
from app.database import session_scope
from app.enums import RunMode
from app.logging_config import get_logger
from app.models import PortfolioSnapshot, Position

logger = get_logger(__name__)

router = APIRouter()


@router.get("/summary", response_model=PortfolioSummary)
def portfolio_summary(
    run_mode: RunMode = Query(default=RunMode.LIVE_PAPER),
) -> PortfolioSummary:
    """Latest portfolio snapshot for ``run_mode``.

    Returns a zeroed summary (never 500) when no snapshot exists yet.
    """
    with session_scope() as session:
        stmt = (
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.run_mode == run_mode.value)
            .order_by(PortfolioSnapshot.timestamp.desc())
            .limit(1)
        )
        snap = session.execute(stmt).scalars().first()
        if snap is None:
            return PortfolioSummary(run_mode=run_mode.value)
        return PortfolioSummary.model_validate(snap)


@router.get("/equity_curve", response_model=EquityCurve)
def equity_curve(
    run_mode: RunMode = Query(default=RunMode.LIVE_PAPER),
    limit: int = Query(default=5000, ge=1, le=100_000),
) -> EquityCurve:
    """Ordered equity-curve points for ``run_mode`` (oldest first)."""
    with session_scope() as session:
        stmt = (
            select(PortfolioSnapshot)
            .where(PortfolioSnapshot.run_mode == run_mode.value)
            .order_by(PortfolioSnapshot.timestamp.asc())
            .limit(limit)
        )
        rows = session.execute(stmt).scalars().all()
        points = [
            EquityPoint(
                timestamp=r.timestamp,
                equity_pln=r.equity_pln,
                drawdown=r.drawdown,
                daily_pnl=r.daily_pnl,
            )
            for r in rows
        ]
        return EquityCurve(run_mode=run_mode.value, points=points)


@router.get("/positions", response_model=list[PositionOut])
def open_positions(
    run_mode: RunMode = Query(default=RunMode.LIVE_PAPER),
) -> list[PositionOut]:
    """Currently open positions for ``run_mode``."""
    with session_scope() as session:
        stmt = (
            select(Position)
            .where(Position.run_mode == run_mode.value)
            .order_by(Position.opened_at.asc())
        )
        rows = session.execute(stmt).scalars().all()
        return [PositionOut.model_validate(r) for r in rows]
