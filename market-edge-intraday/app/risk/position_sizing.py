"""Position sizing.

Sizing is risk-first: we risk a small fixed fraction of equity per trade, then
cap by position value, cash, gross-exposure room, the bar's traded value and
average daily volume. The final share count is the MINIMUM of all caps. If it is
zero or negative the trade is rejected.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings


@dataclass
class SizingResult:
    shares: int
    binding_constraint: str
    risk_amount_pln: float
    breakdown: dict[str, float] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.shares > 0


def compute_position_size(
    *,
    entry_price_usd: float,
    stop_price_usd: float,
    equity_pln: float,
    cash_pln: float,
    usdpln: float,
    bar_dollar_volume_usd: float,
    adv_usd: float | None = None,
    gross_exposure_room_pln: float | None = None,
) -> SizingResult:
    """Return the largest position that respects every risk cap."""
    risk_per_share = entry_price_usd - stop_price_usd
    risk_amount_pln = equity_pln * settings.risk_per_trade_pct
    if risk_per_share <= 0 or entry_price_usd <= 0 or usdpln <= 0:
        return SizingResult(0, "INVALID_PRICES", risk_amount_pln)

    risk_amount_usd = risk_amount_pln / usdpln
    shares_by_risk = risk_amount_usd / risk_per_share

    # Position-value cap (PLN).
    shares_by_value = (settings.max_position_value_pln / usdpln) / entry_price_usd

    # Cash cap (cannot spend more than available cash).
    shares_by_cash = max(0.0, (cash_pln / usdpln) / entry_price_usd)

    caps: dict[str, float] = {
        "risk": shares_by_risk,
        "position_value": shares_by_value,
        "cash": shares_by_cash,
    }

    # Gross-exposure room.
    if gross_exposure_room_pln is not None:
        caps["exposure"] = max(0.0, (gross_exposure_room_pln / usdpln) / entry_price_usd)

    # Bar-volume cap: never take more than X% of the bar's traded value.
    if bar_dollar_volume_usd > 0:
        caps["bar_volume"] = (
            settings.max_position_bar_volume_pct * bar_dollar_volume_usd
        ) / entry_price_usd

    # ADV cap: never more than X% of average daily volume (value).
    if adv_usd and adv_usd > 0:
        caps["adv"] = (settings.max_adv_participation_pct * adv_usd) / entry_price_usd

    binding = min(caps, key=lambda k: caps[k])
    shares = int(max(0.0, min(caps.values())))
    return SizingResult(
        shares=shares,
        binding_constraint=binding,
        risk_amount_pln=risk_amount_pln,
        breakdown={k: round(v, 2) for k, v in caps.items()},
    )
