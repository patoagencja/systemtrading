from app.config import (
    RISK_PER_TRADE_PCT,
    MAX_POSITION_SIZE_PCT,
    MAX_OPEN_POSITIONS,
    MAX_PORTFOLIO_EXPOSURE_PCT,
)


class RiskManager:
    def __init__(self, portfolio_value_pln: float, pln_usd_rate: float):
        self.portfolio_value = portfolio_value_pln
        self.rate = pln_usd_rate

    def usd_to_pln(self, usd: float) -> float:
        return usd * self.rate

    def pln_to_usd(self, pln: float) -> float:
        return pln / self.rate

    def calc_position_size(
        self,
        entry_price_usd: float,
        stop_loss_usd: float,
    ) -> dict:
        risk_pln = self.portfolio_value * RISK_PER_TRADE_PCT
        max_pos_pln = self.portfolio_value * MAX_POSITION_SIZE_PCT

        risk_per_share_usd = entry_price_usd - stop_loss_usd
        if risk_per_share_usd <= 0:
            return {"valid": False, "reason": "stop_loss >= entry"}

        risk_per_share_pln = self.usd_to_pln(risk_per_share_usd)
        shares_by_risk = risk_pln / risk_per_share_pln

        position_value_pln = self.usd_to_pln(shares_by_risk * entry_price_usd)
        if position_value_pln > max_pos_pln:
            shares_by_risk = self.pln_to_usd(max_pos_pln) / entry_price_usd
            position_value_pln = max_pos_pln

        shares = max(1.0, round(shares_by_risk, 4))
        actual_pos_pln = self.usd_to_pln(shares * entry_price_usd)
        actual_risk_pln = self.usd_to_pln(shares * risk_per_share_usd)

        return {
            "valid": True,
            "shares": shares,
            "position_value_pln": actual_pos_pln,
            "risk_pln": actual_risk_pln,
        }

    def can_open_position(
        self,
        new_position_pln: float,
        current_invested_pln: float,
        open_positions_count: int,
    ) -> tuple[bool, str]:
        if open_positions_count >= MAX_OPEN_POSITIONS:
            return False, f"Max open positions reached ({MAX_OPEN_POSITIONS})"

        projected_exposure = (current_invested_pln + new_position_pln) / self.portfolio_value
        if projected_exposure > MAX_PORTFOLIO_EXPOSURE_PCT:
            return False, f"Max portfolio exposure would be exceeded ({projected_exposure*100:.1f}%)"

        if new_position_pln > self.portfolio_value - current_invested_pln:
            return False, "Insufficient cash"

        return True, "ok"
