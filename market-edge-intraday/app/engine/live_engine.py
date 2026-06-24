"""Live paper-trading engine.

Runs on real, live market data but executes ONLY against the virtual paper
broker — it never sends a real order. On each 15-minute tick it calls the exact
same :func:`app.backtest.event_simulator.process_bar` used by the backtester, so
the live behaviour matches the research behaviour.

Daily flow (America/New_York):
  * 08:30–09:20  pre-session checks, build universe, reset limits, open a session
  * 09:30–10:00  collect bars / build opening range (strategies self-gate)
  * 10:00+       every 15 min: manage positions, then generate signals
  * 15:15        stop opening new positions (LAST_NEW_ENTRY_TIME_ET)
  * 15:45        begin flattening; 15:50 everything must be flat
  * 16:00+       finalise the session, snapshot, summary
"""
from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pandas as pd

from app.alerts import AlertManager
from app.config import settings
from app.data import get_provider
from app.engine import session_manager as sm
from app.engine.scanner import MarketScanner, get_usdpln
from app.enums import RunMode, SessionStatus, Severity
from app.logging_config import configure_logging, get_logger
from app.risk.kill_switch import KillSwitch
from app.risk.portfolio_risk import RiskManager
from app.strategies.strategy_registry import build_strategies

logger = get_logger(__name__)
ET = ZoneInfo("America/New_York")


