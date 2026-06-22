"""Intraday risk management with all session-level guards."""
import logging
from typing import Optional

from app.intraday.config import (
    INTRADAY_RISK_PER_TRADE_PCT,
    INTRADAY_MAX_POSITION_VALUE_PLN,
    INTRADAY_MAX_OPEN_POSITIONS,
    INTRADAY_MAX_TOTAL_EXPOSURE_PCT,
    INTRADAY_MAX_TOTAL_OPEN_RISK_PCT,
    INTRADAY_MAX_SECTOR_EXPOSURE_PCT,
    INTRADAY_MAX_SECTOR_RISK_PCT,
    INTRADAY_MAX_POSITIONS_PER_SECTOR,
    INTRADAY_DAILY_LOSS_LIMIT_PCT,
    INTRADAY_WEEKLY_LOSS_LIMIT_PCT,
    INTRADAY_MAX_DAILY_DRAWDOWN_PCT,
)

log = logging.getLogger(__name__)


class IntradayRiskManager:
    """Session-level risk controls for intraday paper trading."""

    def __init__(
        self,
        equity_pln: float,
        pln_usd_rate: float,
        session_date=None,
    ):
        self.equity = equity_pln
        self.rate = pln_usd_rate
        self.session_date = session_date

    def _usd_to_pln(self, usd: float) -> float:
        return usd * self.rate

    def _pln_to_usd(self, pln: float) -> float:
        return pln / self.rate

    def calc_position_size(
        self,
        entry_price_usd: float,
        stop_price_usd: float,
    ) -> dict:
        """Calculate shares based on risk per trade. Returns sizing dict."""
        risk_per_trade_pln = self.equity * INTRADAY_RISK_PER_TRADE_PCT
        risk_per_share_usd = entry_price_usd - stop_price_usd

        if risk_per_share_usd <= 0:
            return {"valid": False, "reason": "stop >= entry price"}
        if entry_price_usd <= 0:
            return {"valid": False, "reason": "entry price <= 0"}

        risk_per_share_pln = self._usd_to_pln(risk_per_share_usd)
        shares_by_risk = risk_per_trade_pln / risk_per_share_pln

        pos_val_pln = self._usd_to_pln(shares_by_risk * entry_price_usd)

        # Cap by max position value
        if pos_val_pln > INTRADAY_MAX_POSITION_VALUE_PLN:
            shares_by_risk = self._pln_to_usd(INTRADAY_MAX_POSITION_VALUE_PLN) / entry_price_usd
            pos_val_pln = INTRADAY_MAX_POSITION_VALUE_PLN

        shares = max(1, int(shares_by_risk))
        actual_pos_pln = self._usd_to_pln(shares * entry_price_usd)
        actual_risk_pln = self._usd_to_pln(shares * risk_per_share_usd)

        return {
            "valid": True,
            "reason": "",
            "shares": shares,
            "position_value_pln": round(actual_pos_pln, 2),
            "planned_risk_pln": round(actual_risk_pln, 2),
            "risk_per_share_pln": round(risk_per_share_pln, 4),
        }

    def can_open_position(
        self,
        position_value_pln: float,
        planned_risk_pln: float,
        sector: str,
        current_invested_pln: float,
        current_open_risk_pln: float,
        open_positions_count: int,
        sector_invested_pln: float,
        sector_open_risk_pln: float,
        sector_position_count: int,
        daily_pnl_pln: float,
        daily_drawdown_pct: float,
        consecutive_losses_today: int,
        weekly_pnl_pln: float,
        market_regime: str = "NEUTRAL",
    ) -> tuple[bool, str]:
        """Check all risk constraints before allowing a new position."""

        # Max open positions
        if open_positions_count >= INTRADAY_MAX_OPEN_POSITIONS:
            return False, f"Max open positions reached ({INTRADAY_MAX_OPEN_POSITIONS})"

        # Max total exposure
        new_exposure = (current_invested_pln + position_value_pln) / self.equity
        if new_exposure > INTRADAY_MAX_TOTAL_EXPOSURE_PCT:
            return False, f"Total exposure {new_exposure*100:.1f}% would exceed {INTRADAY_MAX_TOTAL_EXPOSURE_PCT*100:.0f}%"

        # Max total open risk
        new_risk = (current_open_risk_pln + planned_risk_pln) / self.equity
        if new_risk > INTRADAY_MAX_TOTAL_OPEN_RISK_PCT:
            return False, f"Total open risk {new_risk*100:.2f}% would exceed {INTRADAY_MAX_TOTAL_OPEN_RISK_PCT*100:.1f}%"

        # Sector exposure
        if sector and sector not in ("ETF", "Unknown"):
            new_sec_exp = (sector_invested_pln + position_value_pln) / self.equity
            if new_sec_exp > INTRADAY_MAX_SECTOR_EXPOSURE_PCT:
                return False, f"Sector {sector} exposure {new_sec_exp*100:.1f}% would exceed limit"
            new_sec_risk = (sector_open_risk_pln + planned_risk_pln) / self.equity
            if new_sec_risk > INTRADAY_MAX_SECTOR_RISK_PCT:
                return False, f"Sector {sector} risk {new_sec_risk*100:.3f}% would exceed limit"
            if sector_position_count >= INTRADAY_MAX_POSITIONS_PER_SECTOR:
                return False, f"Max positions in {sector} reached ({INTRADAY_MAX_POSITIONS_PER_SECTOR})"

        # Daily loss limit
        daily_loss_limit_pln = -self.equity * INTRADAY_DAILY_LOSS_LIMIT_PCT
        if daily_pnl_pln < daily_loss_limit_pln:
            return False, f"Daily loss limit hit ({daily_pnl_pln:.0f} PLN)"

        # Weekly loss limit
        weekly_loss_limit_pln = -self.equity * INTRADAY_WEEKLY_LOSS_LIMIT_PCT
        if weekly_pnl_pln < weekly_loss_limit_pln:
            return False, f"Weekly loss limit hit ({weekly_pnl_pln:.0f} PLN)"

        # Daily drawdown
        if daily_drawdown_pct > INTRADAY_MAX_DAILY_DRAWDOWN_PCT:
            return False, f"Daily drawdown {daily_drawdown_pct*100:.2f}% exceeds limit"

        # Consecutive losses circuit breaker (>=4 = pause new entries)
        if consecutive_losses_today >= 4:
            return False, f"Circuit breaker: {consecutive_losses_today} consecutive losses today"

        # Sufficient cash
        if position_value_pln > (self.equity - current_invested_pln):
            return False, "Insufficient cash"

        return True, "ok"

    def apply_volatility_adjustment(self, shares: int, market_regime: str) -> int:
        """Reduce position size by 50% in high volatility."""
        if market_regime == "HIGH_VOLATILITY":
            return max(1, shares // 2)
        return shares

    def get_daily_loss_status(self, daily_pnl_pln: float) -> dict:
        """Return daily loss usage stats."""
        limit_pln = self.equity * INTRADAY_DAILY_LOSS_LIMIT_PCT
        used_pct = abs(min(0, daily_pnl_pln)) / limit_pln if limit_pln > 0 else 0
        remaining = limit_pln + daily_pnl_pln  # positive = headroom
        return {
            "hit": daily_pnl_pln < -limit_pln,
            "used_pct": round(used_pct * 100, 2),
            "remaining_pln": round(remaining, 2),
        }
