"""market-edge-intraday — Streamlit dashboard (professional dark mode).

Reads directly from the database via ``app.database.session_scope`` and the ORM
models. Every view is resilient to an empty database: nothing here crashes on
empty tables, it just shows friendly empty states.

A RUN_MODE selector (BACKTEST vs LIVE_PAPER) at the top filters everything so
the two data streams are never mixed.

Run:
    streamlit run dashboard/streamlit_app.py --server.port 8501 --server.address 0.0.0.0
"""
from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

# Lazy/defensive imports of the app layer happen inside functions so the
# dashboard still loads if a sibling module is mid-build.

ASSETS = Path(__file__).parent / "assets" / "style.css"
ET_TZ = "America/New_York"


# --------------------------------------------------------------------------- #
# Setup / styling
# --------------------------------------------------------------------------- #
def setup_page() -> None:
    st.set_page_config(
        page_title="Intraday Paper Trading",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    if ASSETS.exists():
        st.markdown(f"<style>{ASSETS.read_text()}</style>", unsafe_allow_html=True)
    st.markdown(
        '<div class="paper-banner">PAPER TRADING — virtual capital only, no real orders. '
        "This is a research / simulation system.</div>",
        unsafe_allow_html=True,
    )


def badge(label: str, kind: str = "idle") -> str:
    return f'<span class="badge badge-{kind}">{label}</span>'


# --------------------------------------------------------------------------- #
# Data access (all read-only, all tolerant of empty / missing tables)
# --------------------------------------------------------------------------- #
def _query_df(builder) -> pd.DataFrame:
    """Run a callable(session) -> list[dict] inside a session, return a DataFrame.

    Any error (missing table, no DB) yields an empty DataFrame so the UI never
    crashes.
    """
    try:
        from app.database import session_scope

        with session_scope() as session:
            rows = builder(session)
        return pd.DataFrame(rows or [])
    except Exception:
        return pd.DataFrame()


def load_portfolio_snapshots(run_mode: str) -> pd.DataFrame:
    def builder(session):
        from app.models import PortfolioSnapshot

        q = (
            session.query(PortfolioSnapshot)
            .filter(PortfolioSnapshot.run_mode == run_mode)
            .order_by(PortfolioSnapshot.timestamp.asc())
        )
        return [
            {
                "timestamp": s.timestamp,
                "session_date": s.session_date,
                "equity_pln": s.equity_pln,
                "cash_pln": s.cash_pln,
                "invested_pln": s.invested_pln,
                "daily_pnl": s.daily_pnl,
                "total_pnl": s.total_pnl,
                "exposure": s.exposure,
                "open_risk": s.open_risk,
                "drawdown": s.drawdown,
                "open_positions": s.open_positions,
            }
            for s in q.all()
        ]

    return _query_df(builder)


def load_positions(run_mode: str) -> pd.DataFrame:
    def builder(session):
        from app.models import Position

        q = session.query(Position).filter(Position.run_mode == run_mode)
        out = []
        now = datetime.now(UTC)
        for p in q.all():
            opened = p.opened_at
            hold = ""
            if opened is not None:
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=UTC)
                mins = (now - opened).total_seconds() / 60.0
                hold = f"{mins:,.0f}m"
            risk_per_share = (
                (p.average_entry - p.stop_price)
                if (p.stop_price is not None and p.average_entry)
                else None
            )
            r_mult = None
            if risk_per_share and risk_per_share > 0 and p.current_price is not None:
                r_mult = (p.current_price - p.average_entry) / risk_per_share
            out.append(
                {
                    "symbol": p.symbol,
                    "strategy": p.strategy,
                    "entry": p.average_entry,
                    "current": p.current_price,
                    "stop": p.stop_price,
                    "target": p.target_price,
                    "unrealized_pnl": p.unrealized_pnl,
                    "r_multiple": r_mult,
                    "holding": hold,
                    "open_risk": p.open_risk,
                }
            )
        return out

    return _query_df(builder)


def load_signals(run_mode: str) -> pd.DataFrame:
    def builder(session):
        from app.models import Signal

        q = (
            session.query(Signal)
            .filter(Signal.run_mode == run_mode)
            .order_by(Signal.signal_time.desc())
            .limit(2000)
        )
        return [
            {
                "signal_time": s.signal_time,
                "symbol": s.symbol,
                "strategy": s.strategy,
                "score": s.score,
                "side": s.side,
                "planned_entry": s.planned_entry,
                "stop_price": s.stop_price,
                "target_price": s.target_price,
                "risk_reward": s.risk_reward,
                "status": s.status,
                "rejection_reason": s.rejection_reason,
            }
            for s in q.all()
        ]

    return _query_df(builder)


