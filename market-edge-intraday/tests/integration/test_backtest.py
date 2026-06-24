"""End-to-end backtest: no look-ahead, EOD flatten, no overnight, separation."""
from __future__ import annotations

from datetime import UTC, date, datetime

from app.backtest.engine import BacktestConfig, run_backtest
from app.backtest.event_simulator import process_bar
from app.enums import RunMode, StrategyName
from app.risk.kill_switch import KillSwitch
from app.risk.portfolio_risk import RiskManager
from app.strategies.strategy_registry import build_strategies


def _fixture_bars():
    from app.data import get_provider

    prov = get_provider("fixture")
    start = datetime(2024, 1, 2, tzinfo=UTC)
    end = datetime(2024, 2, 15, tzinfo=UTC)
    syms = [a["symbol"] for a in prov.get_assets()]
    return prov.get_historical_bars(syms, start, end, "15Min"), syms


def test_signal_on_close_T_fill_on_open_T_plus_1():
    """A signal formed on bar T must fill at bar T+1's open — never on bar T."""
    from app.execution.paper_broker import PaperBroker
    from tests.conftest import make_bars

    # Hand-crafted opening-range breakout: bars 0-1 form a tight opening range,
    # bar 2 closes above it on a volume spike (a clean ORB long), bar 3 opens at
    # ~bar-2 close so the entry is not chasing a gap.
    #          0       1       2       3       4       5       6       7
    opens =  [100.0, 100.10, 100.30, 100.62, 100.70, 100.80, 100.90, 101.00]
    closes = [100.10, 100.20, 100.60, 100.70, 100.80, 100.90, 101.00, 101.10]
    highs =  [100.20, 100.30, 100.66, 100.78, 100.88, 100.98, 101.08, 101.18]
    lows =   [99.95, 100.02, 100.25, 100.58, 100.66, 100.76, 100.86, 100.96]
    vols =   [2_000_000, 2_000_000, 6_000_000, 3_000_000,
              3_000_000, 3_000_000, 3_000_000, 3_000_000]
    bars = make_bars(opens, highs, lows, closes, vols, symbol="AAA")
    bench = make_bars([400.0] * 8, [400.1] * 8, [399.9] * 8, [400.0] * 8,
                      [5_000_000] * 8, symbol="SPY")
    broker = PaperBroker(RunMode.BACKTEST, 1_000_000, 4.0)
    strategies = build_strategies([StrategyName.RELATIVE_STRENGTH_MOMENTUM,
                                   StrategyName.OPENING_RANGE_BREAKOUT,
                                   StrategyName.VOLUME_EXPANSION_MOMENTUM])
    rm, ks = RiskManager(), KillSwitch()

    pending: list = []
    opened_at_bar = None
    signal_bar_ts = None
    grid = list(bars.index)
    for i, ts in enumerate(grid):
        day_bars = {"AAA": bars.loc[:ts]}
        res = process_bar(
            ts=ts, session_bar_index=i, bars_remaining=len(grid) - i - 1,
            is_last_bar=(i == len(grid) - 1), session_date=ts.date(), broker=broker,
            strategies=strategies, risk_manager=rm, kill_switch=ks,
            incoming_pending=pending, day_bars=day_bars, benchmark_bars=bench.loc[:ts],
            sector_bars={}, prev_closes={}, universe_symbols={"AAA"},
            sector_of={"AAA": "TECH"}, sector_etf_of={"AAA": "XLK"}, min_score=0.0,
        )
        if pending and signal_bar_ts is None:
            signal_bar_ts = pending[0].created_at
        if res.opened and opened_at_bar is None:
            opened_at_bar = res.opened[0].opened_at
            # The fill timestamp must be strictly after the signal bar.
            assert opened_at_bar > signal_bar_ts
            # And the fill reference equals THIS bar's open (T+1 open).
            assert abs(res.opened[0].raw_entry_price - float(bars.loc[ts, "open"])) < 1e-9
        pending = res.pending
    assert opened_at_bar is not None, "expected at least one entry in the trend"


def test_no_overnight_positions_and_eod_flatten():
    bars, syms = _fixture_bars()
    cfg = BacktestConfig(symbols=[s for s in syms if s not in ("SPY", "XLK")],
                         start=date(2024, 1, 2), end=date(2024, 2, 15),
                         strategies=[], cost_scenario="BASE", min_score=55)
    res = run_backtest(cfg, bars_by_symbol=bars)
    assert res.metrics["num_trades"] > 0
    # No trade may span more than one calendar (ET) session.
    cross = [t for t in res.trades if t.entry_time.date() != t.exit_time.date()]
    assert cross == []
    # Every position is closed (each trade has an exit).
    assert all(t.exit_time is not None for t in res.trades)
    # Some trades exit via the EOD flatten path.
    reasons = {str(t.exit_reason) for t in res.trades}
    assert "END_OF_DAY_FLATTEN" in reasons


def test_backtest_is_deterministic_idempotent():
    bars, syms = _fixture_bars()
    cfg = BacktestConfig(symbols=[s for s in syms if s not in ("SPY", "XLK")],
                         start=date(2024, 1, 2), end=date(2024, 1, 31),
                         strategies=[], cost_scenario="BASE", min_score=60)
    r1 = run_backtest(cfg, bars_by_symbol=bars)
    r2 = run_backtest(cfg, bars_by_symbol=bars)
    assert r1.metrics["num_trades"] == r2.metrics["num_trades"]
    assert abs(r1.metrics["net_pnl"] - r2.metrics["net_pnl"]) < 1e-6


def test_no_duplicate_open_positions_per_ticker():
    bars, syms = _fixture_bars()
    cfg = BacktestConfig(symbols=[s for s in syms if s not in ("SPY", "XLK")],
                         start=date(2024, 1, 2), end=date(2024, 1, 31),
                         strategies=[], cost_scenario="BASE", min_score=55)
    res = run_backtest(cfg, bars_by_symbol=bars)
    # Within any single instant a ticker can hold at most one open position; here
    # we assert no overlapping open windows for the same symbol.
    by_symbol: dict[str, list] = {}
    for t in res.trades:
        by_symbol.setdefault(t.symbol, []).append((t.entry_time, t.exit_time))
    for windows in by_symbol.values():
        windows.sort()
        for (_s1, e1), (s2, _e2) in zip(windows, windows[1:], strict=False):
            assert e1 <= s2  # no overlap


def test_stress_costs_reduce_pnl_vs_low():
    bars, syms = _fixture_bars()
    symbols = [s for s in syms if s not in ("SPY", "XLK")]
    base = dict(symbols=symbols, start=date(2024, 1, 2), end=date(2024, 2, 15),
                strategies=[], min_score=55)
    low = run_backtest(BacktestConfig(cost_scenario="LOW", **base), bars_by_symbol=bars)
    stress = run_backtest(BacktestConfig(cost_scenario="STRESS", **base), bars_by_symbol=bars)
    assert stress.metrics["net_pnl"] <= low.metrics["net_pnl"]
