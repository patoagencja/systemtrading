"""P&L / drawdown correctness and data-quality gates."""
from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from app.analytics.metrics import compute_metrics
from app.data.data_validator import validate_bars
from app.domain import ClosedTrade
from app.enums import ExitReason, Side, StrategyName
from tests.conftest import make_bars

TS = datetime(2024, 3, 1, 15, 0, tzinfo=UTC)


def _trade(net_pln, r=1.0):
    return ClosedTrade(
        symbol="X", strategy=StrategyName.OPENING_RANGE_BREAKOUT, side=Side.LONG,
        entry_time=TS, entry_price=100, exit_time=TS, exit_price=101, shares=10,
        stop_price=99, target_price=103, gross_pnl_usd=net_pln / 4, net_pnl_usd=net_pln / 4,
        net_pnl_pln=net_pln, return_pct=0.01, r_multiple=r, holding_bars=3,
        exit_reason=ExitReason.TAKE_PROFIT, costs_usd=1.0, slippage_usd=0.5,
        usdpln_entry=4.0, usdpln_exit=4.0, fx_pnl_pln=0.0,
    )


def test_pnl_aggregation():
    trades = [_trade(100), _trade(-50), _trade(200)]
    m = compute_metrics(trades, None, initial_capital=1_000_000)
    assert m["num_trades"] == 3
    assert abs(m["net_pnl"] - 250) < 1e-6
    assert abs(m["win_rate"] - 2 / 3) < 1e-6
    # PF = (100+200) / 50 = 6
    assert abs(m["profit_factor"] - 6.0) < 1e-6


def test_drawdown_calculation():
    eq = pd.DataFrame({
        "timestamp": pd.date_range("2024-03-01", periods=4, freq="D", tz="UTC"),
        "equity_pln": [1_000_000, 1_100_000, 900_000, 950_000],
    })
    m = compute_metrics([_trade(1)], eq, initial_capital=1_000_000)
    # Peak 1.1M -> trough 0.9M = -18.18%.
    assert abs(m["max_drawdown"] - (-0.18181818)) < 1e-4


def test_empty_metrics_safe():
    m = compute_metrics([], None, initial_capital=1_000_000)
    assert m["num_trades"] == 0
    assert m["profit_factor"] == 0.0


def test_validate_rejects_stale_data():
    bars = make_bars(opens=[100, 101], volumes=[1e6, 1e6])
    now = datetime(2024, 3, 5, tzinfo=UTC)  # days later -> stale
    vr = validate_bars(bars, "X", max_staleness_seconds=1200, now=now)
    assert not vr.ok


def test_validate_rejects_zero_volume():
    bars = make_bars(opens=[100, 101], volumes=[1e6, 0])
    now = bars.index[-1].to_pydatetime() + pd.Timedelta(minutes=16).to_pytimedelta()
    vr = validate_bars(bars, "X", max_staleness_seconds=100000, now=now)
    assert not vr.ok


def test_validate_rejects_empty():
    vr = validate_bars(pd.DataFrame(), "X")
    assert not vr.ok
