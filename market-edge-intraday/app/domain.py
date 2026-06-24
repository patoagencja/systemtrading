"""Lightweight in-memory dataclasses passed between engine components.

These are deliberately decoupled from SQLAlchemy so strategies, the risk
manager, the fill model and the backtester can be unit-tested without a
database. Persistence converts these into the ORM rows in ``app.models``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.enums import (
    ExitReason,
    OrderType,
    RejectionReason,
    Side,
    StrategyName,
)


@dataclass(frozen=True)
class Bar:
    """A single completed OHLCV bar. Timestamp is the bar's *open* time (UTC)."""

    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def dollar_volume(self) -> float:
        return self.close * self.volume

    @property
    def typical_price(self) -> float:
        return (self.high + self.low + self.close) / 3.0


@dataclass
class SignalCandidate:
    """A raw signal produced by a strategy on a completed bar T.

    Entry must execute no earlier than the *open* of bar T+1 (no look-ahead).
    """

    symbol: str
    strategy: StrategyName
    side: Side
    signal_time: datetime  # close time of the bar that produced the signal
    reference_price: float  # close of bar T (for reference only)
    stop_price: float
    target_price: float
    score: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def risk_per_share(self) -> float:
        return abs(self.reference_price - self.stop_price)

    @property
    def reward_per_share(self) -> float:
        return abs(self.target_price - self.reference_price)

    @property
    def risk_reward(self) -> float:
        rps = self.risk_per_share
        return self.reward_per_share / rps if rps > 0 else 0.0


@dataclass
class RejectedSignal:
    candidate: SignalCandidate
    reason: RejectionReason
    detail: str = ""


@dataclass
class Fill:
    """The realised execution of an order (entry or exit)."""

    symbol: str
    timestamp: datetime
    side: Side
    quantity: float
    requested_price: float  # reference (e.g. bar open)
    fill_price: float  # after spread + slippage
    commission_usd: float
    slippage_usd: float
    spread_cost_usd: float


@dataclass
class PaperPosition:
    """An open position held by the paper broker."""

    symbol: str
    strategy: StrategyName
    side: Side
    quantity: float
    entry_price: float  # net fill price (incl. costs baked into cash, not price)
    raw_entry_price: float  # mid/open reference at entry
    stop_price: float
    target_price: float
    initial_risk_per_share: float
    opened_at: datetime
    bars_held: int = 0
    current_price: float = 0.0
    highest_close: float = 0.0
    moved_to_breakeven: bool = False
    entry_costs_usd: float = 0.0
    usdpln_entry: float = 4.0
    signal_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def unrealized_pnl_usd(self) -> float:
        if self.side == Side.LONG:
            return (self.current_price - self.entry_price) * self.quantity
        return (self.entry_price - self.current_price) * self.quantity

    @property
    def open_risk_usd(self) -> float:
        """Remaining risk to stop, in USD (>=0). Negative stop distance => 0."""
        if self.side == Side.LONG:
            dist = max(0.0, self.current_price - self.stop_price)
        else:
            dist = max(0.0, self.stop_price - self.current_price)
        return dist * self.quantity

    @property
    def r_multiple(self) -> float:
        if self.initial_risk_per_share <= 0:
            return 0.0
        if self.side == Side.LONG:
            return (self.current_price - self.entry_price) / self.initial_risk_per_share
        return (self.entry_price - self.current_price) / self.initial_risk_per_share


@dataclass
class ClosedTrade:
    """A completed round-trip trade ready to be persisted / analysed."""

    symbol: str
    strategy: StrategyName
    side: Side
    entry_time: datetime
    entry_price: float
    exit_time: datetime
    exit_price: float
    shares: float
    stop_price: float
    target_price: float
    gross_pnl_usd: float
    net_pnl_usd: float
    net_pnl_pln: float
    return_pct: float
    r_multiple: float
    holding_bars: int
    exit_reason: ExitReason
    costs_usd: float
    slippage_usd: float
    usdpln_entry: float
    usdpln_exit: float
    fx_pnl_pln: float
    signal_id: int | None = None


@dataclass
class PendingOrder:
    """An order queued on bar T, to be executed on bar T+1."""

    candidate: SignalCandidate
    quantity: float
    order_type: OrderType
    created_at: datetime
    signal_id: int | None = None
