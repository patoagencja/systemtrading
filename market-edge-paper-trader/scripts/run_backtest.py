"""
Backtest: simulate day-by-day trading over a historical window.
No look-ahead: for each simulated day, only data up to that day is used.
Entry timing: signal generated on day T → position entered at day T+1 open.
Results are saved to the database and an equity curve is printed.

Usage:
  python scripts/run_backtest.py                    # default: last 12 months
  python scripts/run_backtest.py --months 18        # last N months
  python scripts/run_backtest.py --start 2023-01-01 # explicit start date
"""
import sys
import os
import argparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import time
import pandas as pd
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta
from dataclasses import replace as dc_replace

from app.config import (
    INITIAL_CAPITAL_PLN, PLN_USD_RATE, MIN_SCORE_TO_OPEN,
    MIN_HISTORY_BARS, STRATEGY_MAX_HOLDING, COMMISSION_PCT, SLIPPAGE_PCT,
    MIN_AVG_VOLUME,
)
from app.database import db_cursor, get_connection
from app.data_provider import fetch_ohlcv_range, fetch_ohlcv
from app.indicators import compute_indicators, get_latest_row
from app.strategies import run_all_strategies
from app.risk_manager import RiskManager
from app.paper_broker import PaperBroker
from app.portfolio import Portfolio
from app.utils import trading_days_between


