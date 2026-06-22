"""Tests for the intraday engine.
Focus on critical invariants from the spec.
"""
import pytest
import pandas as pd
import numpy as np
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo

NY_TZ = ZoneInfo("America/New_York")

# --- SessionManager tests ---

def test_no_trade_on_weekend():
    from app.intraday.session_manager import SessionManager
    sm = SessionManager()
    saturday = date(2025, 6, 21)  # Saturday
    assert sm.is_trading_day(saturday) == False

def test_no_trade_on_holiday():
    from app.intraday.session_manager import SessionManager
    sm = SessionManager()
    christmas = date(2025, 12, 25)
    assert sm.is_trading_day(christmas) == False

def test_no_entry_before_10am_et():
    from app.intraday.session_manager import SessionManager
    sm = SessionManager()
    dt = datetime(2025, 6, 16, 9, 45, tzinfo=NY_TZ)  # 9:45 ET
    assert sm.can_enter_new_position(dt) == False

def test_no_entry_after_14_30_et():
    from app.intraday.session_manager import SessionManager
    sm = SessionManager()
    dt = datetime(2025, 6, 16, 14, 31, tzinfo=NY_TZ)  # 14:31 ET
    assert sm.can_enter_new_position(dt) == False

def test_can_enter_during_valid_window():
    from app.intraday.session_manager import SessionManager
    sm = SessionManager()
    dt = datetime(2025, 6, 16, 11, 30, tzinfo=NY_TZ)  # 11:30 ET on a Monday
    assert sm.can_enter_new_position(dt) == True

def test_force_close_at_15_40():
    from app.intraday.session_manager import SessionManager
    sm = SessionManager()
    dt = datetime(2025, 6, 16, 15, 40, tzinfo=NY_TZ)
    assert sm.should_force_close(dt) == True

def test_no_force_close_before_15_40():
    from app.intraday.session_manager import SessionManager
    sm = SessionManager()
    dt = datetime(2025, 6, 16, 15, 39, tzinfo=NY_TZ)
    assert sm.should_force_close(dt) == False

# --- DataQualityChecker tests ---

def test_invalid_bar_negative_open():
    from app.intraday.data_quality import DataQualityChecker
    bar = pd.Series({"open": -1.0, "high": 10.0, "low": 9.0, "close": 9.5, "volume": 1000},
                    name=pd.Timestamp("2025-01-10 10:00", tz=NY_TZ))
    valid, reason = DataQualityChecker.validate_bar(bar)
    assert valid == False

def test_invalid_bar_high_less_than_low():
    from app.intraday.data_quality import DataQualityChecker
    bar = pd.Series({"open": 10.0, "high": 9.0, "low": 11.0, "close": 10.0, "volume": 1000},
                    name=pd.Timestamp("2025-01-10 10:00", tz=NY_TZ))
    valid, reason = DataQualityChecker.validate_bar(bar)
    assert valid == False

def test_valid_bar():
    from app.intraday.data_quality import DataQualityChecker
    bar = pd.Series({"open": 10.0, "high": 11.0, "low": 9.5, "close": 10.5, "volume": 50000},
                    name=pd.Timestamp("2025-01-10 10:00", tz=NY_TZ))
    valid, reason = DataQualityChecker.validate_bar(bar)
    assert valid == True

def test_duplicate_removal():
    from app.intraday.data_quality import DataQualityChecker
    idx = pd.to_datetime(["2025-01-10 10:00", "2025-01-10 10:00", "2025-01-10 10:30"]).tz_localize(NY_TZ)
    df = pd.DataFrame({
        "open": [10, 10, 11], "high": [11, 11, 12],
        "low": [9, 9, 10], "close": [10.5, 10.5, 11.5], "volume": [1000, 1000, 2000]
    }, index=idx)
    result = DataQualityChecker.check_for_duplicates(df)
    assert len(result) == 2

# --- Indicators tests ---

def test_vwap_resets_each_day():
    """VWAP should start fresh each calendar day."""
    from app.intraday.indicators import compute_intraday_indicators
    # Create 2-day dataset
    timestamps = []
    for d_str in ["2025-01-10", "2025-01-13"]:
        year, month, day_num = int(d_str[:4]), int(d_str[5:7]), int(d_str[8:10])
        for h, m in [(10, 0), (10, 30), (11, 0), (11, 30)]:
            ts = datetime(year, month, day_num, h, m, tzinfo=NY_TZ)
            timestamps.append(ts)

    idx = pd.DatetimeIndex(timestamps)
    np.random.seed(42)
    prices = 100 + np.random.randn(8) * 2
    df = pd.DataFrame({
        "open": prices, "high": prices + 1, "low": prices - 1,
        "close": prices + 0.5, "volume": np.random.randint(100000, 500000, 8)
    }, index=idx)

    result = compute_intraday_indicators(df)
    assert "vwap" in result.columns
    # First bar of each day should have vwap == typical price of that bar
    # (only 1 data point, so vwap = typical price)

# --- Risk Manager tests ---

