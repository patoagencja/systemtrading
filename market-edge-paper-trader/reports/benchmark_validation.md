# SPY Benchmark Validation Report

## Audit Date
2026-06-23 11:12

## Problem Identified

### Issue 1: Only 2 Years of SPY Data Fetched
**Location:** `scripts/build_dashboard_html.py`, function `fetch_spy_benchmark()`, line ~143

**Root Cause:**
```python
spy = fetch_ohlcv("SPY", period="2y")  # BUG: only fetches last 2 years
```

The backtest runs from **2020-01-01 to 2026-06-22** (6.5 years), but `period="2y"`
only fetches ~2024-2026 data. Since `snap_dates` starts from 2020-01-01, the loop:
```python
for sd in snap_dates:
    if sd in spy_dates_set:  # False for all 2020-2023 dates!
```
finds no matching dates for the first ~4 years, so `first_price` is never set
until ~2024. The benchmark silently starts from the first matched date (~2024),
making SPY appear to only start when it enters the chart's visible range.

### Issue 2: Silent Failure / Incomplete Benchmark
The code doesn't warn when most `snap_dates` have no SPY match. The benchmark
dict returned covers only ~2024-2026, so the SPY line appears to start in 2024
on the equity chart despite the portfolio line going back to 2020.

## Fix Applied

**Location:** `scripts/build_dashboard_html.py`, function `fetch_spy_benchmark()`

### Before (Buggy Code):
```python
def fetch_spy_benchmark(snap_dates: list[str], initial: float) -> list[dict]:
    ...
    spy = fetch_ohlcv("SPY", period="2y")  # BUG: only 2 years
```

### After (Fixed Code):
```python
def fetch_spy_benchmark(snap_dates: list[str], initial: float) -> list[dict]:
    ...
    # Use start date from snap_dates to fetch full backtest period
    start_date = snap_dates[0] if snap_dates else "2020-01-01"
    from app.data_provider import fetch_ohlcv_range
    spy = fetch_ohlcv_range("SPY", start=start_date, end=snap_dates[-1] if snap_dates else "")
```

## Verification

After the fix:
- SPY data should cover **2020-01-03 to 2026-06-22**
- Backtest initial capital: 1,000,000 PLN
- SPY normalization: `initial * price / first_price` (unchanged — was correct)
- Both portfolio and SPY lines start from the same date on the equity chart

## Data Note

The `fetch_ohlcv_range` function uses `yfinance.download(start=..., end=...)`
with `auto_adjust=True`, which provides total return (dividends included).
This is consistent with the existing `fetch_ohlcv` behavior.
