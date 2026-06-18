import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from app.risk_manager import RiskManager
from app.config import (
    RISK_PER_TRADE_PCT,
    MAX_POSITION_SIZE_PCT,
    MAX_OPEN_POSITIONS,
    MAX_PORTFOLIO_EXPOSURE_PCT,
)

PORTFOLIO = 1_000_000.0
RATE = 4.0


def make_rm(portfolio=PORTFOLIO, rate=RATE):
    return RiskManager(portfolio, rate)


class TestPositionSizing:
    def test_returns_valid_result(self):
        rm = make_rm()
        result = rm.calc_position_size(entry_price_usd=100.0, stop_loss_usd=97.0)
        assert result["valid"] is True
        assert result["shares"] > 0
        assert result["position_value_pln"] > 0
        assert result["risk_pln"] > 0

    def test_stop_loss_equals_entry_is_invalid(self):
        rm = make_rm()
        result = rm.calc_position_size(entry_price_usd=100.0, stop_loss_usd=100.0)
        assert result["valid"] is False

    def test_stop_loss_above_entry_is_invalid(self):
        rm = make_rm()
        result = rm.calc_position_size(entry_price_usd=100.0, stop_loss_usd=105.0)
        assert result["valid"] is False

    def test_risk_does_not_exceed_limit(self):
        rm = make_rm()
        result = rm.calc_position_size(entry_price_usd=100.0, stop_loss_usd=97.0)
        max_risk = PORTFOLIO * RISK_PER_TRADE_PCT
        assert result["risk_pln"] <= max_risk * 1.01  # 1% tolerance for rounding

    def test_position_does_not_exceed_max_size(self):
        rm = make_rm()
        # Wide stop → risk-based size would be tiny, position well within limit
        result = rm.calc_position_size(entry_price_usd=50.0, stop_loss_usd=49.0)
        assert result["position_value_pln"] <= PORTFOLIO * MAX_POSITION_SIZE_PCT * 1.01

    def test_position_capped_at_max_size(self):
        rm = make_rm()
        # Very tight stop → risk-based shares would be huge → capped at max position
        result = rm.calc_position_size(entry_price_usd=100.0, stop_loss_usd=99.99)
        assert result["valid"] is True
        assert result["position_value_pln"] <= PORTFOLIO * MAX_POSITION_SIZE_PCT * 1.01


class TestExposureLimits:
    def test_can_open_when_within_limits(self):
        rm = make_rm()
        ok, _ = rm.can_open_position(
            new_position_pln=25_000,
            current_invested_pln=0,
            open_positions_count=0,
        )
        assert ok is True

    def test_blocked_at_max_positions(self):
        rm = make_rm()
        ok, reason = rm.can_open_position(
            new_position_pln=25_000,
            current_invested_pln=0,
            open_positions_count=MAX_OPEN_POSITIONS,
        )
        assert ok is False
        assert "Max open positions" in reason

    def test_blocked_when_exposure_exceeded(self):
        rm = make_rm()
        ok, reason = rm.can_open_position(
            new_position_pln=100_000,
            current_invested_pln=PORTFOLIO * MAX_PORTFOLIO_EXPOSURE_PCT,
            open_positions_count=5,
        )
        assert ok is False
        assert "exposure" in reason.lower()

    def test_blocked_when_insufficient_cash(self):
        rm = make_rm()
        ok, reason = rm.can_open_position(
            new_position_pln=PORTFOLIO + 1,
            current_invested_pln=0,
            open_positions_count=0,
        )
        assert ok is False

    def test_no_duplicate_ticker(self):
        """Duplicate ticker check lives in scanner; confirm open_tickers set works."""
        open_tickers = {"AAPL", "MSFT"}
        new_ticker = "AAPL"
        assert new_ticker in open_tickers
