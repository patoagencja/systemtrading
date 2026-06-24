"""Relative-strength helpers: a symbol vs SPY and vs its sector ETF.

Relative strength here = the symbol's intraday return-from-open minus the
benchmark's intraday return-from-open, using completed bars only.
"""
from __future__ import annotations

import pandas as pd


def return_from_open(bars: pd.DataFrame) -> float:
    """Return of the last completed bar's close vs the session open."""
    if bars.empty:
        return 0.0
    session_open = float(bars["open"].iloc[0])
    last_close = float(bars["close"].iloc[-1])
    if session_open <= 0:
        return 0.0
    return (last_close - session_open) / session_open


def relative_strength(symbol_bars: pd.DataFrame, benchmark_bars: pd.DataFrame) -> float:
    """Symbol return-from-open minus benchmark return-from-open (fraction).

    Positive => symbol is outperforming the benchmark so far this session.
    """
    return return_from_open(symbol_bars) - return_from_open(benchmark_bars)


def benchmark_trend(benchmark_bars: pd.DataFrame, vwap: pd.Series | None = None) -> float:
    """Crude SPY intraday trend score in [-1, 1].

    Combines return-from-open sign/magnitude with position vs its own VWAP.
    Used by strategies to gate longs when SPY is dropping hard.
    """
    if benchmark_bars.empty:
        return 0.0
    rfo = return_from_open(benchmark_bars)
    # Scale: a 1% move maps to ~1.0.
    trend = max(-1.0, min(1.0, rfo / 0.01))
    return trend