def run_backtest(start_date: date = None, months: int = 12):
    end_date = date.today() - timedelta(days=1)
    if start_date is None:
        start_date = end_date - relativedelta(months=months)

    print(f"\n{'='*60}")
    print(f"  BACKTEST: {start_date} → {end_date}")
    print(f"  Initial capital: {INITIAL_CAPITAL_PLN:,.0f} PLN")
    print(f"{'='*60}")

    # Clear previous backtest data only (keep live trades)
    with db_cursor() as cur:
        cur.execute("DELETE FROM trades WHERE run_mode='backtest'")
        cur.execute("DELETE FROM signals WHERE run_mode='backtest'")
        cur.execute("DELETE FROM portfolio_snapshots WHERE run_mode='backtest'")
        cur.execute("DELETE FROM strategy_stats WHERE run_mode='backtest'")

    # Load watchlist
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT ticker, sector, is_etf, sector_etf FROM watchlist WHERE active=1")
        cols = [d[0] for d in cur.description]
        watchlist = [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()

    print(f"  Loading historical data for {len(watchlist)} tickers...")

    # Fetch all data upfront (2 years to have enough history before backtest window)
    all_data: dict[str, pd.DataFrame] = {}
    for item in watchlist:
        ticker = item["ticker"]
        df = fetch_ohlcv(ticker, period="2y")
        if df.empty or len(df) < MIN_HISTORY_BARS:
            continue
        df = compute_indicators(df)
        all_data[ticker] = df
        time.sleep(0.15)

    print(f"  Loaded data for {len(all_data)} tickers.\n")

    spy_full = all_data.get("SPY", pd.DataFrame())

    broker = PaperBroker(PLN_USD_RATE, run_mode="backtest")
    trading_days = trading_days_between(start_date, end_date)

    open_trades: list[dict] = []
    # pending_entries: signals from day T waiting to enter at day T+1 open
    # each item: {"signal": Signal, "signal_date": date, "avg_volume": float}
    pending_entries: list[dict] = []
    cash = INITIAL_CAPITAL_PLN
    total_value = INITIAL_CAPITAL_PLN
    prev_value = INITIAL_CAPITAL_PLN

    equity_curve = []

    for sim_date in trading_days:
        sim_date_str = str(sim_date)

        def slice_df(df: pd.DataFrame) -> pd.DataFrame:
            return df[df.index.date <= sim_date]  # type: ignore

        spy_today = slice_df(spy_full) if not spy_full.empty else pd.DataFrame()

        # ── Phase 1: Enter pending signals at today's open ─────────────
        still_pending = []
        new_today = 0
        open_tickers = {t["ticker"] for t in open_trades}
        invested_pln = sum(t["position_value_pln"] for t in open_trades)
        risk_mgr = RiskManager(total_value, PLN_USD_RATE)

        for pe in pending_entries:
            sig = pe["signal"]
            avg_volume = pe["avg_volume"]

            if sig.ticker in open_tickers:
                continue  # already have a position

            df_full = all_data.get(sig.ticker)
            if df_full is None:
                continue
            df_slice = slice_df(df_full)
            if df_slice.empty or str(df_slice.iloc[-1].name.date()) != sim_date_str:  # type: ignore
                still_pending.append(pe)  # no data yet, try next day
                continue

            actual_entry = float(df_slice.iloc[-1]["open"])
            if actual_entry <= 0:
                continue

            # Use actual open as entry; keep original SL/TP (ATR-based from signal day)
            actual_sig = dc_replace(sig, entry_price=actual_entry)
            sizing = risk_mgr.calc_position_size(actual_entry, sig.stop_loss)
            if not sizing["valid"]:
                continue

            can_open, _ = risk_mgr.can_open_position(
                sizing["position_value_pln"], invested_pln, len(open_trades)
            )
            if not can_open:
                continue
            if sizing["position_value_pln"] > cash:
                continue

            db_id = broker.open_trade(
                actual_sig, sizing["shares"], sizing["position_value_pln"],
                sizing["risk_pln"], sim_date, avg_volume,
            )
            entry_cost_pln = sizing["position_value_pln"] * (COMMISSION_PCT + SLIPPAGE_PCT)
            cash -= sizing["position_value_pln"] + entry_cost_pln
            invested_pln += sizing["position_value_pln"]

            open_trades.append({
                "db_id": db_id,
                "ticker": sig.ticker,
                "strategy": sig.strategy,
                "entry_date": str(sim_date),
                "entry_price": actual_entry,
                "stop_loss": sig.stop_loss,
                "take_profit": sig.take_profit,
                "shares": sizing["shares"],
                "position_value_pln": sizing["position_value_pln"],
                "risk_pln": sizing["risk_pln"],
                "max_holding_days": sig.max_holding_days,
                "current_price": actual_entry,
                "avg_volume": avg_volume,
            })
            open_tickers.add(sig.ticker)
            new_today += 1

        pending_entries = still_pending

        # ── Phase 2: Update & close open positions ─────────────────────
        closed_today = 0
        still_open = []
        for trade in open_trades:
            ticker = trade["ticker"]
            df_full = all_data.get(ticker)
            if df_full is None:
                still_open.append(trade)
                continue

            df_slice = slice_df(df_full)
            if df_slice.empty:
                still_open.append(trade)
                continue

            latest = df_slice.iloc[-1]
            if str(latest.name.date()) != sim_date_str:  # type: ignore
                still_open.append(trade)
                continue

            current_close = float(latest["close"])
            current_open  = float(latest["open"])
            current_high  = float(latest["high"])
            current_low   = float(latest["low"])
            sma50 = latest.get("sma50")
            avg_volume = trade.get("avg_volume", 0)

            entry_date_obj = date.fromisoformat(trade["entry_date"])
            holding_days = (sim_date - entry_date_obj).days
            stop_loss = trade["stop_loss"]
            take_profit = trade["take_profit"]
            max_hold = trade.get("max_holding_days", 10)
            entry_price = trade["entry_price"]
            shares = trade["shares"]

            exit_price = None
            exit_reason = None

            sl_hit = current_low <= stop_loss
            tp_hit = current_high >= take_profit

            if current_open <= stop_loss:
                exit_price = current_open
                exit_reason = "stop_loss"
            elif current_open >= take_profit:
                exit_price = current_open
                exit_reason = "take_profit"
            elif sl_hit and tp_hit:
                if abs(current_open - stop_loss) <= abs(current_open - take_profit):
                    exit_price = stop_loss
                    exit_reason = "stop_loss"
                else:
                    exit_price = take_profit
                    exit_reason = "take_profit"
            elif sl_hit:
                exit_price = stop_loss
                exit_reason = "stop_loss"
            elif tp_hit:
                exit_price = take_profit
                exit_reason = "take_profit"
            elif holding_days >= max_hold:
                exit_price = current_close
                exit_reason = "max_holding_days"
            elif (not pd.isna(sma50) and
                  current_close < sma50 and
                  current_close < entry_price):
                exit_price = current_close
                exit_reason = "technical_exit_below_sma50"

            if exit_price is not None:
                slip = exit_price * SLIPPAGE_PCT
                comm = exit_price * COMMISSION_PCT
                eff_exit = exit_price - slip
                exit_cost_pln = comm * shares * PLN_USD_RATE
                pnl_usd = (eff_exit - entry_price) * shares
                pnl_pln = pnl_usd * PLN_USD_RATE - exit_cost_pln
                cash += trade["position_value_pln"] + pnl_pln

                broker.close_trade(trade["db_id"], exit_price, sim_date, exit_reason, avg_volume)
                closed_today += 1
            else:
                broker.update_open_trade_pnl(trade["db_id"], current_close, holding_days)
                trade["current_price"] = current_close
                still_open.append(trade)

        open_trades = still_open

        # ── Phase 3: Scan for new signals → queue for tomorrow ─────────
        invested_pln = sum(t["position_value_pln"] for t in open_trades)
        total_value = cash + invested_pln
        risk_mgr = RiskManager(total_value, PLN_USD_RATE)
        open_tickers = {t["ticker"] for t in open_trades}
        pending_tickers = {pe["signal"].ticker for pe in pending_entries}

        all_signals = []
        for item in watchlist:
            ticker = item["ticker"]
            df_full = all_data.get(ticker)
            if df_full is None:
                continue
            df_slice = slice_df(df_full)
            if len(df_slice) < MIN_HISTORY_BARS:
                continue
            if get_latest_row(df_slice) is None:
                continue
            if str(df_slice.iloc[-1].name.date()) != sim_date_str:  # type: ignore
                continue

            # Volume filter: skip thinly-traded tickers
            avg_vol = float(df_slice["volume"].tail(20).mean())
            if avg_vol < MIN_AVG_VOLUME:
                continue

            sector_etf_df = None
            if item.get("sector_etf"):
                etf_full = all_data.get(item["sector_etf"])
                if etf_full is not None:
                    sector_etf_df = slice_df(etf_full)

            sigs = run_all_strategies(ticker, df_slice, spy_today, sector_etf_df)
            for s in sigs:
                if s.score >= MIN_SCORE_TO_OPEN:
                    all_signals.append((s, avg_vol))

        all_signals.sort(key=lambda x: x[0].score, reverse=True)

        for sig, avg_vol in all_signals:
            if sig.ticker in open_tickers or sig.ticker in pending_tickers:
                continue
            if len(open_trades) + len(pending_entries) >= 40:
                break
            pending_entries.append({"signal": sig, "signal_date": sim_date, "avg_volume": avg_vol})
            pending_tickers.add(sig.ticker)

        total_value = cash + sum(t["position_value_pln"] for t in open_trades)
        daily_pnl = total_value - prev_value
        prev_value = total_value
        pnl_total = total_value - INITIAL_CAPITAL_PLN

        equity_curve.append({"date": sim_date, "value": total_value, "pnl": pnl_total})

        with db_cursor() as cur:
            cur.execute("""
                INSERT OR REPLACE INTO portfolio_snapshots
                (snapshot_date, run_mode, total_value_pln, cash_pln, invested_pln,
                 open_positions, daily_pnl_pln, total_pnl_pln, total_return_pct,
                 max_drawdown_pct, total_closed_trades)
                VALUES (?, 'backtest', ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """, (
                sim_date_str, round(total_value, 2), round(cash, 2),
                round(sum(t["position_value_pln"] for t in open_trades), 2),
                len(open_trades), round(daily_pnl, 2), round(pnl_total, 2),
                round(pnl_total / INITIAL_CAPITAL_PLN * 100, 4),
                len(equity_curve),
            ))

        if new_today or closed_today:
            sign = "+" if pnl_total >= 0 else ""
            print(f"  {sim_date}  +{new_today} entered  -{closed_today} closed  "
                  f"open={len(open_trades):2d}  pending={len(pending_entries):2d}  "
                  f"P&L: {sign}{pnl_total:,.0f} PLN  "
                  f"({sign}{pnl_total/INITIAL_CAPITAL_PLN*100:.2f}%)")

    broker.update_strategy_stats()
    _print_backtest_summary(equity_curve, start_date, end_date)


def _print_backtest_summary(equity_curve: list, start_date=None, end_date=None):
    if not equity_curve:
        print("No data.")
        return

    final = equity_curve[-1]["value"]
    pnl = final - INITIAL_CAPITAL_PLN
    ret = pnl / INITIAL_CAPITAL_PLN * 100
    values = [e["value"] for e in equity_curve]
    peak = values[0]
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        dd = (peak - v) / peak * 100
        max_dd = max(max_dd, dd)

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*), SUM(CASE WHEN pnl_pln>0 THEN 1 ELSE 0 END), SUM(pnl_pln) FROM trades WHERE status='closed' AND run_mode='backtest'")
        row = cur.fetchone()
        total_trades = row[0] or 0
        wins = row[1] or 0
        total_pnl_db = row[2] or 0
    finally:
        conn.close()

    # Sharpe ratio from daily returns
    daily_returns = []
    for i in range(1, len(values)):
        if values[i - 1] > 0:
            daily_returns.append((values[i] - values[i - 1]) / values[i - 1])
    sharpe = 0.0
    if len(daily_returns) > 1:
        import math
        mean_r = sum(daily_returns) / len(daily_returns)
        var = sum((r - mean_r) ** 2 for r in daily_returns) / (len(daily_returns) - 1)
        std_r = math.sqrt(var) if var > 0 else 0
        rf = (1.04) ** (1 / 252) - 1
        if std_r > 0:
            sharpe = (mean_r - rf) / std_r * math.sqrt(252)

    print(f"\n{'='*60}")
    print(f"  BACKTEST RESULTS")
    print(f"{'='*60}")
    print(f"  Final value        : {final:>15,.2f} PLN")
    sign = "+" if pnl >= 0 else ""
    print(f"  Total P&L          : {sign}{pnl:>14,.2f} PLN")
    print(f"  Total return       : {sign}{ret:>13.2f} %")
    print(f"  Max drawdown       : {max_dd:>14.2f} %")
    print(f"  Sharpe ratio       : {sharpe:>14.2f}")
    print(f"  Total closed trades: {total_trades:>15d}")
    if total_trades > 0:
        print(f"  Win rate           : {wins/total_trades*100:>13.1f} %")
    print(f"{'='*60}")
    print("\n  Equity curve (monthly):")
    monthly = {}
    for e in equity_curve:
        key = e["date"].strftime("%Y-%m")
        monthly[key] = e["value"]
    for ym, val in sorted(monthly.items()):
        sign = "+" if val >= INITIAL_CAPITAL_PLN else ""
        pnl_m = val - INITIAL_CAPITAL_PLN
        bar = "█" * int(abs(pnl_m) / INITIAL_CAPITAL_PLN * 200)
        print(f"  {ym}  {val:>12,.0f} PLN  {sign}{pnl_m/INITIAL_CAPITAL_PLN*100:.2f}%  {bar}")
    print()


if __name__ == "__main__":
    try:
        from dateutil.relativedelta import relativedelta
    except ImportError:
        print("Install python-dateutil: pip install python-dateutil")
        sys.exit(1)
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", help="Start date YYYY-MM-DD")
    parser.add_argument("--months", type=int, default=12, help="Number of months (default: 12)")
    args = parser.parse_args()
    start = date.fromisoformat(args.start) if args.start else None
    run_backtest(start_date=start, months=args.months)
