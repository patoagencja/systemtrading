"""Daily loss guard — halts new entries once daily losses exceed the limit.

Distinct from the kill switch: the loss guard is a *normal* risk control that
resets each session, whereas the kill switch reacts to anomalies/failures.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.config import settings


@dataclass
class DailyLossStatus:
    blocked: bool
    reason: str = ""
    daily_pnl_pln: float = 0.0
    limit_pln: float = 0.0


class DailyLossGuard:
    def __init__(self, equity_pln: float):
        self.start_equity_pln = equity_pln
        self.daily_pnl_pln = 0.0
        self.strategy_pnl_pln: dict[str, float] = {}

    def reset(self, equity_pln: float) -> None:
        self.start_equity_pln = equity_pln
        self.daily_pnl_pln = 0.0
        self.strategy_pnl_pln = {}

    def record(self, strategy: str, pnl_pln: float) -> None:
        self.daily_pnl_pln += pnl_pln
        self.strategy_pnl_pln[strategy] = self.strategy_pnl_pln.get(strategy, 0.0) + pnl_pln

    def status(self) -> DailyLossStatus:
        limit = settings.max_daily_loss_pct * self.start_equity_pln
        if self.daily_pnl_pln <= -limit:
            return DailyLossStatus(
                True, "daily loss limit reached", self.daily_pnl_pln, limit
            )
        return DailyLossStatus(False, "", self.daily_pnl_pln, limit)

    def strategy_blocked(self, strategy: str) -> bool:
        limit = settings.max_strategy_daily_loss_pct * self.start_equity_pln
        return self.strategy_pnl_pln.get(strategy, 0.0) <= -limit
