"""Pydantic response models for the read-only dashboard API.

These mirror the ORM rows in :mod:`app.models` but expose only the fields the
dashboard and external monitors need, with JSON-friendly types.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class _ORMModel(BaseModel):
    """Base allowing construction directly from ORM objects."""

    model_config = ConfigDict(from_attributes=True)


class PortfolioSummary(_ORMModel):
    run_mode: str
    timestamp: datetime | None = None
    session_date: date | None = None
    cash_pln: float = 0.0
    invested_pln: float = 0.0
    equity_pln: float = 0.0
    daily_pnl: float = 0.0
    total_pnl: float = 0.0
    exposure: float = 0.0
    open_risk: float = 0.0
    drawdown: float = 0.0
    open_positions: int = 0


class EquityPoint(_ORMModel):
    timestamp: datetime
    equity_pln: float
    drawdown: float = 0.0
    daily_pnl: float = 0.0


class EquityCurve(BaseModel):
    run_mode: str
    points: list[EquityPoint] = []


class PositionOut(_ORMModel):
    id: int
    run_mode: str
    symbol: str
    strategy: str
    quantity: float
    average_entry: float
    current_price: float | None = None
    stop_price: float | None = None
    target_price: float | None = None
    unrealized_pnl: float | None = None
    open_risk: float | None = None
    opened_at: datetime
    updated_at: datetime


class TradeOut(_ORMModel):
    id: int
    run_mode: str
    session_date: date
    symbol: str
    strategy: str
    side: str
    entry_time: datetime
    entry_price: float
    exit_time: datetime | None = None
    exit_price: float | None = None
    shares: float
    stop_price: float | None = None
    target_price: float | None = None
    gross_pnl_usd: float | None = None
    net_pnl_usd: float | None = None
    net_pnl_pln: float | None = None
    return_pct: float | None = None
    r_multiple: float | None = None
    holding_bars: int | None = None
    exit_reason: str | None = None
    costs: float | None = None
    slippage: float | None = None
    status: str


class SignalOut(_ORMModel):
    id: int
    run_mode: str
    session_date: date
    signal_time: datetime
    symbol: str
    strategy: str
    score: float
    side: str
    planned_entry: float
    stop_price: float
    target_price: float
    risk_reward: float | None = None
    status: str
    rejection_reason: str | None = None


class HeartbeatOut(_ORMModel):
    service: str
    timestamp: datetime
    status: str


class SystemEventOut(_ORMModel):
    id: int
    timestamp: datetime
    severity: str
    event_type: str
    message: str
    metadata_json: dict[str, Any] | None = None


class SystemHealth(BaseModel):
    db_reachable: bool
    heartbeats: list[HeartbeatOut] = []
    latest_data_time: datetime | None = None
    recent_errors: int = 0
    recent_warnings: int = 0
    last_successful_run: date | None = None
    kill_switch_active: bool = False
    kill_switch_reason: str | None = None


class StrategyStatsOut(BaseModel):
    strategy: str
    trades: int = 0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    net_pnl_pln: float = 0.0
    avg_holding_bars: float = 0.0
    max_drawdown: float = 0.0


class ServiceInfo(BaseModel):
    service: str
    version: str


__all__ = [
    "PortfolioSummary",
    "EquityPoint",
    "EquityCurve",
    "PositionOut",
    "TradeOut",
    "SignalOut",
    "HeartbeatOut",
    "SystemEventOut",
    "SystemHealth",
    "StrategyStatsOut",
    "ServiceInfo",
]
