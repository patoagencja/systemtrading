"""Kill switch — trips on anomalies or repeated failures and freezes trading.

When tripped: no new positions are opened, the reason is recorded, an alert is
sent, and (in live paper) open positions are flattened by a safe procedure. A
new session or a manual reset is required to resume.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.config import settings
from app.logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class KillSwitch:
    active: bool = False
    reason: str = ""
    triggers: list[str] = field(default_factory=list)

    def trip(self, reason: str) -> None:
        if not self.active:
            self.active = True
            self.reason = reason
            logger.critical("KILL SWITCH TRIPPED: %s", reason)
        self.triggers.append(reason)

    def reset(self) -> None:
        self.active = False
        self.reason = ""
        self.triggers.clear()

    # --- anomaly checks ----------------------------------------------------
    def check_consecutive_losses(self, n: int) -> bool:
        if n >= settings.max_consecutive_losses:
            self.trip(f"consecutive losses >= {settings.max_consecutive_losses} ({n})")
            return True
        return False

    def check_daily_loss(self, daily_pnl_pln: float, equity_pln: float) -> bool:
        if daily_pnl_pln <= -settings.max_daily_loss_pct * equity_pln:
            self.trip("daily loss limit exceeded")
            return True
        return False

    def check_equity_jump(self, prev_equity: float, new_equity: float, max_jump: float = 0.25) -> bool:
        """Unrealistic single-step equity move suggests bad data / a bug."""
        if prev_equity > 0 and abs(new_equity - prev_equity) / prev_equity > max_jump:
            self.trip(f"unrealistic equity jump {prev_equity:.0f} -> {new_equity:.0f}")
            return True
        return False

    def check_data_errors(self, consecutive_api_errors: int, threshold: int = 5) -> bool:
        if consecutive_api_errors >= threshold:
            self.trip(f"data API errors >= {threshold}")
            return True
        return False

    def check_signal_flood(self, signals_this_bar: int, threshold: int = 200) -> bool:
        if signals_this_bar >= threshold:
            self.trip(f"abnormal signal count this bar ({signals_this_bar})")
            return True
        return False

    def check_open_positions(self, n_open: int) -> bool:
        if n_open > settings.max_open_positions:
            self.trip(f"open positions exceeded hard limit ({n_open})")
            return True
        return False

    def check_positions_not_flat_after_eod(self, n_open: int) -> bool:
        if n_open > 0:
            self.trip(f"positions still open after EOD flatten ({n_open})")
            return True
        return False
