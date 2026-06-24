"""Pure message-formatting functions for outbound alerts.

These return plain-text blocks exactly as specified by the project. They are
side-effect-free and channel-agnostic; the channel adapters (Telegram / Slack)
decide how to deliver them. PLN amounts use a thin space as thousands separator
(e.g. ``29 609 PLN``).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any


def _fmt_pln(value: float | int | None) -> str:
    """Format a PLN amount with space thousands separators, no decimals.

    Example: ``29609.4 -> "29 609"``.
    """
    if value is None:
        return "0"
    rounded = int(round(float(value)))
    return f"{rounded:,}".replace(",", " ")


def _fmt_signed_pln(value: float | int | None) -> str:
    """Signed PLN amount: ``+621``, ``-150`` (space thousands separators)."""
    v = 0.0 if value is None else float(value)
    sign = "+" if v >= 0 else "-"
    return f"{sign}{_fmt_pln(abs(v))}"


def _fmt_signed_pct(value: float | None) -> str:
    """Signed percentage with two decimals, e.g. ``+2.10%``."""
    v = 0.0 if value is None else float(value)
    return f"{v:+.2f}%"


def _fmt_signed_r(value: float | None) -> str:
    """Signed R multiple with two decimals + 'R', e.g. ``+1.92R``."""
    v = 0.0 if value is None else float(value)
    return f"{v:+.2f}R"


def _fmt_et_time(value: Any) -> str:
    """Render an ET wall-clock 'HH:MM' from a datetime / string."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.strftime("%H:%M")
    return str(value)


def format_trade_opened(
    *,
    ticker: str,
    strategy: Any,
    entry: float,
    stop: float,
    target: float,
    shares: float | int,
    position_value_pln: float,
    risk_pln: float,
    score: float | int,
    time_et: Any,
) -> str:
    """Format the 'trade opened' alert block."""
    return (
        "INTRADAY PAPER TRADE OPENED\n"
        "\n"
        f"Ticker: {ticker}\n"
        f"Strategy: {strategy}\n"
        f"Entry: {entry:.2f} USD\n"
        f"Stop: {stop:.2f} USD\n"
        f"Target: {target:.2f} USD\n"
        f"Shares: {int(shares)}\n"
        f"Position value: {_fmt_pln(position_value_pln)} PLN\n"
        f"Risk: {_fmt_pln(risk_pln)} PLN\n"
        f"Score: {int(round(float(score)))}/100\n"
        f"Time: {_fmt_et_time(time_et)} ET"
    )


def format_trade_closed(
    *,
    ticker: str,
    strategy: Any,
    exit_reason: Any,
    net_pnl_pln: float,
    return_pct: float,
    r_multiple: float,
    holding_minutes: float | int,
) -> str:
    """Format the 'trade closed' alert block."""
    return (
        "PAPER TRADE CLOSED\n"
        "\n"
        f"Ticker: {ticker}\n"
        f"Strategy: {strategy}\n"
        f"Exit reason: {exit_reason}\n"
        f"Net P&L: {_fmt_signed_pln(net_pnl_pln)} PLN\n"
        f"Return: {_fmt_signed_pct(return_pct)}\n"
        f"R multiple: {_fmt_signed_r(r_multiple)}\n"
        f"Holding time: {int(round(float(holding_minutes)))} minutes"
    )


def format_kill_switch(reason: str, positions_closed: bool) -> str:
    """Format the kill-switch alert.

    Clearly marked, includes the reason and whether positions were closed.
    """
    closed = "Yes" if positions_closed else "No"
    return (
        "!!! KILL SWITCH ACTIVATED !!!\n"
        "\n"
        "Trading halted for the session.\n"
        f"Reason: {reason}\n"
        f"Positions closed: {closed}"
    )


def format_daily_summary(
    *,
    session_date: Any,
    run_mode: Any,
    trades_opened: int,
    trades_closed: int,
    daily_pnl_pln: float,
    win_rate: float | None = None,
    end_equity_pln: float | None = None,
    kill_switch_reason: str | None = None,
) -> str:
    """Format the end-of-day summary alert."""
    lines = [
        "INTRADAY PAPER DAILY SUMMARY",
        "",
        f"Date: {session_date}",
        f"Mode: {run_mode}",
        f"Trades opened: {int(trades_opened)}",
        f"Trades closed: {int(trades_closed)}",
        f"Daily P&L: {_fmt_signed_pln(daily_pnl_pln)} PLN",
    ]
    if win_rate is not None:
        lines.append(f"Win rate: {float(win_rate) * 100:.1f}%")
    if end_equity_pln is not None:
        lines.append(f"End equity: {_fmt_pln(end_equity_pln)} PLN")
    if kill_switch_reason:
        lines.append(f"Kill switch: {kill_switch_reason}")
    return "\n".join(lines)


__all__ = [
    "format_trade_opened",
    "format_trade_closed",
    "format_kill_switch",
    "format_daily_summary",
]
