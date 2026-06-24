"""Fill model: turns a desired trade into a realistic fill with costs.

Entry (long): you pay the reference price PLUS half the spread PLUS slippage.
Exit  (long): you receive the reference price MINUS half the spread MINUS slippage.
Commission is charged per side on the notional. Nothing is an ideal fill.
"""
from __future__ import annotations

from datetime import datetime

from app.config import settings
from app.domain import Bar, Fill
from app.enums import COST_TABLE, CostScenario, Side
from app.execution.slippage_model import SlippageModel


def estimate_spread_bps(price: float, bar_dollar_volume: float) -> float:
    """Conservative spread estimate (basis points) when no bid/ask is available.

    More liquid bars get tighter spreads; cheap/illiquid names get wider ones.
    """
    if price <= 0:
        return 20.0
    # Liquidity component: >$5M/bar -> ~2bps; thin bars widen toward ~20bps.
    if bar_dollar_volume >= 5_000_000:
        liq = 2.0
    elif bar_dollar_volume >= 1_000_000:
        liq = 5.0
    elif bar_dollar_volume >= 250_000:
        liq = 10.0
    else:
        liq = 20.0
    # Cheap stocks have proportionally wider spreads.
    if price < 10:
        liq += 3.0
    return liq


class FillModel:
    def __init__(self, scenario: CostScenario | None = None):
        self.scenario = scenario or settings.cost_scenario
        self.commission_pct = COST_TABLE[self.scenario]["commission"]
        self.slippage = SlippageModel(self.scenario)

    def _half_spread_frac(self, price: float, bar: Bar, spread_bps: float | None) -> float:
        if spread_bps is None:
            spread_bps = estimate_spread_bps(price, bar.dollar_volume)
        return (spread_bps / 10_000.0) / 2.0

    def build_fill(
        self,
        *,
        symbol: str,
        timestamp: datetime,
        side: Side,
        is_entry: bool,
        reference_price: float,
        shares: float,
        bar: Bar,
        spread_bps: float | None = None,
    ) -> Fill:
        order_value = reference_price * shares
        half_spread = self._half_spread_frac(reference_price, bar, spread_bps)
        slip = self.slippage.slippage_pct(order_value, bar.dollar_volume)

        # Direction of price penalty. Long entry = buy (pay up); long exit = sell (get less).
        buying = (side == Side.LONG and is_entry) or (side == Side.SHORT and not is_entry)
        sign = 1.0 if buying else -1.0
        fill_price = reference_price * (1.0 + sign * (half_spread + slip))

        commission_usd = abs(fill_price * shares) * self.commission_pct
        spread_cost_usd = reference_price * half_spread * shares
        slippage_usd = reference_price * slip * shares

        return Fill(
            symbol=symbol,
            timestamp=timestamp,
            side=side,
            quantity=shares,
            requested_price=reference_price,
            fill_price=fill_price,
            commission_usd=commission_usd,
            slippage_usd=slippage_usd,
            spread_cost_usd=spread_cost_usd,
        )
