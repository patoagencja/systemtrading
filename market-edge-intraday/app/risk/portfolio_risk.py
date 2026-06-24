"""Portfolio-level risk checks applied before a new entry is allowed.

The risk manager is the single gate every candidate must pass after scoring and
before sizing. It is intentionally stateless: callers pass an immutable
:class:`PortfolioRiskState` snapshot, so backtest and live use identical logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings
from app.enums import RejectionReason, StrategyName


@dataclass
class PortfolioRiskState:
    equity_pln: float
    cash_pln: float
    open_symbols: set[str] = field(default_factory=set)
    pending_symbols: set[str] = field(default_factory=set)
    n_open: int = 0
    gross_exposure_pln: float = 0.0
    total_open_risk_pln: float = 0.0
    sector_exposure_pln: dict[str, float] = field(default_factory=dict)
    sector_counts: dict[str, int] = field(default_factory=dict)
    daily_realized_pnl_pln: float = 0.0
    strategy_daily_pnl_pln: dict[str, float] = field(default_factory=dict)
    consecutive_losses: int = 0
    kill_switch_active: bool = False
    universe_symbols: set[str] = field(default_factory=set)


@dataclass
class RiskDecision:
    allowed: bool
    reason: RejectionReason | None = None
    detail: str = ""
    gross_exposure_room_pln: float = 0.0
    sector_room_pln: float = 0.0


class RiskManager:
    """Vets candidate entries against all portfolio limits."""

    def check_new_entry(
        self,
        *,
        symbol: str,
        strategy: StrategyName,
        sector: str,
        state: PortfolioRiskState,
        check_universe: bool = True,
    ) -> RiskDecision:
        eq = max(1.0, state.equity_pln)

        if state.kill_switch_active:
            return RiskDecision(False, RejectionReason.KILL_SWITCH_ACTIVE, "kill switch active")

        # Daily loss guard (total).
        if state.daily_realized_pnl_pln <= -settings.max_daily_loss_pct * eq:
            return RiskDecision(False, RejectionReason.DAILY_LOSS_LIMIT, "daily loss limit hit")

        # Per-strategy daily loss guard.
        strat_pnl = state.strategy_daily_pnl_pln.get(str(strategy), 0.0)
        if strat_pnl <= -settings.max_strategy_daily_loss_pct * eq:
            return RiskDecision(
                False, RejectionReason.DAILY_LOSS_LIMIT, f"{strategy} daily loss limit"
            )

        # Universe membership.
        if check_universe and state.universe_symbols and symbol not in state.universe_symbols:
            return RiskDecision(False, RejectionReason.NOT_IN_UNIVERSE, "not in today's universe")

        # One position per ticker; no stacking on pending either.
        if symbol in state.open_symbols:
            return RiskDecision(False, RejectionReason.DUPLICATE_TICKER, "position already open")
        if symbol in state.pending_symbols:
            return RiskDecision(False, RejectionReason.DUPLICATE_TICKER, "pending order exists")

        # Max open positions.
        if state.n_open >= settings.max_open_positions:
            return RiskDecision(False, RejectionReason.MAX_POSITIONS, "max open positions reached")

        # Total open risk.
        if state.total_open_risk_pln >= settings.max_total_open_risk_pct * eq:
            return RiskDecision(
                False, RejectionReason.PORTFOLIO_RISK_LIMIT, "total open risk limit"
            )

        # Sector position count.
        if state.sector_counts.get(sector, 0) >= settings.max_positions_per_sector:
            return RiskDecision(False, RejectionReason.SECTOR_LIMIT, "max positions per sector")

        # Gross exposure room.
        gross_cap = settings.max_gross_exposure_pct * eq
        gross_room = gross_cap - state.gross_exposure_pln
        if gross_room <= 0:
            return RiskDecision(False, RejectionReason.PORTFOLIO_RISK_LIMIT, "gross exposure limit")

        # Sector exposure room.
        sector_cap = settings.max_sector_exposure_pct * eq
        sector_room = sector_cap - state.sector_exposure_pln.get(sector, 0.0)
        if sector_room <= 0:
            return RiskDecision(False, RejectionReason.SECTOR_LIMIT, "sector exposure limit")

        return RiskDecision(
            allowed=True,
            gross_exposure_room_pln=gross_room,
            sector_room_pln=sector_room,
        )