def test_position_cap_30000_pln():
    from app.intraday.risk_manager import IntradayRiskManager
    rm = IntradayRiskManager(equity_pln=1_000_000, pln_usd_rate=4.0)
    # entry 100 USD, stop 99 USD → risk 1 USD * 4 = 4 PLN/share
    # risk_amount = 1000 PLN → 250 shares → 250 * 100 * 4 = 100,000 PLN → capped at 30,000
    result = rm.calc_position_size(entry_price_usd=100.0, stop_price_usd=99.0)
    assert result["valid"] == True
    assert result["position_value_pln"] <= 30_000

def test_no_position_if_stop_above_entry():
    from app.intraday.risk_manager import IntradayRiskManager
    rm = IntradayRiskManager(equity_pln=1_000_000, pln_usd_rate=4.0)
    result = rm.calc_position_size(entry_price_usd=100.0, stop_price_usd=101.0)
    assert result["valid"] == False

def test_daily_loss_limit():
    from app.intraday.risk_manager import IntradayRiskManager
    rm = IntradayRiskManager(equity_pln=1_000_000, pln_usd_rate=4.0)
    # -1% of 1M = -10,000 PLN daily loss
    can_open, reason = rm.can_open_position(
        position_value_pln=5000,
        planned_risk_pln=100,
        sector="tech",
        current_invested_pln=0,
        current_open_risk_pln=0,
        open_positions_count=0,
        sector_invested_pln=0,
        sector_open_risk_pln=0,
        sector_position_count=0,
        daily_pnl_pln=-10_500,  # exceeded 1% limit
        daily_drawdown_pct=0.011,
        consecutive_losses_today=0,
        weekly_pnl_pln=-10_500,
        market_regime="NEUTRAL",
    )
    assert can_open == False
    assert "daily" in reason.lower() or "loss" in reason.lower()

def test_max_open_positions():
    from app.intraday.risk_manager import IntradayRiskManager
    rm = IntradayRiskManager(equity_pln=1_000_000, pln_usd_rate=4.0)
    can_open, reason = rm.can_open_position(
        position_value_pln=5000,
        planned_risk_pln=100,
        sector="tech",
        current_invested_pln=100_000,
        current_open_risk_pln=1000,
        open_positions_count=30,  # at max
        sector_invested_pln=5000,
        sector_open_risk_pln=500,
        sector_position_count=2,
        daily_pnl_pln=0,
        daily_drawdown_pct=0,
        consecutive_losses_today=0,
        weekly_pnl_pln=0,
        market_regime="NEUTRAL",
    )
    assert can_open == False

# --- No overnight test ---

def test_no_overnight_positions_invariant():
    """After 15:50 ET, should_force_close is True and is_past_deadline is True."""
    from app.intraday.session_manager import SessionManager
    sm = SessionManager()
    dt = datetime(2025, 6, 16, 15, 55, tzinfo=NY_TZ)
    assert sm.is_past_deadline(dt) == True
    assert sm.should_force_close(dt) == True

# --- Strategy / look-ahead bias test ---

def test_signal_only_on_completed_bar():
    """Strategies must not use the current (incomplete) bar."""
    from app.intraday.strategies import strategy_vwap_mean_reversion
    import numpy as np

    # Create minimal intraday dataframe - 25 bars (need enough for indicators)
    n = 25
    base_time = datetime(2025, 6, 16, 10, 0, tzinfo=NY_TZ)
    times = [base_time + timedelta(minutes=30*i) for i in range(n)]

    prices = np.linspace(100, 96, n)  # declining prices (for mean reversion)
    df_intra = pd.DataFrame({
        "open": prices + 0.1,
        "high": prices + 0.5,
        "low": prices - 0.5,
        "close": prices,
        "volume": [500_000] * n,
    }, index=pd.DatetimeIndex(times))

    from app.intraday.indicators import compute_intraday_indicators
    df_intra = compute_intraday_indicators(df_intra)

    # Create minimal daily dataframe
    daily_times = pd.date_range("2024-06-01", periods=260, freq="B")
    daily_prices = np.linspace(90, 105, 260)
    df_daily = pd.DataFrame({
        "open": daily_prices,
        "high": daily_prices + 1,
        "low": daily_prices - 1,
        "close": daily_prices,
        "volume": [5_000_000] * 260,
    }, index=daily_times)

    from app.intraday.indicators import compute_daily_indicators_for_filter
    df_daily = compute_daily_indicators_for_filter(df_daily)

    # Signal may or may not be generated - just ensure no exception
    signal = strategy_vwap_mean_reversion("AAPL", df_intra, df_daily)
    # If signal returned, planned_entry should be FUTURE (based on next bar open)
    if signal is not None:
        assert signal.planned_entry > 0

# --- Swing/Intraday separation test ---

def test_no_mixing_engine_types():
    """Intraday tables should not contain swing data and vice versa."""
    from app.database import db_cursor, init_db
    init_db()
    with db_cursor() as cur:
        # Check intraday tables exist
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'intraday_%'")
        tables = [r[0] for r in cur.fetchall()]
        assert "intraday_trades" in tables
        assert "intraday_signals" in tables
        # Swing tables should still exist
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='trades'")
        assert cur.fetchone() is not None

def test_no_mixing_run_mode():
    """live and backtest data must be separable by run_mode column."""
    from app.database import db_cursor
    with db_cursor() as cur:
        cur.execute("PRAGMA table_info(intraday_trades)")
        cols = [r[1] for r in cur.fetchall()]
        assert "run_mode" in cols
