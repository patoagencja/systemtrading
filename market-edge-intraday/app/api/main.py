"""FastAPI application for the intraday paper-trading dashboard.

Read-only views over the database. Import as ``app.api.main:app`` for uvicorn::

    uvicorn app.api.main:app --host 0.0.0.0 --port 8000

CORS is open because this is an internal dashboard API.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.api import (
    routes_portfolio,
    routes_signals,
    routes_system,
    routes_trades,
)
from app.api.schemas import ServiceInfo
from app.database import session_scope
from app.logging_config import get_logger

logger = get_logger(__name__)

SERVICE_NAME = "market-edge-intraday-api"
VERSION = "1.0.0"

app = FastAPI(
    title="Market Edge Intraday API",
    version=VERSION,
    description="Read-only dashboard / monitoring API for the intraday paper-trading system.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_portfolio.router, prefix="/portfolio", tags=["portfolio"])
app.include_router(routes_trades.router, prefix="/trades", tags=["trades"])
app.include_router(routes_signals.router, prefix="/signals", tags=["signals"])
app.include_router(routes_system.router, prefix="/system", tags=["system"])


@app.get("/", response_model=ServiceInfo)
def root() -> ServiceInfo:
    """Service identity."""
    return ServiceInfo(service=SERVICE_NAME, version=VERSION)


@app.get("/health")
def liveness() -> dict[str, object]:
    """Liveness + DB reachability probe (always 200)."""
    db_ok = False
    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
        db_ok = True
    except Exception as exc:  # noqa: BLE001 - liveness must not raise
        logger.warning("Liveness DB check failed: %s", exc)
    return {"status": "ok", "service": SERVICE_NAME, "version": VERSION, "db_reachable": db_ok}
