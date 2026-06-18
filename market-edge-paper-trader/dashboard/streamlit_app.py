import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from app.database import get_connection
from app.config import INITIAL_CAPITAL_PLN
from app.reporting import get_performance_summary

st.set_page_config(
    page_title="Market Edge Paper Trader",
    page_icon="📈",
    layout="wide",
)


# ─── helpers ──────────────────────────────────────────────────────────────────

@st.cache_data(ttl=30)
def load_snapshots() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql("SELECT * FROM portfolio_snapshots ORDER BY snapshot_date ASC", conn)
    conn.close()
    if not df.empty:
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"])
    return df


@st.cache_data(ttl=30)
def load_open_trades() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql("SELECT * FROM trades WHERE status='open' ORDER BY entry_date DESC", conn)
    conn.close()
    return df


@st.cache_data(ttl=30)
def load_closed_trades() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql("SELECT * FROM trades WHERE status='closed' ORDER BY exit_date DESC", conn)
    conn.close()
    return df


@st.cache_data(ttl=30)
def load_strategy_stats() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql("SELECT * FROM strategy_stats ORDER BY total_pnl_pln DESC", conn)
    conn.close()
    return df


@st.cache_data(ttl=30)
def load_signals() -> pd.DataFrame:
    conn = get_connection()
    df = pd.read_sql(
        "SELECT * FROM signals ORDER BY signal_date DESC, score DESC LIMIT 100", conn
    )
    conn.close()
    return df


def color_pnl(val):
    if isinstance(val, (int, float)):
        color = "green" if val > 0 else ("red" if val < 0 else "gray")
        return f"color: {color}"
    return ""


def pct_bar(val):
    if isinstance(val, (int, float)):
        color = "rgba(0,200,100,0.3)" if val >= 0 else "rgba(255,80,80,0.3)"
        return f"background: linear-gradient(90deg, {color} {min(abs(val)*2, 100):.0f}%, transparent 0%)"
    return ""


# ─── layout ───────────────────────────────────────────────────────────────────

st.title("📈 Market Edge Paper Trader")
st.caption(f"Virtual capital: {INITIAL_CAPITAL_PLN:,.0f} PLN | Simulation only — no real trades")

snapshots = load_snapshots()
open_trades = load_open_trades()
closed_trades = load_closed_trades()
strategy_stats = load_strategy_stats()
signals = load_signals()
perf = get_performance_summary()

if snapshots.empty:
    st.warning("No data yet. Run `python scripts/run_scan.py` or `python scripts/run_backtest.py` first.")
    st.stop()

latest = snapshots.iloc[-1]
total_value = latest["total_value_pln"]
cash = latest["cash_pln"]
invested = latest["invested_pln"]
total_pnl = latest["total_pnl_pln"]
total_return = latest["total_return_pct"]
daily_pnl = latest["daily_pnl_pln"]
max_dd = snapshots["max_drawdown_pct"].max()

# ─── KPI row ──────────────────────────────────────────────────────────────────
st.subheader("Portfolio Overview")
col1, col2, col3, col4, col5, col6 = st.columns(6)

col1.metric("Total Value", f"{total_value:,.0f} PLN")
col2.metric("Cash", f"{cash:,.0f} PLN")
col3.metric("Invested", f"{invested:,.0f} PLN")
col4.metric(
    "Total Return",
    f"{total_return:+.2f}%",
    delta=f"{total_pnl:+,.0f} PLN",
    delta_color="normal",
)
col5.metric("Daily P&L", f"{daily_pnl:+,.0f} PLN")
col6.metric("Max Drawdown", f"{max_dd:.2f}%")

st.divider()

# ─── Performance metrics ───────────────────────────────────────────────────────
st.subheader("Trading Statistics")
m1, m2, m3, m4, m5, m6, m7 = st.columns(7)
m1.metric("Open Positions", len(open_trades))
m2.metric("Closed Trades", len(closed_trades))

if perf:
    m3.metric("Win Rate", f"{perf['win_rate']:.1f}%")
    m4.metric("Avg Win", f"{perf['avg_win']:+,.0f} PLN")
    m5.metric("Avg Loss", f"{perf['avg_loss']:+,.0f} PLN")
    m6.metric("Profit Factor", f"{perf['profit_factor']:.2f}")
    m7.metric("Avg R-Multiple", f"{perf['avg_r']:.2f}R")
else:
    m3.metric("Win Rate", "—")
    m4.metric("Avg Win", "—")
    m5.metric("Avg Loss", "—")
    m6.metric("Profit Factor", "—")
    m7.metric("Avg R-Multiple", "—")

st.divider()

# ─── Charts ───────────────────────────────────────────────────────────────────
chart_col1, chart_col2 = st.columns([2, 1])

with chart_col1:
    st.subheader("Equity Curve")
    fig_eq = go.Figure()
    fig_eq.add_trace(go.Scatter(
        x=snapshots["snapshot_date"],
        y=snapshots["total_value_pln"],
        mode="lines",
        line=dict(color="#00C853", width=2),
        fill="tozeroy",
        fillcolor="rgba(0,200,83,0.08)",
        name="Portfolio Value",
        hovertemplate="%{x|%Y-%m-%d}<br>%{y:,.0f} PLN<extra></extra>",
    ))
    fig_eq.add_hline(
        y=INITIAL_CAPITAL_PLN,
        line_dash="dash",
        line_color="gray",
        annotation_text="Initial Capital",
    )
    fig_eq.update_layout(
        height=300,
        margin=dict(l=0, r=0, t=10, b=0),
        yaxis_tickformat=",.0f",
        showlegend=False,
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig_eq, use_container_width=True)