def load_trades(run_mode: str) -> pd.DataFrame:
    def builder(session):
        from app.models import Trade

        q = (
            session.query(Trade)
            .filter(Trade.run_mode == run_mode)
            .order_by(Trade.entry_time.desc())
            .limit(5000)
        )
        return [
            {
                "session_date": t.session_date,
                "symbol": t.symbol,
                "strategy": t.strategy,
                "side": t.side,
                "entry_time": t.entry_time,
                "entry_price": t.entry_price,
                "exit_time": t.exit_time,
                "exit_price": t.exit_price,
                "shares": t.shares,
                "stop_price": t.stop_price,
                "net_pnl_pln": t.net_pnl_pln,
                "return_pct": t.return_pct,
                "r_multiple": t.r_multiple,
                "exit_reason": t.exit_reason,
                "status": t.status,
            }
            for t in q.all()
        ]

    return _query_df(builder)


def load_sessions(run_mode: str) -> pd.DataFrame:
    def builder(session):
        from app.models import TradingSession

        q = (
            session.query(TradingSession)
            .filter(TradingSession.run_mode == run_mode)
            .order_by(TradingSession.session_date.desc())
            .limit(60)
        )
        return [
            {
                "session_date": s.session_date,
                "status": s.status,
                "started_at": s.started_at,
                "completed_at": s.completed_at,
                "scanned_symbols": s.scanned_symbols,
                "signals_generated": s.signals_generated,
                "trades_opened": s.trades_opened,
                "trades_closed": s.trades_closed,
                "errors": s.errors,
                "warnings": s.warnings,
                "daily_pnl": s.daily_pnl,
                "kill_switch_reason": s.kill_switch_reason,
            }
            for s in q.all()
        ]

    return _query_df(builder)


def load_events(limit: int = 200) -> pd.DataFrame:
    def builder(session):
        from app.models import SystemEvent

        q = session.query(SystemEvent).order_by(SystemEvent.timestamp.desc()).limit(limit)
        return [
            {
                "timestamp": e.timestamp,
                "severity": e.severity,
                "event_type": e.event_type,
                "message": e.message,
            }
            for e in q.all()
        ]

    return _query_df(builder)


def load_heartbeats() -> pd.DataFrame:
    def builder(session):
        from app.models import Heartbeat

        q = session.query(Heartbeat).order_by(Heartbeat.timestamp.desc()).limit(20)
        return [
            {"service": h.service, "timestamp": h.timestamp, "status": h.status} for h in q.all()
        ]

    return _query_df(builder)


def latest_universe_size(run_mode: str) -> int:
    def builder(session):
        from sqlalchemy import func

        from app.models import UniverseSnapshot

        latest = session.query(func.max(UniverseSnapshot.session_date)).scalar()
        if latest is None:
            return []
        n = (
            session.query(func.count(UniverseSnapshot.id))
            .filter(UniverseSnapshot.session_date == latest)
            .scalar()
        )
        return [{"n": n or 0}]

    df = _query_df(builder)
    return int(df["n"].iloc[0]) if not df.empty else 0


def load_report_csv(name: str) -> pd.DataFrame:
    """Load a CSV from the reports/ directory if present, else empty."""
    for base in ("reports", os.path.join(os.getcwd(), "reports")):
        path = Path(base) / name
        if path.exists():
            try:
                return pd.read_csv(path)
            except Exception:
                return pd.DataFrame()
    return pd.DataFrame()


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _latest(df: pd.DataFrame, col: str, default=0.0):
    if df is None or df.empty or col not in df.columns:
        return default
    val = df[col].iloc[-1]
    return default if pd.isna(val) else val


def _profit_factor(trades: pd.DataFrame) -> float:
    if trades.empty or "net_pnl_pln" not in trades.columns:
        return 0.0
    pnl = trades["net_pnl_pln"].dropna()
    gains = pnl[pnl > 0].sum()
    losses = -pnl[pnl < 0].sum()
    if losses <= 0:
        return float("inf") if gains > 0 else 0.0
    return float(gains / losses)


