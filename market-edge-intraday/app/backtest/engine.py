"""Historical backtester — orchestrates day-by-day session simulation.

It reuses the SAME strategies, risk manager, paper broker and fill model as live
paper trading (via :mod:`app.backtest.event_simulator`). There is no second copy
of the trading logic. Positions are isolated per session (never held overnight),
which makes day-by-day processing both correct and fast.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from app.config import settings
from app.domain import ClosedTrade
from app.enums import CostScenario, RunMode, StrategyName
from app.logging_config import get_logger
from app.risk.kill_switch import KillSwitch
from app.risk.portfolio_risk import RiskManager
from app.strategies.strategy_registry import build_strategies

logger = get_logger(__name__)
ET = ZoneInfo("America/New_York")


@dataclass
class BacktestConfig:
    symbols: list[str]
    start: date
    end: date
    strategies: list[str] = field(default_factory=list)
    cost_scenario: str = "BASE"
    initial_capital_pln: float = 1_000_000.0
    min_score: float = 75.0
    benchmark_symbol: str = "SPY"
    usdpln: float | None = None


@dataclass
class BacktestResult:
    config: BacktestConfig
    trades: list[ClosedTrade]
    equity_curve: pd.DataFrame
    metrics: dict
    per_strategy: dict
    signals: list = field(default_factory=list)
    sessions: list = field(default_factory=list)
    data_range: tuple[date, date] | None = None
    run_mode: str = RunMode.BACKTEST.value
    inconclusive: bool = False


def _ensure_utc_index(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    elif df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    return df.sort_index()


def _group_by_session(df: pd.DataFrame) -> dict[date, pd.DataFrame]:
    """Split a symbol's 15-min bars into regular-session day slices (ET)."""
    df = _ensure_utc_index(df)
    et = df.index.tz_convert(ET)
    mask = (et.time >= settings.session_open) & (et.time < settings.session_close)
    df = df[mask]
    if df.empty:
        return {}
    et_dates = df.index.tz_convert(ET).date
    out: dict[date, pd.DataFrame] = {}
    for d, sub in df.groupby(et_dates):
        out[d] = sub
    return out


def _load_bars(config: BacktestConfig) -> dict[str, pd.DataFrame]:
    """Load 15-min bars for all symbols (+benchmark) from cache, else provider."""
    from app.data.bar_cache import has_bars, load_bars

    symbols = list(dict.fromkeys([*config.symbols, config.benchmark_symbol]))
    start_dt = datetime.combine(config.start, datetime.min.time(), tzinfo=UTC)
    end_dt = datetime.combine(config.end, datetime.max.time(), tzinfo=UTC)

    bars: dict[str, pd.DataFrame] = {}
    missing: list[str] = []
    for sym in symbols:
        if has_bars(sym):
            df = load_bars(sym, start=start_dt, end=end_dt)
            if df is not None and not df.empty:
                bars[sym] = df
                continue
        missing.append(sym)

    if missing:
        from app.data import get_provider

        provider = get_provider()
        fetched = provider.get_historical_bars(missing, start_dt, end_dt, timeframe="15Min")
        for sym, df in fetched.items():
            if df is not None and not df.empty:
                bars[sym] = df
    return bars


