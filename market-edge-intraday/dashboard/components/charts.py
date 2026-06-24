"""Reusable Plotly figures for the dashboard, all on a dark template.

Every function tolerates an empty / missing DataFrame and returns a figure with
a friendly "no data" annotation rather than raising. Keep these pure: they take
data in and return a ``plotly.graph_objects.Figure``.
"""
from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.graph_objects as go

# Shared dark palette.
BG = "#0e1117"
PANEL = "#161b22"
GRID = "#21262d"
TEXT = "#c9d1d9"
ACCENT = "#2f81f7"
GREEN = "#3fb950"
RED = "#f85149"
AMBER = "#d29922"


def _base_layout(fig: go.Figure, title: str = "", height: int = 320) -> go.Figure:
    fig.update_layout(
        template="plotly_dark",
        title=title,
        paper_bgcolor=BG,
        plot_bgcolor=PANEL,
        font=dict(color=TEXT, size=12),
        margin=dict(l=40, r=20, t=40 if title else 10, b=30),
        height=height,
        xaxis=dict(gridcolor=GRID, zerolinecolor=GRID),
        yaxis=dict(gridcolor=GRID, zerolinecolor=GRID),
        legend=dict(bgcolor="rgba(0,0,0,0)"),
    )
    return fig


def _empty(title: str, message: str = "No data yet") -> go.Figure:
    fig = go.Figure()
    fig.add_annotation(
        text=message,
        showarrow=False,
        font=dict(color="#6e7681", size=16),
        xref="paper",
        yref="paper",
        x=0.5,
        y=0.5,
    )
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return _base_layout(fig, title)


def _has(df: Any, *cols: str) -> bool:
    if df is None or not isinstance(df, pd.DataFrame) or df.empty:
        return False
    return all(c in df.columns for c in cols)


def equity_curve(df: pd.DataFrame, title: str = "Equity (PLN)") -> go.Figure:
    """Line chart of equity over time. Expects columns: timestamp, equity_pln."""
    if not _has(df, "timestamp", "equity_pln"):
        return _empty(title)
    data = df.sort_values("timestamp")
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=data["timestamp"],
            y=data["equity_pln"],
            mode="lines",
            line=dict(color=ACCENT, width=2),
            fill="tozeroy",
            fillcolor="rgba(47,129,247,0.12)",
            name="Equity",
        )
    )
    return _base_layout(fig, title)


def drawdown(df: pd.DataFrame, title: str = "Drawdown (%)") -> go.Figure:
    """Underwater curve. Expects timestamp + equity_pln (computes drawdown)."""
    if not _has(df, "timestamp", "equity_pln"):
        return _empty(title)
    data = df.sort_values("timestamp").copy()
    running_max = data["equity_pln"].cummax()
    dd = (data["equity_pln"] / running_max - 1.0) * 100.0
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=data["timestamp"],
            y=dd,
            mode="lines",
            line=dict(color=RED, width=1.5),
            fill="tozeroy",
            fillcolor="rgba(248,81,73,0.15)",
            name="Drawdown",
        )
    )
    return _base_layout(fig, title)


def daily_pnl_bars(df: pd.DataFrame, title: str = "Daily P&L (PLN)") -> go.Figure:
    """Bar chart of P&L per day. Expects columns: session_date, daily_pnl."""
    if not _has(df, "session_date", "daily_pnl"):
        return _empty(title)
    data = df.sort_values("session_date")
    colors = [GREEN if v >= 0 else RED for v in data["daily_pnl"]]
    fig = go.Figure(
        go.Bar(x=data["session_date"], y=data["daily_pnl"], marker_color=colors, name="P&L")
    )
    return _base_layout(fig, title)


def monthly_heatmap(df: pd.DataFrame, title: str = "Monthly returns (%)") -> go.Figure:
    """Year x month heatmap. Expects columns: session_date, return_pct (per trade/day)."""
    if not _has(df, "session_date", "return_pct"):
        return _empty(title)
    data = df.copy()
    data["session_date"] = pd.to_datetime(data["session_date"], errors="coerce")
    data = data.dropna(subset=["session_date"])
    if data.empty:
        return _empty(title)
    data["year"] = data["session_date"].dt.year
    data["month"] = data["session_date"].dt.month
    pivot = (
        data.groupby(["year", "month"])["return_pct"].sum().unstack(fill_value=0.0).sort_index()
    )
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    cols = [months[m - 1] for m in pivot.columns]
    fig = go.Figure(
        go.Heatmap(
            z=pivot.values,
            x=cols,
            y=[str(y) for y in pivot.index],
            colorscale=[[0, RED], [0.5, PANEL], [1, GREEN]],
            zmid=0,
            colorbar=dict(title="%"),
        )
    )
    return _base_layout(fig, title, height=max(220, 60 + 30 * len(pivot.index)))


def yearly_returns(df: pd.DataFrame, title: str = "Yearly returns (%)") -> go.Figure:
    """Bar chart of return per calendar year. Expects session_date, return_pct."""
    if not _has(df, "session_date", "return_pct"):
        return _empty(title)
    data = df.copy()
    data["session_date"] = pd.to_datetime(data["session_date"], errors="coerce")
    data = data.dropna(subset=["session_date"])
    if data.empty:
        return _empty(title)
    by_year = data.groupby(data["session_date"].dt.year)["return_pct"].sum()
    colors = [GREEN if v >= 0 else RED for v in by_year.values]
    fig = go.Figure(
        go.Bar(x=[str(y) for y in by_year.index], y=by_year.values, marker_color=colors)
    )
    return _base_layout(fig, title)


def strategy_comparison(df: pd.DataFrame, metric: str = "net_pnl_pln") -> go.Figure:
    """Horizontal bar comparing a metric across strategies.

    Expects a 'strategy' column plus the chosen metric column.
    """
    title = f"Strategy comparison — {metric}"
    if not _has(df, "strategy", metric):
        return _empty(title)
    data = df.sort_values(metric)
    colors = [GREEN if v >= 0 else RED for v in data[metric]]
    fig = go.Figure(
        go.Bar(x=data[metric], y=data["strategy"], orientation="h", marker_color=colors)
    )
    return _base_layout(fig, title, height=max(220, 40 + 36 * len(data)))


def r_multiple_hist(df: pd.DataFrame, title: str = "R-multiple distribution") -> go.Figure:
    """Histogram of trade R-multiples. Expects an 'r_multiple' column."""
    if not _has(df, "r_multiple"):
        return _empty(title)
    fig = go.Figure(go.Histogram(x=df["r_multiple"].dropna(), marker_color=ACCENT, nbinsx=30))
    fig.add_vline(x=0, line_color="#6e7681", line_width=1)
    return _base_layout(fig, title)


def exposure_gauge(value_pct: float, limit_pct: float, title: str = "Gross exposure") -> go.Figure:
    """Simple gauge for an exposure / risk percentage against a limit."""
    import plotly.graph_objects as _go

    value = max(0.0, float(value_pct))
    limit = max(0.01, float(limit_pct))
    fig = _go.Figure(
        _go.Indicator(
            mode="gauge+number",
            value=value,
            number={"suffix": "%"},
            gauge={
                "axis": {"range": [0, max(limit * 1.5, value * 1.2, 1)]},
                "bar": {"color": ACCENT},
                "threshold": {
                    "line": {"color": RED, "width": 3},
                    "thickness": 0.8,
                    "value": limit,
                },
            },
            title={"text": title},
        )
    )
    return _base_layout(fig, "", height=220)
