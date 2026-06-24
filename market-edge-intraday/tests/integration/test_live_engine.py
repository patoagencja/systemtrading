"""Live engine: backtest/live separation, persistence, resume, stale-data safety."""
from __future__ import annotations

from datetime import UTC, date, datetime

from app.database import session_scope
from app.engine.scanner import ScanResult
from app.enums import RunMode
from app.models import PortfolioSnapshot, Trade, UniverseSnapshot
from tests.conftest import trending_session


def _seed_universe(symbols, d=date(2024, 3, 1)):
    with session_scope() as s:
        for i, sym in enumerate(symbols):
            s.add(UniverseSnapshot(session_date=d, symbol=sym, rank=i + 1,
                                   avg_daily_volume=5e6, avg_dollar_volume=5e8,
                                   price=100.0, sector="TECH", asset_type="COMMON_STOCK",
                                   inclusion_reason="test"))


def test_backtest_and_live_streams_are_separated():
    from app.backtest.engine import BacktestConfig, run_backtest
    from app.data import get_provider
    from app.engine.session_manager import persist_backtest_result

    prov = get_provider("fixture")
    bars = prov.get_historical_bars([a["symbol"] for a in prov.get_assets()],
                                    datetime(2024, 1, 2, tzinfo=UTC),
                                    datetime(2024, 1, 31, tzinfo=UTC), "15Min")
    cfg = BacktestConfig(symbols=["AAPL", "MSFT", "NVDA"], start=date(2024, 1, 2),
                         end=date(2024, 1, 31), strategies=[], min_score=55)
    res = run_backtest(cfg, bars_by_symbol=bars)
    persist_backtest_result(res, RunMode.BACKTEST)

    with session_scope() as s:
        bt = s.query(Trade).filter(Trade.run_mode == RunMode.BACKTEST.value).count()
        live = s.query(Trade).filter(Trade.run_mode == RunMode.LIVE_PAPER.value).count()
    assert bt > 0
    assert live == 0  # nothing leaked into the live stream


def test_resume_equity_from_last_snapshot():
    from app.engine.live_engine import LiveEngine

    with session_scope() as s:
        s.add(PortfolioSnapshot(run_mode=RunMode.LIVE_PAPER.value,
                                timestamp=datetime(2024, 3, 1, 20, tzinfo=UTC),
                                session_date=date(2024, 3, 1), cash_pln=990_000,
                                invested_pln=0, equity_pln=1_005_000, daily_pnl=5000,
                                total_pnl=5000, exposure=0, open_risk=0, drawdown=0,
                                open_positions=0))
    eng = LiveEngine(provider=__import__("app.data", fromlist=["get_provider"]).get_provider("fixture"))
    assert abs(eng._resume_equity_pln() - 1_005_000) < 1e-6


def test_on_tick_persists_to_live_stream(monkeypatch):
    from app.data import get_provider
    from app.engine import live_engine as le

    _seed_universe(["AAA"])
    eng = le.LiveEngine(provider=get_provider("fixture"))

    # Controlled, in-session data: AAA trends, SPY flat. Last bar = chosen ts.
    aaa = trending_session(symbol="AAA", n=10)
    spy = trending_session(symbol="SPY", base=400, step=0.0, n=10)
    ts = aaa.index[-1]

    def fake_fetch(symbols, lookback=60, now=None):
        bars = {"AAA": aaa, "SPY": spy}
        return ScanResult(bars={k: v for k, v in bars.items() if k in symbols}, issues={})

    monkeypatch.setattr(eng.scanner, "fetch_latest", fake_fetch)
    monkeypatch.setattr(le, "get_usdpln", lambda: 4.0)
    monkeypatch.setattr("app.data.session_calendar.is_trading_day", lambda d: True)

    assert eng.prepare_session(ts.date())
    eng.on_tick(now=ts.to_pydatetime())

    with session_scope() as s:
        snaps = s.query(PortfolioSnapshot).filter(
            PortfolioSnapshot.run_mode == RunMode.LIVE_PAPER.value).count()
    assert snaps >= 1  # a snapshot was written for the live stream


def test_on_tick_skips_on_stale_benchmark(monkeypatch):
    from app.data import get_provider
    from app.engine import live_engine as le

    _seed_universe(["AAA"])
    eng = le.LiveEngine(provider=get_provider("fixture"))

    def empty_fetch(symbols, lookback=60, now=None):
        return ScanResult(bars={}, issues={"SPY": ["stale"]})

    monkeypatch.setattr(eng.scanner, "fetch_latest", empty_fetch)
    monkeypatch.setattr(le, "get_usdpln", lambda: 4.0)
    monkeypatch.setattr("app.data.session_calendar.is_trading_day", lambda d: True)
    eng.prepare_session(date(2024, 3, 1))
    # Should not raise even though the benchmark is missing/stale.
    eng.on_tick(now=datetime(2024, 3, 1, 16, 0, tzinfo=UTC))