def run_backtest(
    config: BacktestConfig,
    bars_by_symbol: dict[str, pd.DataFrame] | None = None,
) -> BacktestResult:
    """Run the backtest. Provide ``bars_by_symbol`` to skip data loading (tests)."""
    cost = CostScenario(config.cost_scenario)
    strat_names = [StrategyName(s) for s in config.strategies] if config.strategies else None
    strategies = build_strategies(strat_names)

    bars = bars_by_symbol if bars_by_symbol is not None else _load_bars(config)
    if not bars:
        raise RuntimeError(
            "No market data available for the backtest. This system never "
            "fabricates data. Provide bars, populate the Parquet cache, or set a "
            "valid market-data API key (see README 'How to get an API key')."
        )

    benchmark = bars.get(config.benchmark_symbol)
    benchmark_sessions = _group_by_session(benchmark) if benchmark is not None else {}

    # Sector ETF mapping for relative-strength.
    from app.universe.sector_mapping import default_sector, sector_etf

    sector_of = {s: default_sector(s) for s in config.symbols}
    sector_etf_of = {s: sector_etf(sec) for s, sec in sector_of.items()}
    etf_symbols = set(sector_etf_of.values())

    symbol_sessions = {s: _group_by_session(df) for s, df in bars.items()}
    etf_sessions = {e: symbol_sessions.get(e, {}) for e in etf_symbols}

    all_days = sorted({d for sess in symbol_sessions.values() for d in sess})
    all_days = [d for d in all_days if config.start <= d <= config.end]
    if not all_days:
        raise RuntimeError("No trading sessions found in the requested date range.")

    broker = _make_broker(config, cost)
    risk_manager = RiskManager()
    kill_switch = KillSwitch()
    universe_symbols = set(config.symbols)

    from app.backtest.event_simulator import simulate_session

    equity_rows: list = []
    all_signals: list = []
    sessions: list = []
    prev_close_by_symbol: dict[str, float] = {}

    sessions_halted = 0
    for day in all_days:
        day_bars = {
            s: sess[day]
            for s, sess in symbol_sessions.items()
            if s in universe_symbols and day in sess and not sess[day].empty
        }
        if not day_bars:
            continue
        # The kill switch and consecutive-loss circuit breaker are per-session
        # safety controls. In a research backtest we reset them at the start of
        # each session so one bad day cannot abort the whole study; in LIVE_PAPER
        # the kill switch persists and requires a manual reset.
        kill_switch.reset()
        broker.consecutive_losses = 0
        bench_day = benchmark_sessions.get(day)
        sector_day = {e: etf_sessions[e].get(day) for e in etf_symbols if etf_sessions[e].get(day) is not None}

        res = simulate_session(
            session_date=day,
            broker=broker,
            strategies=strategies,
            risk_manager=risk_manager,
            kill_switch=kill_switch,
            day_bars=day_bars,
            benchmark_bars=bench_day,
            sector_bars=sector_day,
            prev_closes=dict(prev_close_by_symbol),
            universe_symbols=universe_symbols,
            sector_of=sector_of,
            sector_etf_of=sector_etf_of,
            min_score=config.min_score,
            run_mode=RunMode.BACKTEST,
            equity_rows=equity_rows,
        )
        sessions.append(res)
        all_signals.extend(res.signals)
        # Carry the day's last close as the next day's prev-close (gap input).
        for s, df in day_bars.items():
            prev_close_by_symbol[s] = float(df["close"].iloc[-1])
        if res.kill_switch_reason:
            sessions_halted += 1
            logger.warning("Session %s halted by kill switch: %s", day, res.kill_switch_reason)

    equity_curve = pd.DataFrame(equity_rows)
    from app.analytics.metrics import compute_metrics

    metrics = compute_metrics(
        broker.closed_trades, equity_curve,
        initial_capital=config.initial_capital_pln, run_mode=RunMode.BACKTEST.value,
    )
    per_strategy = _per_strategy_metrics(broker.closed_trades, config.initial_capital_pln)

    result = BacktestResult(
        config=config,
        trades=broker.closed_trades,
        equity_curve=equity_curve,
        metrics=metrics,
        per_strategy=per_strategy,
        signals=all_signals,
        sessions=sessions,
        data_range=(all_days[0], all_days[-1]),
    )
    # Mark inconclusive if the sample is too small to mean anything.
    if metrics.get("num_trades", 0) < 100:
        result.inconclusive = True
    return result


def _make_broker(config: BacktestConfig, cost: CostScenario):
    from app.execution.paper_broker import PaperBroker

    return PaperBroker(
        run_mode=RunMode.BACKTEST,
        initial_capital_pln=config.initial_capital_pln,
        usdpln=config.usdpln or settings.fallback_usdpln,
        cost_scenario=cost,
    )


def _per_strategy_metrics(trades: list[ClosedTrade], initial_capital: float) -> dict:
    from app.analytics.metrics import compute_metrics

    out: dict[str, dict] = {}
    by_strat: dict[str, list[ClosedTrade]] = {}
    for t in trades:
        by_strat.setdefault(str(t.strategy), []).append(t)
    for name, ts in by_strat.items():
        out[name] = compute_metrics(ts, None, initial_capital=initial_capital)
    return out
