"""DataFrame styling helpers for the dashboard.

These return pandas Styler objects (or plain DataFrames) ready for
``st.dataframe`` / ``st.table`` with a consistent dark look and sensible
number formatting. All helpers tolerate empty frames.
"""
from __future__ import annotations

import pandas as pd

POS = "#3fb950"
NEG = "#f85149"


def _empty_frame(columns: list[str]) -> pd.DataFrame:
    return pd.DataFrame({c: [] for c in columns})


def color_pnl(value: object) -> str:
    """CSS for a P&L cell: green if positive, red if negative."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    if v > 0:
        return f"color: {POS}; font-weight: 600;"
    if v < 0:
        return f"color: {NEG}; font-weight: 600;"
    return ""


def style_trades(df: pd.DataFrame) -> pd.io.formats.style.Styler | pd.DataFrame:
    """Style a closed-trades table: colour P&L / R columns, format numbers."""
    if df is None or df.empty:
        return df if isinstance(df, pd.DataFrame) else _empty_frame([])
    styler = df.style
    pnl_cols = [c for c in ("net_pnl_pln", "net_pnl_usd", "return_pct", "r_multiple") if c in df]
    if pnl_cols:
        styler = styler.map(color_pnl, subset=pnl_cols)
    fmt: dict[str, str] = {}
    for col in df.columns:
        if col in ("net_pnl_pln", "net_pnl_usd", "entry_price", "exit_price", "stop_price"):
            fmt[col] = "{:,.2f}"
        elif col in ("return_pct",):
            fmt[col] = "{:+.2f}%"
        elif col in ("r_multiple",):
            fmt[col] = "{:+.2f}R"
    if fmt:
        styler = styler.format(fmt, na_rep="—")
    return styler


def style_positions(df: pd.DataFrame) -> pd.io.formats.style.Styler | pd.DataFrame:
    """Style an open-positions table."""
    if df is None or df.empty:
        return df if isinstance(df, pd.DataFrame) else _empty_frame([])
    styler = df.style
    pnl_cols = [c for c in ("unrealized_pnl", "pnl_pln", "r_multiple") if c in df]
    if pnl_cols:
        styler = styler.map(color_pnl, subset=pnl_cols)
    fmt = {
        c: "{:,.2f}"
        for c in ("entry", "current", "stop", "target", "unrealized_pnl", "open_risk")
        if c in df
    }
    if "r_multiple" in df:
        fmt["r_multiple"] = "{:+.2f}R"
    if fmt:
        styler = styler.format(fmt, na_rep="—")
    return styler


def style_signals(df: pd.DataFrame) -> pd.io.formats.style.Styler | pd.DataFrame:
    """Style a signals table; colour status text."""
    if df is None or df.empty:
        return df if isinstance(df, pd.DataFrame) else _empty_frame([])

    def _status_color(val: object) -> str:
        s = str(val).upper()
        if s == "FILLED":
            return f"color: {POS};"
        if s in ("REJECTED", "EXPIRED", "CANCELLED"):
            return f"color: {NEG};"
        if s == "PENDING":
            return "color: #d29922;"
        return ""

    styler = df.style
    if "status" in df:
        styler = styler.map(_status_color, subset=["status"])
    fmt = {
        c: "{:,.2f}"
        for c in ("score", "planned_entry", "stop_price", "target_price", "risk_reward")
        if c in df
    }
    if fmt:
        styler = styler.format(fmt, na_rep="—")
    return styler


def style_strategies(df: pd.DataFrame) -> pd.io.formats.style.Styler | pd.DataFrame:
    """Style a per-strategy summary table."""
    if df is None or df.empty:
        return df if isinstance(df, pd.DataFrame) else _empty_frame([])
    styler = df.style
    pnl_cols = [c for c in ("net_pnl_pln", "expectancy_r", "avg_r") if c in df]
    if pnl_cols:
        styler = styler.map(color_pnl, subset=pnl_cols)
    fmt: dict[str, str] = {}
    for col in df.columns:
        if col in ("win_rate",):
            fmt[col] = "{:.1f}%"
        elif col in ("profit_factor", "expectancy_r", "avg_r"):
            fmt[col] = "{:.2f}"
        elif col in ("net_pnl_pln", "max_drawdown_pct"):
            fmt[col] = "{:,.2f}"
    if fmt:
        styler = styler.format(fmt, na_rep="—")
    return styler
