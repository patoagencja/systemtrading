import pandas as pd
from datetime import date, timedelta


def trading_days_between(start: date, end: date) -> list[date]:
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:  # Mon–Fri
            days.append(current)
        current += timedelta(days=1)
    return days


def pct_fmt(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:.2f}%"


def pln_fmt(value: float) -> str:
    sign = "+" if value >= 0 else ""
    return f"{sign}{value:,.2f} PLN"


def safe_float(value, default: float = 0.0) -> float:
    try:
        v = float(value)
        return v if pd.notna(v) else default
    except (TypeError, ValueError):
        return default