with chart_col2:
    st.subheader("Drawdown")
    if len(snapshots) > 1:
        peak = snapshots["total_value_pln"].cummax()
        drawdown = (snapshots["total_value_pln"] - peak) / peak * 100
        fig_dd = go.Figure()
        fig_dd.add_trace(go.Scatter(
            x=snapshots["snapshot_date"],
            y=drawdown,
            mode="lines",
            line=dict(color="#FF5252", width=1.5),
            fill="tozeroy",
            fillcolor="rgba(255,82,82,0.12)",
            name="Drawdown %",
            hovertemplate="%{x|%Y-%m-%d}<br>%{y:.2f}%<extra></extra>",
        ))
        fig_dd.update_layout(
            height=300,
            margin=dict(l=0, r=0, t=10, b=0),
            yaxis_ticksuffix="%",
            showlegend=False,
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_dd, use_container_width=True)
    else:
        st.info("Not enough snapshots for drawdown chart.")

st.divider()

# ─── Strategy performance ──────────────────────────────────────────────────────
st.subheader("Strategy Performance")
if not strategy_stats.empty:
    display_cols = ["strategy", "total_trades", "win_rate", "total_pnl_pln",
                    "avg_win_pln", "avg_loss_pln", "profit_factor", "avg_r_multiple", "avg_holding_days"]
    display_cols = [c for c in display_cols if c in strategy_stats.columns]
    st.dataframe(
        strategy_stats[display_cols].rename(columns={
            "strategy": "Strategy",
            "total_trades": "Trades",
            "win_rate": "Win %",
            "total_pnl_pln": "P&L PLN",
            "avg_win_pln": "Avg Win",
            "avg_loss_pln": "Avg Loss",
            "profit_factor": "Prof. Factor",
            "avg_r_multiple": "Avg R",
            "avg_holding_days": "Avg Days",
        }).style.map(color_pnl, subset=["P&L PLN", "Avg Win", "Avg Loss"]),
        use_container_width=True,
        hide_index=True,
    )

    if len(strategy_stats) > 1:
        fig_strat = px.bar(
            strategy_stats,
            x="strategy",
            y="total_pnl_pln",
            color="total_pnl_pln",
            color_continuous_scale=["#FF5252", "#FFA726", "#66BB6A"],
            labels={"total_pnl_pln": "P&L PLN", "strategy": "Strategy"},
            title="P&L per Strategy",
        )
        fig_strat.update_layout(
            height=280,
            showlegend=False,
            margin=dict(l=0, r=0, t=40, b=0),
            plot_bgcolor="rgba(0,0,0,0)",
            paper_bgcolor="rgba(0,0,0,0)",
        )
        st.plotly_chart(fig_strat, use_container_width=True)
else:
    st.info("No closed trades yet.")

st.divider()

# ─── Open positions ─────────────────────────────────────────────────────────
st.subheader(f"Open Positions ({len(open_trades)})")
if not open_trades.empty:
    cols_show = ["ticker", "strategy", "entry_date", "entry_price", "stop_loss",
                 "take_profit", "shares", "position_value_pln", "pnl_pln", "pnl_pct",
                 "score", "holding_days"]
    cols_show = [c for c in cols_show if c in open_trades.columns]
    st.dataframe(
        open_trades[cols_show].style.map(color_pnl, subset=["pnl_pln", "pnl_pct"]),
        use_container_width=True,
        hide_index=True,
    )
else:
    st.info("No open positions.")

st.divider()

# ─── Closed trades ────────────────────────────────────────────────────────────
st.subheader(f"Closed Trades ({len(closed_trades)})")
if not closed_trades.empty:
    tab_all, tab_best, tab_worst = st.tabs(["All", "Top 5 Winners", "Top 5 Losers"])

    cols_show = ["ticker", "strategy", "entry_date", "exit_date", "entry_price",
                 "exit_price", "pnl_pln", "pnl_pct", "r_multiple",
                 "holding_days", "exit_reason", "score"]
    cols_show = [c for c in cols_show if c in closed_trades.columns]

    with tab_all:
        st.dataframe(
            closed_trades[cols_show].style.map(color_pnl, subset=["pnl_pln", "pnl_pct"]),
            use_container_width=True,
            hide_index=True,
        )

    with tab_best:
        best = closed_trades.nlargest(5, "pnl_pln")[cols_show]
        st.dataframe(best.style.map(color_pnl, subset=["pnl_pln", "pnl_pct"]),
                     use_container_width=True, hide_index=True)

    with tab_worst:
        worst = closed_trades.nsmallest(5, "pnl_pln")[cols_show]
        st.dataframe(worst.style.map(color_pnl, subset=["pnl_pln", "pnl_pct"]),
                     use_container_width=True, hide_index=True)
else:
    st.info("No closed trades yet.")

st.divider()

# ─── Latest signals ───────────────────────────────────────────────────────────
st.subheader("Latest Signals")
if not signals.empty:
    cols_show = ["signal_date", "ticker", "strategy", "score", "entry_price",
                 "stop_loss", "take_profit", "rsi", "volume_ratio", "acted", "reason"]
    cols_show = [c for c in cols_show if c in signals.columns]
    st.dataframe(signals[cols_show].head(30), use_container_width=True, hide_index=True)
else:
    st.info("No signals yet.")

# ─── footer ───────────────────────────────────────────────────────────────────
st.divider()
st.caption(
    "⚠️ Paper trading simulation only. No real money. No guarantees. "
    "Past performance does not predict future results. "
    "Minimum 100–200 trades needed for statistical significance."
)

if st.button("🔄 Refresh data"):
    st.cache_data.clear()
    st.rerun()
