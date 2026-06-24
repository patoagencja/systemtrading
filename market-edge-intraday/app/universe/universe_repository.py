"""Persistence helpers for the daily universe and instrument master.

Wraps ``app.database.session_scope`` to write :class:`UniverseSnapshot` rows and
upsert :class:`Instrument` rows. Read helpers return symbol lists.
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.database import session_scope
from app.logging_config import get_logger
from app.models import Instrument, UniverseSnapshot

if TYPE_CHECKING:  # avoid a runtime import cycle with builder
    from app.universe.builder import UniverseEntry

logger = get_logger(__name__)


def save_snapshot(snapshot_date: date, entries: list[UniverseEntry]) -> int:
    """Persist the universe snapshot for ``snapshot_date``.

    Existing rows for that date are replaced (idempotent re-runs). Returns the
    number of rows written.
    """
    with session_scope() as session:
        session.query(UniverseSnapshot).filter(
            UniverseSnapshot.session_date == snapshot_date
        ).delete()
        for e in entries:
            session.add(
                UniverseSnapshot(
                    session_date=snapshot_date,
                    symbol=e.symbol,
                    rank=e.rank,
                    avg_daily_volume=e.adv,
                    avg_dollar_volume=e.dollar_volume,
                    price=e.price,
                    atr_daily=e.atr_daily,
                    spread=e.spread,
                    sector=e.sector,
                    asset_type=e.asset_type,
                    inclusion_reason=e.inclusion_reason,
                )
            )
    logger.info("Saved %d universe rows for %s.", len(entries), snapshot_date)
    return len(entries)


def upsert_instruments(entries: list[UniverseEntry]) -> int:
    """Insert or update the instrument master from ``entries``. Returns count."""
    now = datetime.now(tz=UTC)
    with session_scope() as session:
        for e in entries:
            inst = session.get(Instrument, e.symbol)
            if inst is None:
                session.add(
                    Instrument(
                        symbol=e.symbol,
                        name=e.name,
                        exchange=None,
                        sector=e.sector,
                        industry=e.industry,
                        asset_type=e.asset_type,
                        active=True,
                        created_at=now,
                        updated_at=now,
                    )
                )
            else:
                inst.name = e.name or inst.name
                inst.sector = e.sector or inst.sector
                inst.industry = e.industry or inst.industry
                inst.asset_type = e.asset_type or inst.asset_type
                inst.active = True
                inst.updated_at = now
    logger.info("Upserted %d instruments.", len(entries))
    return len(entries)


def get_universe(snapshot_date: date) -> list[str]:
    """Return the ranked symbol list for ``snapshot_date`` (may be empty)."""
    with session_scope() as session:
        rows = session.execute(
            select(UniverseSnapshot.symbol)
            .where(UniverseSnapshot.session_date == snapshot_date)
            .order_by(UniverseSnapshot.rank)
        ).scalars().all()
    return list(rows)


def latest_universe() -> list[str]:
    """Return the ranked symbol list for the most recent snapshot date."""
    with session_scope() as session:
        latest = session.execute(
            select(UniverseSnapshot.session_date)
            .order_by(UniverseSnapshot.session_date.desc())
            .limit(1)
        ).scalar_one_or_none()
        if latest is None:
            return []
        rows = session.execute(
            select(UniverseSnapshot.symbol)
            .where(UniverseSnapshot.session_date == latest)
            .order_by(UniverseSnapshot.rank)
        ).scalars().all()
    return list(rows)
