"""Trade read endpoints: filtered list and single-trade lookup."""
from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select

from app.api.schemas import TradeOut
from app.database import session_scope
from app.enums import RunMode, StrategyName
from app.logging_config import get_logger
from app.models import Trade

logger = get_logger(__name__)

router = APIRouter()


@router.get("", response_model=list[TradeOut])
def list_trades(
    run_mode: RunMode = Query(default=RunMode.LIVE_PAPER),
    strategy: StrategyName | None = Query(default=None),
    start: date | None = Query(default=None, description="Inclusive session_date lower bound."),
    end: date | None = Query(default=None, description="Inclusive session_date upper bound."),
    result: str | None = Query(default=None, description="Filter by 'win' or 'loss'."),
    limit: int = Query(default=500, ge=1, le=10_000),
) -> list[TradeOut]:
    """List trades for ``run_mode`` with optional filters.

    ``result='win'`` keeps trades with net_pnl_pln > 0, ``'loss'`` keeps < 0.
    """
    with session_scope() as session:
        stmt = select(Trade).where(Trade.run_mode == run_mode.value)
        if strategy is not None:
            stmt = stmt.where(Trade.strategy == strategy.value)
        if start is not None:
            stmt = stmt.where(Trade.session_date >= start)
        if end is not None:
            stmt = stmt.where(Trade.session_date <= end)
        if result is not None:
            r = result.strip().lower()
            if r == "win":
                stmt = stmt.where(Trade.net_pnl_pln > 0)
            elif r == "loss":
                stmt = stmt.where(Trade.net_pnl_pln < 0)
        stmt = stmt.order_by(Trade.entry_time.desc()).limit(limit)
        rows = session.execute(stmt).scalars().all()
        return [TradeOut.model_validate(r) for r in rows]


@router.get("/{trade_id}", response_model=TradeOut)
def get_trade(trade_id: int) -> TradeOut:
    """Fetch a single trade by id (404 if missing)."""
    with session_scope() as session:
        trade = session.get(Trade, trade_id)
        if trade is None:
            raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found")
        return TradeOut.model_validate(trade)
