"""Slippage model.

Base slippage comes from the cost scenario (LOW / BASE / STRESS). On top of
that, a market-impact term scales with the order's participation in the bar's
traded value: the bigger your share of the bar, the worse your fill.
"""
from __future__ import annotations

from app.config import settings
from app.enums import COST_TABLE, CostScenario


class SlippageModel:
    def __init__(self, scenario: CostScenario | None = None, impact_coef: float = 0.5):
        self.scenario = scenario or settings.cost_scenario
        self.base = COST_TABLE[self.scenario]["slippage"]
        self.impact_coef = impact_coef

    def slippage_pct(self, order_value_usd: float, bar_dollar_volume: float) -> float:
        """Fractional slippage applied to the reference price.

        participation = order_value / bar_dollar_volume. Impact grows with the
        square-root of participation (a standard, conservative approximation).
        """
        if bar_dollar_volume <= 0:
            return self.base * 3.0  # illiquid bar -> punitive
        participation = order_value_usd / bar_dollar_volume
        impact = self.impact_coef * (participation ** 0.5)
        return self.base * (1.0 + impact * 10.0)
