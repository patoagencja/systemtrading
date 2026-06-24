"""SQLAlchemy engine / session management.

Times are stored in UTC everywhere. The engine works against PostgreSQL in
production (docker-compose) and falls back to a local SQLite file for unit
tests / quick local runs when no Postgres is reachable.
"""
from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _build_url() -> str:
    # Tests / local runs can force SQLite via env to avoid needing Postgres.
    override = os.getenv("INTRADAY_TEST_DB_URL")
    if override:
        return override
    return settings.sqlalchemy_url


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = _build_url()
        connect_args = {}
        if url.startswith("sqlite"):
            connect_args = {"check_same_thread": False}
        _engine = create_engine(url, pool_pre_ping=True, future=True, connect_args=connect_args)
        logger.info("Database engine created: %s", url.split("@")[-1])
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _SessionFactory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commits on success, rolls back on error."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create all tables. Idempotent."""
    from app.models import Base

    Base.metadata.create_all(get_engine())
    logger.info("Database schema ensured.")


def reset_db() -> None:
    """Drop and recreate all tables. Destructive — used by reset scripts/tests."""
    from app.models import Base

    Base.metadata.drop_all(get_engine())
    Base.metadata.create_all(get_engine())
    logger.warning("Database schema reset (all data dropped).")
