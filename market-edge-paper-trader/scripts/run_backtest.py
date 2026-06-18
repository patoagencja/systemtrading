"""
Backtest: simulate day-by-day trading over the last 12 months.
No look-ahead: for each simulated day, only data up to that day is used.
Results are saved to the database and an equity curve is printed.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import time
import pandas as pd
from datetime import date, timedelta
from dateutil.relativedelta import relativedelta

from app.config import (
    INITIAL_CAPITAL_PLN, PLN_USD_RATE, MIN_SCORE_TO_OPEN,
    MIN_HISTORY_BARS, STRATEGY_MAX_HOLDING, COMMISSION_PCT, SLIPPAGE_PCT,
)
from app.database import db_cursor, get_connection
from app.data_provider import fetch_ohlcv_range, fetch_ohlcv
from app.indicators import compute_indicators, get_latest_row
from app.strategies import run_all_strategies
from app.risk_manager import RiskManager
from app.paper_broker import PaperBroker
from app.portfolio import Portfolio
from app.utils import trading_days_between


def run_backtest():
    end_date = date.today() - timedelta(days=1)
    start_date = end_date - relativedelta(months=12)

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

    open_trades: list[dict] = []  # in-memory for speed
    cash = INITIAL_CAPITAL_PLN
    total_value = INITIAL_CAPITAL_PLN
    prev_value = INITIAL_CAPITAL_PLN

    equity_curve = []

    for sim_date in trading_days:
        sim_date_str = str(sim_date)

        # Slice all data up to sim_date (no look-ahead)
        def slice_df(df: pd.DataFrame) -> pd.DataFrame:
            return df[df.index.date <= sim_date]  # type: ignore

        spy_today = slice_df(spy_full) if not spy_full.empty else pd.DataFrame()

        # ── Update open trades ──────────────────────────────────────
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
            current_high = float(latest["high"])
            current_low = float(latest["low"])
            sma50 = latest.get("sma50")

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

            if sl_hit and tp_hit:
                exit_price = stop_loss
                exit_reason = "stop_loss"
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
                # Apply sell-side slippage + commission
                slip = exit_price * SLIPPAGE_PCT
                comm = exit_price * COMMISSION_PCT
                eff_exit = exit_price - slip
                exit_cost_pln = comm * shares * PLN_USD_RATE
                pnl_usd = (eff_exit - entry_price) * shares
                pnl_pln = pnl_usd * PLN_USD_RATE - exit_cost_pln
                pnl_pct = (eff_exit - entry_price) / entry_price * 100
                risk_pln = trade["risk_pln"]
                r_multiple = pnl_pln / risk_pln if risk_pln > 0 else 0

                cash += trade["position_value_pln"] + pnl_pln

                broker.close_trade(trade["db_id"], exit_price, sim_date, exit_reason)
                closed_today += 1
            else:
                # Update unrealized PnL
                broker.update_open_trade_pnl(trade["db_id"], current_close, holding_days)
                trade["current_price"] = current_close
                still_open.append(trade)

        open_trades = still_open

        # ── Scan for new signals ────────────────────────────────────
        open_tickers = {t["ticker"] for t in open_trades}
        invested_pln = sum(t["position_value_pln"] for t in open_trades)

        # Recompute total value including unrealized
        unrealized = sum(
            (t.get("current_price", t["entry_price"]) - t["entry_price"]) * t["shares"] * PLN_USD_RATE
            for t in open_trades
        )
        total_value = cash + invested_pln  # position_value_pln is at entry; approximate
        risk_mgr = RiskManager(total_value, PLN_USD_RATE)
        new_today = 0

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
            # Only act on bar if it belongs to sim_date
            if str(df_slice.iloc[-1].name.date()) != sim_date_str:  # type: ignore
                continue

            sector_etf_df = None
            if item.get("sector_etf"):
                etf_full = all_data.get(item["sector_etf"])
                if etf_full is not None:
                    sector_etf_df = slice_df(etf_full)

            sigs = run_all_strategies(ticker, df_slice, spy_today, sector_etf_df)
            for s in sigs:
                if s.score >= MIN_SCORE_TO_OPEN:
                    all_signals.append(s)

        all_signals.sort(key=lambda s: s.score, reverse=True)

        for sig in all_signals:
            if sig.ticker in open_tickers:
                continue
            if len(open_trades) >= 40:
                break

            sizing = risk_mgr.calc_position_size(sig.entry_price, sig.stop_loss)
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
                sig, sizing["shares"], sizing["position_value_pln"],
                sizing["risk_pln"], sim_date,
            )
            # Apply buy-side slippage + commission to cash
            entry_cost_pln = sizing["position_value_pln"] * (COMMISSION_PCT + SLIPPAGE_PCT)
            cash -= sizing["position_value_pln"] + entry_cost_pln
            invested_pln += sizing["position_value_pln"]

            open_trades.append({
                "db_id": db_id,
                "ticker": sig.ticker,
                "strategy": sig.strategy,
                "entry_date": str(sim_date),
                "entry_price": sig.entry_price,
                "stop_loss": sig.stop_loss,
                "take_profit": sig.take_profit,
                "shares": sizing["shares"],
                "position_value_pln": sizing["position_value_pln"],
                "risk_pln": sizing["risk_pln"],
                "max_holding_days": sig.max_holding_days,
                "current_price": sig.entry_price,
            })
            open_tickers.add(sig.ticker)
            new_today += 1

        total_value = cash + sum(t["position_value_pln"] for t in open_trades)
        daily_pnl = total_value - prev_value
        prev_value = total_value
        pnl_total = total_value - INITIAL_CAPITAL_PLN

        equity_curve.append({"date": sim_date, "value": total_value, "pnl": pnl_total})

        # Save snapshot directly
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
            print(f"  {sim_date}  +{new_today} opened  -{closed_today} closed  "
                  f"open={len(open_trades):2d}  "
                  f"P&L: {sign}{pnl_total:,.0f} PLN  "
                  f"({sign}{pnl_total/INITIAL_CAPITAL_PLN*100:.2f}%)")

    broker.update_strategy_stats()
    _print_backtest_summary(equity_curve)


def _print_backtest_summary(equity_curve: list):
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

    print(f"\n{'='*60}")
    print(f"  BACKTEST RESULTS")
    print(f"{'='*60}")
    print(f"  Final value        : {final:>15,.2f} PLN")
    sign = "+" if pnl >= 0 else ""
    print(f"  Total P&L          : {sign}{pnl:>14,.2f} PLN")
    print(f"  Total return       : {sign}{ret:>13.2f} %")
    print(f"  Max drawdown       : {max_dd:>14.2f} %")
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
    # Try to import dateutil; if missing give a friendly message
    try:
        from dateutil.relativedelta import relativedelta
    except ImportError:
        print("Install python-dateutil: pip install python-dateutil")
        sys.exit(1)
    run_backtest()
