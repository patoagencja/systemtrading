"""Portfolio risk limits, daily loss guard and kill switch."""
from __future__ import annotations

from app.config import settings
from app.enums import RejectionReason, StrategyName
from app.risk.daily_loss_guard import DailyLossGuard
from app.risk.kill_switch import KillSwitch
from app.risk.portfolio_risk import PortfolioRiskState, RiskManager

EQ = 1_000_000.0


def _state(**kw) -> PortfolioRiskState:
    base = dict(equity_pln=EQ, cash_pln=EQ, universe_symbols={"AAA"})
    base.update(kw)
    return PortfolioRiskState(**base)


def test_duplicate_ticker_rejected():
    rm = RiskManager()
    d = rm.check_new_entry(symbol="AAA", strategy=StrategyName.OPENING_RANGE_BREAKOUT,
                           sector="TECH", state=_state(open_symbols={"AAA"}))
    assert not d.allowed and d.reason == RejectionReason.DUPLICATE_TICKER


def test_pending_ticker_rejected():
    rm = RiskManager()
    d = rm.check_new_entry(symbol="AAA", strategy=StrategyName.OPENING_RANGE_BREAKOUT,
                           sector="TECH", state=_state(pending_symbols={"AAA"}))
    assert not d.allowed and d.reason == RejectionReason.DUPLICATE_TICKER


def test_not_in_universe_rejected():
    rm = RiskManager()
    d = rm.check_new_entry(symbol="ZZZ", strategy=StrategyName.OPENING_RANGE_BREAKOUT,
                           sector="TECH", state=_state())
    assert not d.allowed and d.reason == RejectionReason.NOT_IN_UNIVERSE


def test_max_positions_rejected():
    rm = RiskManager()
    d = rm.check_new_entry(symbol="AAA", strategy=StrategyName.OPENING_RANGE_BREAKOUT,
                           sector="TECH", state=_state(n_open=settings.max_open_positions))
    assert not d.allowed and d.reason == RejectionReason.MAX_POSITIONS


def test_sector_position_count_limit():
    rm = RiskManager()
    d = rm.check_new_entry(
        symbol="AAA", strategy=StrategyName.OPENING_RANGE_BREAKOUT, sector="TECH",
        state=_state(sector_counts={"TECH": settings.max_positions_per_sector}))
    assert not d.allowed and d.reason == RejectionReason.SECTOR_LIMIT


def test_gross_exposure_limit():
    rm = RiskManager()
    d = rm.check_new_entry(
        symbol="AAA", strategy=StrategyName.OPENING_RANGE_BREAKOUT, sector="TECH",
        state=_state(gross_exposure_pln=settings.max_gross_exposure_pct * EQ))
    assert not d.allowed and d.reason == RejectionReason.PORTFOLIO_RISK_LIMIT


def test_sector_exposure_limit():
    rm = RiskManager()
    d = rm.check_new_entry(
        symbol="AAA", strategy=StrategyName.OPENING_RANGE_BREAKOUT, sector="TECH",
        state=_state(sector_exposure_pln={"TECH": settings.max_sector_exposure_pct * EQ}))
    assert not d.allowed and d.reason == RejectionReason.SECTOR_LIMIT


def test_daily_loss_limit_blocks():
    rm = RiskManager()
    loss = -settings.max_daily_loss_pct * EQ - 1
    d = rm.check_new_entry(symbol="AAA", strategy=StrategyName.OPENING_RANGE_BREAKOUT,
                           sector="TECH", state=_state(daily_realized_pnl_pln=loss))
    assert not d.allowed and d.reason == RejectionReason.DAILY_LOSS_LIMIT


def test_allowed_when_within_limits():
    rm = RiskManager()
    d = rm.check_new_entry(symbol="AAA", strategy=StrategyName.OPENING_RANGE_BREAKOUT,
                           sector="TECH", state=_state())
    assert d.allowed
    assert d.gross_exposure_room_pln > 0


def test_daily_loss_guard():
    g = DailyLossGuard(EQ)
    g.record("ORB", -settings.max_daily_loss_pct * EQ - 1)
    assert g.status().blocked


def test_kill_switch_consecutive_losses():
    ks = KillSwitch()
    assert not ks.active
    ks.check_consecutive_losses(settings.max_consecutive_losses)
    assert ks.active
    ks.reset()
    assert not ks.active


def test_kill_switch_equity_jump():
    ks = KillSwitch()
    ks.check_equity_jump(1_000_000, 2_000_000)
    assert ks.active


def test_kill_switch_positions_not_flat_after_eod():
    ks = KillSwitch()
    ks.check_positions_not_flat_after_eod(3)
    assert ks.active