def _win_rate(trades: pd.DataFrame) -> float:
    closed = trades[trades.get("status", "") == "CLOSED"] if "status" in trades else trades
    pnl = closed["net_pnl_pln"].dropna() if "net_pnl_pln" in closed else pd.Series(dtype=float)
    if pnl.empty:
        return 0.0
    return float((pnl > 0).mean() * 100.0)


def _et_clock() -> tuple[str, str]:
    """Return (current ET time str, time-to-close str) using zoneinfo."""
    try:
        from zoneinfo import ZoneInfo

        now_et = datetime.now(ZoneInfo(ET_TZ))
        close = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        if now_et >= close:
            return now_et.strftime("%H:%M:%S ET"), "market closed"
        delta = close - now_et
        mins = int(delta.total_seconds() // 60)
        return now_et.strftime("%H:%M:%S ET"), f"{mins // 60}h {mins % 60}m to close"
    except Exception:
        return datetime.now(UTC).strftime("%H:%M:%S UTC"), "—"


# --------------------------------------------------------------------------- #
# Views
# --------------------------------------------------------------------------- #
def view_overview(run_mode: str) -> None:
    from dashboard.components import charts

    st.subheader("Overview")
    snaps = load_portfolio_snapshots(run_mode)
    positions = load_positions(run_mode)
    trades = load_trades(run_mode)

    equity = _latest(snaps, "equity_pln", default=0.0)
    daily = _latest(snaps, "daily_pnl", default=0.0)
    total = _latest(snaps, "total_pnl", default=0.0)
    dd = _latest(snaps, "drawdown", default=0.0)
    exposure = _latest(snaps, "exposure", default=0.0)
    open_risk = _latest(snaps, "open_risk", default=0.0)

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Equity (PLN)", f"{equity:,.0f}")
    c2.metric("Daily P&L (PLN)", f"{daily:,.0f}")
    c3.metric("Total P&L (PLN)", f"{total:,.0f}")
    c4.metric("Drawdown", f"{dd * 100:,.2f}%" if abs(dd) <= 1 else f"{dd:,.2f}")
    c5.metric("Exposure", f"{exposure * 100:,.1f}%" if abs(exposure) <= 1 else f"{exposure:,.0f}")
    c6.metric("Open risk (PLN)", f"{open_risk:,.0f}")

    closed = trades[trades.get("status", "") == "CLOSED"] if "status" in trades else trades
    today = pd.Timestamp.utcnow().date()
    trades_today = 0
    if "session_date" in trades and not trades.empty:
        sd = pd.to_datetime(trades["session_date"], errors="coerce").dt.date
        trades_today = int((sd == today).sum())

    c7, c8, c9, c10 = st.columns(4)
    c7.metric("Open positions", f"{len(positions)}")
    c8.metric("Trades today", f"{trades_today}")
    c9.metric("Win rate", f"{_win_rate(trades):.1f}%")
    pf = _profit_factor(closed)
    c10.metric("Profit factor", "∞" if pf == float("inf") else f"{pf:.2f}")

    st.markdown("**System status**")
    hb = load_heartbeats()
    db_ok = True  # if we got here, queries ran
    status_html = badge("DB connected", "ok" if db_ok else "error")
    if not hb.empty:
        status_html += "  " + badge(f"worker: {hb['status'].iloc[0]}", "ok")
    else:
        status_html += "  " + badge("worker: no heartbeat", "warn")
    st.markdown(status_html, unsafe_allow_html=True)

    left, right = st.columns(2)
    with left:
        st.plotly_chart(charts.equity_curve(snaps), use_container_width=True)
    with right:
        daily_df = snaps[["session_date", "daily_pnl"]].dropna() if not snaps.empty else snaps
        if not daily_df.empty:
            daily_df = daily_df.groupby("session_date", as_index=False)["daily_pnl"].last()
        st.plotly_chart(charts.daily_pnl_bars(daily_df), use_container_width=True)


def view_live_session(run_mode: str) -> None:
    st.subheader("Live Session")
    if run_mode != "LIVE_PAPER":
        st.info("Switch RUN_MODE to LIVE_PAPER to see live session details.")
    sessions = load_sessions(run_mode)
    hb = load_heartbeats()
    snaps = load_portfolio_snapshots(run_mode)

    now_et, ttc = _et_clock()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Now (ET)", now_et)
    c2.metric("Time to close", ttc)
    c3.metric("Universe size", f"{latest_universe_size(run_mode)}")
    last_bar = "—"
    if not snaps.empty and "timestamp" in snaps:
        last_bar = pd.to_datetime(snaps["timestamp"].iloc[-1]).strftime("%Y-%m-%d %H:%M")
    c4.metric("Last data point", last_bar)

    if not sessions.empty:
        latest = sessions.iloc[0]
        c5, c6, c7, c8 = st.columns(4)
        c5.metric("Session status", str(latest.get("status", "—")))
        c6.metric("Scanned tickers", f"{latest.get('scanned_symbols', 0)}")
        c7.metric("Signals generated", f"{latest.get('signals_generated', 0)}")
        c8.metric("Trades opened", f"{latest.get('trades_opened', 0)}")
    else:
        st.info("No trading sessions recorded yet.")

    st.markdown("**Last heartbeat**")
    if hb.empty:
        st.markdown(badge("no heartbeat recorded", "warn"), unsafe_allow_html=True)
    else:
        st.dataframe(hb, use_container_width=True, hide_index=True)

    try:
        from app.config import settings

        st.markdown("**Active strategies**")
        st.write(", ".join(str(s) for s in settings.enabled_strategy_set) or "—")
    except Exception:
        pass


def view_positions(run_mode: str) -> None:
    from dashboard.components import tables

    st.subheader("Open Positions")
    positions = load_positions(run_mode)
    if positions.empty:
        st.info("No open positions.")
        return
    st.dataframe(tables.style_positions(positions), use_container_width=True, hide_index=True)


def view_signals(run_mode: str) -> None:
    from dashboard.components import tables

    st.subheader("Signals")
    signals = load_signals(run_mode)
    if signals.empty:
        st.info("No signals recorded.")
        return

    statuses = ["ALL", *sorted(signals["status"].dropna().unique().tolist())]
    col1, col2 = st.columns([1, 2])
    chosen = col1.selectbox("Status", statuses, index=0)
    rej_reasons = sorted(
        signals["rejection_reason"].dropna().unique().tolist()
    )
    rej_filter = col2.multiselect("Rejection reason", rej_reasons, default=[])

    filtered = signals
    if chosen != "ALL":
        filtered = filtered[filtered["status"] == chosen]
    if rej_filter:
        filtered = filtered[filtered["rejection_reason"].isin(rej_filter)]

    st.caption(f"{len(filtered)} signals")
    st.dataframe(tables.style_signals(filtered), use_container_width=True, hide_index=True)


def view_trades(run_mode: str) -> None:
    from dashboard.components import tables

    st.subheader("Trades")
    trades = load_trades(run_mode)
    closed = trades[trades.get("status", "") == "CLOSED"] if "status" in trades else trades
    if closed.empty:
        st.info("No closed trades.")
        return

    c1, c2, c3 = st.columns(3)
    strats = ["ALL", *sorted(closed["strategy"].dropna().unique().tolist())]
    chosen_strat = c1.selectbox("Strategy", strats, index=0)
    result = c2.selectbox("Result", ["ALL", "Winners", "Losers"], index=0)
    reasons = ["ALL", *sorted(closed["exit_reason"].dropna().unique().tolist())]
    chosen_reason = c3.selectbox("Exit reason", reasons, index=0)

    filtered = closed
    if chosen_strat != "ALL":
        filtered = filtered[filtered["strategy"] == chosen_strat]
    if chosen_reason != "ALL":
        filtered = filtered[filtered["exit_reason"] == chosen_reason]
    if result == "Winners":
        filtered = filtered[filtered["net_pnl_pln"] > 0]
    elif result == "Losers":
        filtered = filtered[filtered["net_pnl_pln"] < 0]

    st.caption(f"{len(filtered)} trades")
    st.dataframe(tables.style_trades(filtered), use_container_width=True, hide_index=True)


def view_strategies(run_mode: str) -> None:
    from dashboard.components import charts, tables

    st.subheader("Strategies")
    trades = load_trades(run_mode)
    closed = trades[trades.get("status", "") == "CLOSED"] if "status" in trades else trades
    if closed.empty:
        st.info("No closed trades to summarise by strategy.")
        return

    rows = []
    for strat, grp in closed.groupby("strategy"):
        pnl = grp["net_pnl_pln"].dropna()
        gains = pnl[pnl > 0].sum()
        losses = -pnl[pnl < 0].sum()
        pf = (gains / losses) if losses > 0 else (float("inf") if gains > 0 else 0.0)
        r = grp["r_multiple"].dropna()
        rows.append(
            {
                "strategy": strat,
                "trades": len(grp),
                "win_rate": float((pnl > 0).mean() * 100.0) if len(pnl) else 0.0,
                "profit_factor": round(pf, 2) if pf != float("inf") else 9999.0,
                "net_pnl_pln": float(pnl.sum()),
                "expectancy_r": float(r.mean()) if len(r) else 0.0,
                "avg_r": float(r.mean()) if len(r) else 0.0,
            }
        )
    summary = pd.DataFrame(rows)
    st.dataframe(tables.style_strategies(summary), use_container_width=True, hide_index=True)
    st.plotly_chart(charts.strategy_comparison(summary, "net_pnl_pln"), use_container_width=True)


def view_risk(run_mode: str) -> None:
    from dashboard.components import charts

    st.subheader("Risk")
    snaps = load_portfolio_snapshots(run_mode)
    positions = load_positions(run_mode)
    sessions = load_sessions(run_mode)

    try:
        from app.config import settings

        max_gross = settings.max_gross_exposure_pct * 100
        max_loss = settings.max_daily_loss_pct * 100
        max_cons = settings.max_consecutive_losses
    except Exception:
        max_gross, max_loss, max_cons = 50.0, 1.0, 8

    daily = _latest(snaps, "daily_pnl", 0.0)
    equity = _latest(snaps, "equity_pln", 0.0) or 1.0
    daily_loss_pct = abs(min(daily, 0.0)) / equity * 100 if equity else 0.0
    exposure = _latest(snaps, "exposure", 0.0)
    exposure_pct = exposure * 100 if abs(exposure) <= 1 else exposure
    open_risk = _latest(snaps, "open_risk", 0.0)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Daily loss", f"{daily_loss_pct:.2f}%", help=f"limit {max_loss:.2f}%")
    c2.metric("Open risk (PLN)", f"{open_risk:,.0f}")
    c3.metric("Open positions", f"{len(positions)}")

    kill = None
    if not sessions.empty:
        kill = sessions.iloc[0].get("kill_switch_reason")
    if kill:
        c4.markdown(badge(f"KILL SWITCH: {kill}", "error"), unsafe_allow_html=True)
    else:
        c4.markdown(badge("kill switch: inactive", "ok"), unsafe_allow_html=True)

    left, right = st.columns(2)
    with left:
        st.plotly_chart(
            charts.exposure_gauge(exposure_pct, max_gross, "Gross exposure %"),
            use_container_width=True,
        )
    with right:
        st.plotly_chart(
            charts.exposure_gauge(daily_loss_pct, max_loss, "Daily loss %"),
            use_container_width=True,
        )

    st.markdown("**Exposure by strategy (open positions)**")
    if positions.empty:
        st.info("No open positions.")
    else:
        by_strat = (
            positions.assign(value=positions["entry"].fillna(0) * 0 + positions["entry"].fillna(0))
            .groupby("strategy")["entry"]
            .count()
            .reset_index(name="positions")
        )
        st.dataframe(by_strat, use_container_width=True, hide_index=True)

    st.caption(f"Limits — max gross {max_gross:.0f}% · max daily loss {max_loss:.2f}% "
               f"· max consecutive losses {max_cons}")


def view_backtest(run_mode: str) -> None:
    from dashboard.components import charts

    st.subheader("Backtest")
    st.caption("Loads from the BACKTEST DB stream and from reports/ CSVs when present.")

    snaps = load_portfolio_snapshots("BACKTEST")
    trades = load_trades("BACKTEST")

    # Prefer report CSVs if the backtester wrote them.
    equity_csv = load_report_csv("equity_curve.csv")
    trades_csv = load_report_csv("trades.csv")
    if not equity_csv.empty and "equity_pln" in equity_csv.columns:
        if "timestamp" not in equity_csv.columns:
            equity_csv = equity_csv.rename(columns={equity_csv.columns[0]: "timestamp"})
        snaps = equity_csv
    if not trades_csv.empty:
        trades = trades_csv

    if snaps.empty and trades.empty:
        st.info(
            "No backtest data found. Run a backtest first:\n\n"
            "`python scripts/run_backtest.py --start ... --end ... --symbols AAPL,MSFT`"
        )
        return

    c1, c2 = st.columns(2)
    with c1:
        st.plotly_chart(charts.equity_curve(snaps), use_container_width=True)
    with c2:
        st.plotly_chart(charts.drawdown(snaps), use_container_width=True)

    c3, c4 = st.columns(2)
    with c3:
        st.plotly_chart(charts.monthly_heatmap(trades), use_container_width=True)
    with c4:
        st.plotly_chart(charts.yearly_returns(trades), use_container_width=True)

    st.plotly_chart(charts.r_multiple_hist(trades), use_container_width=True)

    wf = load_report_csv("walk_forward.csv")
    if not wf.empty:
        st.markdown("**Walk-forward / out-of-sample**")
        st.dataframe(wf, use_container_width=True, hide_index=True)

    cost = load_report_csv("cost_scenarios.csv")
    if not cost.empty:
        st.markdown("**Cost scenarios**")
        st.dataframe(cost, use_container_width=True, hide_index=True)


def view_health(run_mode: str) -> None:
    st.subheader("System Health")
    hb = load_heartbeats()
    events = load_events()
    sessions = load_sessions(run_mode)

    try:
        from app.config import settings

        provider_ok = settings.has_live_data_credentials
        provider_name = settings.market_data_provider
    except Exception:
        provider_ok, provider_name = False, "unknown"

    c1, c2, c3, c4 = st.columns(4)
    c1.markdown("**Database**<br>" + badge("connected", "ok"), unsafe_allow_html=True)
    c2.markdown(
        f"**Data provider ({provider_name})**<br>"
        + badge("configured" if provider_ok else "no credentials", "ok" if provider_ok else "warn"),
        unsafe_allow_html=True,
    )
    if hb.empty:
        c3.markdown("**Worker heartbeat**<br>" + badge("none", "warn"), unsafe_allow_html=True)
    else:
        last = pd.to_datetime(hb["timestamp"].iloc[0])
        c3.markdown(
            "**Worker heartbeat**<br>" + badge(last.strftime("%H:%M:%S"), "ok"),
            unsafe_allow_html=True,
        )
    last_run = "—"
    if not sessions.empty and sessions.iloc[0].get("completed_at") is not None:
        last_run = pd.to_datetime(sessions.iloc[0]["completed_at"]).strftime("%Y-%m-%d %H:%M")
    c4.metric("Last successful run", last_run)

    st.markdown("**Recent errors & warnings**")
    if events.empty:
        st.success("No system events recorded.")
    else:
        problems = events[events["severity"].isin(["ERROR", "CRITICAL", "WARNING"])]
        if problems.empty:
            st.success("No errors or warnings recorded.")
        else:
            st.dataframe(problems, use_container_width=True, hide_index=True)
        with st.expander("All recent events"):
            st.dataframe(events, use_container_width=True, hide_index=True)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
VIEWS = {
    "Overview": view_overview,
    "Live Session": view_live_session,
    "Open Positions": view_positions,
    "Signals": view_signals,
    "Trades": view_trades,
    "Strategies": view_strategies,
    "Risk": view_risk,
    "Backtest": view_backtest,
    "System Health": view_health,
}


def main() -> None:
    setup_page()

    with st.sidebar:
        st.markdown("### Intraday Paper Trading")
        run_mode = st.radio(
            "RUN MODE",
            options=["BACKTEST", "LIVE_PAPER"],
            index=0,
            help="The two streams are never mixed. Everything below is filtered by this.",
        )
        st.markdown(
            f'<span class="mode-pill">{run_mode}</span>', unsafe_allow_html=True
        )
        st.divider()
        page = st.radio("View", list(VIEWS.keys()), index=0)
        st.divider()
        if st.button("Refresh data"):
            st.rerun()
        st.caption("Read-only dashboard. No orders are placed from here.")

    st.title(page)
    try:
        VIEWS[page](run_mode)
    except Exception as exc:  # last-resort guard so the page never hard-crashes
        st.error(f"Could not render this view: {exc}")
        st.caption("This is usually a still-empty database or a module mid-build.")


if __name__ == "__main__":
    main()
