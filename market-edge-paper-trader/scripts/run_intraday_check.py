"""
Intraday position monitor — runs hourly during US market hours.

For every open paper trade, fetches the latest intraday price (5-min bars).
Closes the position immediately if the current price has crossed the
stop-loss or take-profit level.  Does NOT generate new signals — that is
handled by the EOD scan (run_scan.py).
"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date, datetime
import pandas as pd

from app.config import PLN_USD_RATE, COMMISSION_PCT, SLIPPAGE_PCT
from app.database import get_connection, db_cursor
from app.data_provider import fetch_ohlcv
from app.paper_broker import PaperBroker
from app.portfolio import Portfolio


def _is_market_open() -> bool:
    """True if current UTC time is within extended US market hours."""
    now = datetime.utcnow()
    # US pre-market starts ~13:00 UTC; regular session ends ~20:00 UTC
    return now.weekday() < 5 and 13 <= now.hour < 21


def _get_open_trades(run_mode: str) -> list[dict]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM trades WHERE status='open' AND run_mode=?",
            (run_mode,)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _fetch_current_price(ticker: str) -> dict | None:
    """
    Return dict with current intraday bar data: open, high, low, close, time.
    Uses 5-minute bars for the current session; falls back to daily if empty.
    """
    try:
        df = fetch_ohlcv(ticker, period="1d", interval="5m")
        if not df.empty:
            row = df.iloc[-1]
            return {
                "close": float(row["close"]),
                "high":  float(row["high"]),
                "low":   float(row["low"]),
                "open":  float(row["open"]),
                "time":  str(row.name),
            }
        # Fallback: use today's daily bar if 5-min is unavailable
        df_day = fetch_ohlcv(ticker, period="2d", interval="1d")
        if not df_day.empty:
            row = df_day.iloc[-1]
            return {
                "close": float(row["close"]),
                "high":  float(row["high"]),
                "low":   float(row["low"]),
                "open":  float(row["open"]),
                "time":  str(row.name),
            }
    except Exception as e:
        print(f"  [warn] price fetch {ticker}: {e}")
    return None


def run_intraday_check(run_mode: str = "live", verbose: bool = True) -> dict:
    today = date.today()
    now_str = datetime.utcnow().strftime("%H:%M UTC")

    portfolio = Portfolio(run_mode=run_mode)
    broker = PaperBroker(PLN_USD_RATE, run_mode=run_mode)
    trades = _get_open_trades(run_mode)

    if verbose:
        print(f"\n{'='*56}")
        print(f"  INTRADAY CHECK  {today}  {now_str}")
        print(f"  Open positions: {len(trades)}  |  Portfolio: {portfolio.total_value_pln:,.0f} PLN")
        print(f"{'='*56}")

    if not trades:
        if verbose:
            print("  No open positions to monitor.")
        return {"checked": 0, "closed": 0}

    closed_count = 0
    checked = 0

    for trade in trades:
        ticker    = trade["ticker"]
        trade_id  = trade["id"]
        entry_price = trade["entry_price"]
        stop_loss   = trade["stop_loss"]
        take_profit = trade["take_profit"]
        shares      = trade["shares"]
        entry_date_str = trade["entry_date"]
        max_hold    = trade.get("max_holding_days", 10)

        price_data = _fetch_current_price(ticker)
        time.sleep(0.15)  # rate-limit yfinance
        checked += 1

        if price_data is None:
            if verbose:
                print(f"  ? {ticker:8s}  no price data")
            continue

        current_price = price_data["close"]
        current_high  = price_data["high"]
        current_low   = price_data["low"]
        holding_days  = (today - date.fromisoformat(entry_date_str)).days

        # Update open P&L even if we don't close
        broker.update_open_trade_pnl(trade_id, current_price, holding_days)

        exit_price  = None
        exit_reason = None

        sl_hit = current_low  <= stop_loss
        tp_hit = current_high >= take_profit

        if current_price <= stop_loss:
            # Current price already below SL — fill at stop (or current if worse)
            exit_price  = min(current_price, stop_loss)
            exit_reason = "stop_loss"
        elif current_price >= take_profit:
            exit_price  = max(current_price, take_profit)
            exit_reason = "take_profit"
        elif sl_hit and tp_hit:
            # Both SL and TP touched this bar — use proximity heuristic
            if abs(current_price - stop_loss) <= abs(current_price - take_profit):
                exit_price  = stop_loss
                exit_reason = "stop_loss"
            else:
                exit_price  = take_profit
                exit_reason = "take_profit"
        elif sl_hit:
            exit_price  = stop_loss
            exit_reason = "stop_loss"
        elif tp_hit:
            exit_price  = take_profit
            exit_reason = "take_profit"
        elif holding_days >= max_hold:
            exit_price  = current_price
            exit_reason = "max_holding_days"

        if exit_price is not None:
            broker.close_trade(trade_id, exit_price, today, exit_reason)
            closed_count += 1
            pnl_usd = (exit_price - entry_price) * shares
            pnl_pln = pnl_usd * PLN_USD_RATE
            arrow = "+" if pnl_pln >= 0 else ""
            if verbose:
                print(f"  - CLOSE {ticker:8s} [{exit_reason:25s}] "
                      f"{arrow}{pnl_pln:,.0f} PLN  (price={exit_price:.2f})")
        else:
            pnl_usd = (current_price - entry_price) * shares
            pnl_pln = pnl_usd * PLN_USD_RATE
            sign = "+" if pnl_pln >= 0 else ""
            if verbose:
                print(f"    HOLD  {ticker:8s}  price={current_price:.2f}  "
                      f"SL={stop_loss:.2f}  TP={take_profit:.2f}  "
                      f"P&L: {sign}{pnl_pln:,.0f} PLN")

    # Refresh portfolio and save intraday snapshot
    portfolio._refresh()
    from app.scanner import _get_prev_snapshot_value
    prev_value = _get_prev_snapshot_value(run_mode)
    daily_pnl  = portfolio.total_value_pln - prev_value
    portfolio.save_snapshot(today, daily_pnl)

    if verbose:
        sign = "+" if daily_pnl >= 0 else ""
        print(f"\n  Portfolio: {portfolio.total_value_pln:,.0f} PLN  "
              f"Daily P&L: {sign}{daily_pnl:,.0f} PLN")
        print(f"  Checked {checked} positions, closed {closed_count}.")

    return {"checked": checked, "closed": closed_count}


if __name__ == "__main__":
    if not _is_market_open():
        print("Market is closed — nothing to do.")
        sys.exit(0)
    run_intraday_check(run_mode="live", verbose=True)