class LiveEngine:
    def __init__(self, provider=None, run_mode: RunMode = RunMode.LIVE_PAPER):
        self.run_mode = run_mode
        self.provider = provider or get_provider()
        self.scanner = MarketScanner(self.provider)
        self.strategies = build_strategies()
        self.risk_manager = RiskManager()
        self.kill_switch = KillSwitch()
        self.alerts = AlertManager()
        self.benchmark = settings.benchmark_symbol

        self.universe_symbols: set[str] = set()
        self.sector_of: dict[str, str] = {}
        self.sector_etf_of: dict[str, str] = {}
        self.prev_closes: dict[str, float] = {}
        self.pending: list = []
        self.session_id: int | None = None
        self.session_date: date | None = None
        self.broker = None
        self._counts = {"signals_generated": 0, "trades_opened": 0, "trades_closed": 0}

    # ------------------------------------------------------------------ setup
    def _resume_equity_pln(self) -> float:
        from app.database import session_scope
        from app.models import PortfolioSnapshot

        try:
            with session_scope() as s:
                row = (
                    s.query(PortfolioSnapshot)
                    .filter(PortfolioSnapshot.run_mode == self.run_mode.value)
                    .order_by(PortfolioSnapshot.timestamp.desc())
                    .first()
                )
                if row:
                    return float(row.equity_pln)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not resume equity (%s); using initial capital", exc)
        return settings.initial_capital_pln

    def prepare_session(self, today: date | None = None) -> bool:
        from app.execution.paper_broker import PaperBroker
        from app.universe.sector_mapping import default_sector, sector_etf
        from app.universe.universe_repository import latest_universe

        today = today or datetime.now(ET).date()
        self.session_date = today
        self.pending = []
        self._counts = {"signals_generated": 0, "trades_opened": 0, "trades_closed": 0}

        # 1) universe
        symbols = latest_universe()
        if not symbols:
            logger.warning("No stored universe; build one with scripts/build_universe.py")
            sm.log_event(Severity.WARNING, "no_universe", "No universe available for today")
            return False
        self.universe_symbols = set(symbols)
        self.sector_of = {s: default_sector(s) for s in symbols}
        self.sector_etf_of = {s: sector_etf(sec) for s, sec in self.sector_of.items()}

        # 2) FX + equity resume
        usdpln = get_usdpln()
        equity = self._resume_equity_pln()
        self.broker = PaperBroker(
            run_mode=self.run_mode, initial_capital_pln=equity, usdpln=usdpln,
            cost_scenario=settings.cost_scenario,
        )
        self.broker.reset_daily()
        self.kill_switch.reset()

        # 3) previous-session closes (for overnight gap)
        self._load_prev_closes(symbols + [self.benchmark])

        # 4) session row + heartbeat
        self.session_id = sm.create_session(self.run_mode, today)
        sm.save_heartbeat("worker", "OK", {"phase": "prepared", "universe": len(symbols)})
        sm.log_event(Severity.INFO, "session_prepared",
                     f"Session {today} prepared: {len(symbols)} symbols, USD/PLN={usdpln:.4f}")
        logger.info("Session prepared for %s with %d symbols (USD/PLN=%.4f)",
                    today, len(symbols), usdpln)
        return True

    def _load_prev_closes(self, symbols: list[str]) -> None:
        try:
            res = self.scanner.fetch_latest(symbols, lookback=120)
            for sym, df in res.bars.items():
                idx = df.index.tz_convert(ET)
                prior = df[idx.date < self.session_date]
                if not prior.empty:
                    self.prev_closes[sym] = float(prior["close"].iloc[-1])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not load previous closes: %s", exc)

    # ------------------------------------------------------------------ tick
    def on_tick(self, now: datetime | None = None) -> None:
        from app.data.session_calendar import is_trading_day

        now = now or datetime.now(UTC)
        today = now.astimezone(ET).date()
        if not is_trading_day(today):
            return
        if (self.broker is None or self.session_date != today) and not self.prepare_session(today):
            return

        symbols = list(self.universe_symbols) + [self.benchmark]
        scan = self.scanner.fetch_latest(symbols, lookback=60, now=now)
        bench_df = scan.bars.get(self.benchmark)
        if bench_df is None or bench_df.empty:
            sm.log_event(Severity.ERROR, "benchmark_stale",
                         "Benchmark data missing/stale — skipping tick")
            self.kill_switch.check_data_errors(self._data_error_count())
            return

        ts = bench_df.index[-1]  # last COMPLETED bar (provider returns completed only)
        day_bars = self._today_frames(scan.bars, ts, today)
        if not day_bars:
            return
        bench_today = self._slice_today(bench_df, ts, today)
        bar_index = max(0, len(bench_today) - 1)
        sector_bars = {e: self._slice_today(scan.bars.get(e), ts, today)
                       for e in set(self.sector_etf_of.values()) if scan.bars.get(e) is not None}

        from app.backtest.event_simulator import process_bar

        self.broker.set_usdpln(get_usdpln())
        et_time = ts.tz_convert(ET).time()
        is_last = et_time >= settings.session_close.replace(minute=45)

        bar_res = process_bar(
            ts=ts, session_bar_index=bar_index, bars_remaining=max(0, 26 - bar_index),
            is_last_bar=is_last, session_date=today, broker=self.broker,
            strategies=self.strategies, risk_manager=self.risk_manager,
            kill_switch=self.kill_switch, incoming_pending=self.pending, day_bars=day_bars,
            benchmark_bars=bench_today, sector_bars=sector_bars, prev_closes=self.prev_closes,
            universe_symbols=self.universe_symbols, sector_of=self.sector_of,
            sector_etf_of=self.sector_etf_of, min_score=settings.min_signal_score,
        )
        self.pending = bar_res.pending
        self._persist_bar(bar_res, today)
        self._emit_alerts(bar_res)
        sm.save_heartbeat("worker", "OK", {"bar": str(ts), "equity": self.broker.equity_pln()})

        if self.kill_switch.active:
            sm.log_event(Severity.CRITICAL, "kill_switch", self.kill_switch.reason,
                         {"positions_open": len(self.broker.positions)})
            self.alerts.kill_switch(self.kill_switch.reason,
                                    positions_closed=len(self.broker.positions) == 0)

    def _persist_bar(self, bar_res, today: date) -> None:
        sm.save_signals(bar_res.signals, self.run_mode)
        self._counts["signals_generated"] += len(bar_res.signals)
        self._counts["trades_opened"] += len(bar_res.opened)
        self._counts["trades_closed"] += len(bar_res.closed)
        for t in bar_res.closed:
            sm.save_trade(t, self.run_mode, today)
        sm.sync_positions(self.broker.positions, self.run_mode)
        sm.save_portfolio_snapshot(
            self.run_mode, today, cash_pln=self.broker.cash_pln,
            invested_pln=self.broker.invested_pln(), equity_pln=self.broker.equity_pln(),
            daily_pnl=self.broker.daily_realized_pnl_pln, total_pnl=self.broker.realized_pnl_pln,
            exposure=self.broker.gross_exposure_pln(), open_risk=self.broker.total_open_risk_pln(),
            drawdown=self.broker.drawdown(), open_positions=len(self.broker.positions),
        )

    def _emit_alerts(self, bar_res) -> None:
        for pos in bar_res.opened:
            self.alerts.trade_opened(
                ticker=pos.symbol, strategy=str(pos.strategy), entry=pos.entry_price,
                stop=pos.stop_price, target=pos.target_price, shares=int(pos.quantity),
                position_value_pln=pos.quantity * pos.entry_price * self.broker.usdpln,
                risk_pln=pos.initial_risk_per_share * pos.quantity * self.broker.usdpln,
                score=int(pos.metadata.get("score_components", {}).get("total", 0)),
                time_et=pos.opened_at.astimezone(ET).strftime("%H:%M"),
            )
        for t in bar_res.closed:
            self.alerts.trade_closed(
                ticker=t.symbol, strategy=str(t.strategy), exit_reason=str(t.exit_reason),
                net_pnl_pln=t.net_pnl_pln, return_pct=t.return_pct * 100, r_multiple=t.r_multiple,
                holding_minutes=t.holding_bars * 15,
            )

    def finalize_session(self) -> None:
        if self.broker is None or self.session_id is None:
            return
        # Safety: nothing may survive overnight.
        if self.broker.positions:
            from app.backtest.event_simulator import BarResult, _flatten_all
            from app.enums import ExitReason

            now = datetime.now(UTC)
            ts = pd.Timestamp(now)
            out = BarResult()
            _flatten_all(self.broker, {}, ts, out, reason=ExitReason.END_OF_DAY_FLATTEN)
            for t in out.closed:
                sm.save_trade(t, self.run_mode, self.session_date)
            self.kill_switch.check_positions_not_flat_after_eod(len(self.broker.positions))
        sm.sync_positions(self.broker.positions, self.run_mode)
        sm.finalize_session(
            self.session_id, status=SessionStatus.COMPLETED,
            daily_pnl=self.broker.daily_realized_pnl_pln, end_equity=self.broker.equity_pln(),
            counts=self._counts, kill_switch_reason=self.kill_switch.reason or None,
        )
        self.alerts.daily_summary(
            session_date=str(self.session_date), run_mode=self.run_mode.value,
            trades_opened=self._counts["trades_opened"],
            trades_closed=self._counts["trades_closed"],
            daily_pnl_pln=self.broker.daily_realized_pnl_pln,
            end_equity_pln=self.broker.equity_pln(),
            kill_switch_reason=self.kill_switch.reason or None,
        )
        sm.log_event(Severity.INFO, "session_completed",
                     f"Session {self.session_date} completed; flat={len(self.broker.positions)==0}")

    # ------------------------------------------------------------------ helpers
    def _slice_today(self, df, ts, today: date):
        if df is None or df.empty:
            return None
        idx = df.index.tz_convert(ET)
        mask = (idx.date == today) & (df.index <= ts)
        sub = df[mask]
        return sub if not sub.empty else None

    def _today_frames(self, bars: dict, ts, today: date) -> dict:
        out = {}
        for sym, df in bars.items():
            if sym not in self.universe_symbols:
                continue
            sub = self._slice_today(df, ts, today)
            if sub is not None:
                out[sym] = sub
        return out

    def _data_error_count(self) -> int:
        return 0  # extended in production; placeholder keeps the interface


def run_live_paper() -> None:
    """Entry point used by scripts/run_live_paper.py — starts the scheduler loop."""
    configure_logging()
    if not settings.has_live_data_credentials and settings.market_data_provider.lower() != "fixture":
        logger.error(
            "No market-data credentials. Live paper trading needs a data API key. "
            "Set ALPACA_API_KEY / ALPACA_SECRET_KEY in .env (see README). "
            "The system will not invent data."
        )
        raise SystemExit(2)

    from app.engine.event_loop import EventLoop

    engine = LiveEngine()
    loop = EventLoop(engine)
    loop.start_blocking()
