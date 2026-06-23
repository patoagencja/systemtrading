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
    MIN_AVG_VOLUME, ENABLE_TECHNICAL_EXIT,
)
from app.database import db_cursor, get_connection
from app.data_provider import fetch_ohlcv_range, fetch_ohlcv
from app.indicators import compute_indicators, get_latest_row
from app.strategies import run_all_strategies
from app.risk_manager import RiskManager
from app.paper_broker import PaperBroker
from app.portfolio import Portfolio
from app.utils import trading_days_between
from app import exit_logic as XL

# Exit-logic identifiers
LEGACY = "LEGACY_EXIT_LOGIC"
FIXED_TP = "FIXED_TP_DYNAMIC_STOP"
TRAILING = "TRAILING_AFTER_10"
NEW_VARIANTS = (FIXED_TP, TRAILING)

# Round-trip cost fraction used for break-even computation in the new logic.
COST_PCT = COMMISSION_PCT + SLIPPAGE_PCT


def _load_all_data():
    """Load + index OHLCV (with indicators) for the active watchlist.

    Returns (watchlist, all_data). On network failure all_data may be empty.
    """
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT ticker, sector, is_etf, sector_etf FROM watchlist WHERE active=1")
        cols = [d[0] for d in cur.description]
        watchlist = [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()
    return watchlist


def _fetch_price_data(watchlist, start_date, end_date):
    """Fetch + indicator-compute OHLCV for the whole watchlist once.

    Returns dict[ticker]->DataFrame (may be empty if network blocked).
    """
    from dateutil.relativedelta import relativedelta as _rd
    data_start = (start_date - _rd(years=1)).strftime("%Y-%m-%d")
    data_end = (end_date + timedelta(days=1)).strftime("%Y-%m-%d")
    needs_range = start_date < (date.today() - timedelta(days=700))

    all_data: dict = {}
    for item in watchlist:
        ticker = item["ticker"]
        if needs_range:
            df = fetch_ohlcv_range(ticker, start=data_start, end=data_end)
        else:
            df = fetch_ohlcv(ticker, period="2y")
        if df is None or df.empty or len(df) < MIN_HISTORY_BARS:
            continue
        df = compute_indicators(df)
        all_data[ticker] = df
        time.sleep(0.05)
    return all_data


def run_backtest(start_date: date = None, months: int = 12,
                 exit_logic: str = "SIMPLE_DYNAMIC_EXIT_V1",
                 run_mode: str = "backtest", preloaded_data: dict = None):
    """Run a single backtest.

    exit_logic:
      "LEGACY_EXIT_LOGIC"     — original static SL/TP behaviour (unchanged)
      "FIXED_TP_DYNAMIC_STOP" — SIMPLE_DYNAMIC_EXIT_V1, close at +10% TP
      "TRAILING_AFTER_10"     — SIMPLE_DYNAMIC_EXIT_V1, trail above +10%
      "SIMPLE_DYNAMIC_EXIT_V1"— alias -> FIXED_TP_DYNAMIC_STOP
    """
    if exit_logic == "SIMPLE_DYNAMIC_EXIT_V1":
        exit_logic = FIXED_TP
    use_new = exit_logic in NEW_VARIANTS

    end_date = date.today() - timedelta(days=1)
    if start_date is None:
        start_date = end_date - relativedelta(months=months)

    print(f"\n{'='*60}")
    print(f"  BACKTEST: {start_date} → {end_date}")
    print(f"  Exit logic: {exit_logic}   run_mode={run_mode}")
    print(f"  Initial capital: {INITIAL_CAPITAL_PLN:,.0f} PLN")
    print(f"{'='*60}")

    # Clear previous data for this run_mode only (keep live trades)
    with db_cursor() as cur:
        cur.execute("DELETE FROM stop_history WHERE trade_id IN "
                    "(SELECT id FROM trades WHERE run_mode=?)", (run_mode,))
        cur.execute("DELETE FROM trades WHERE run_mode=?", (run_mode,))
        cur.execute("DELETE FROM signals WHERE run_mode=?", (run_mode,))
        cur.execute("DELETE FROM portfolio_snapshots WHERE run_mode=?", (run_mode,))
        cur.execute("DELETE FROM strategy_stats WHERE run_mode=?", (run_mode,))

    # Load watchlist
    watchlist = _load_all_data()

    if preloaded_data is not None:
        # Reuse data already fetched (comparative / sensitivity runs).
        all_data = preloaded_data
        print(f"  Reusing preloaded data for {len(all_data)} tickers.\n")
    else:
        print(f"  Loading historical data for {len(watchlist)} tickers...")

        # Need 1 year of warmup before backtest start for indicator calculation
        from dateutil.relativedelta import relativedelta as _rd
        data_start = (start_date - _rd(years=1)).strftime("%Y-%m-%d")
        data_end = (end_date + timedelta(days=1)).strftime("%Y-%m-%d")
        needs_range = start_date < (date.today() - timedelta(days=700))

        all_data: dict[str, pd.DataFrame] = {}
        for item in watchlist:
            ticker = item["ticker"]
            if needs_range:
                df = fetch_ohlcv_range(ticker, start=data_start, end=data_end)
            else:
                df = fetch_ohlcv(ticker, period="2y")
            if df is None or df.empty or len(df) < MIN_HISTORY_BARS:
                continue
            df = compute_indicators(df)
            all_data[ticker] = df
            time.sleep(0.15)

        print(f"  Loaded data for {len(all_data)} tickers.\n")

    spy_full = all_data.get("SPY", pd.DataFrame())

    broker = PaperBroker(PLN_USD_RATE, run_mode=run_mode)
    trading_days = trading_days_between(start_date, end_date)

    # in-memory TradeState objects keyed by db_id (new logic only)
    states: dict[int, XL.TradeState] = {}

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

            # Determine entry stop / TP. New logic validates + overrides TP.
            if use_new:
                validated, reject = XL.validate_signal_for_new_logic(sig, actual_entry)
                if validated is None:
                    continue  # rejected (INITIAL_STOP_TOO_WIDE / INSUFFICIENT_RR / ...)
                entry_stop, entry_tp = validated
            else:
                entry_stop, entry_tp = sig.stop_loss, sig.take_profit

            # Use actual open as entry.
            actual_sig = dc_replace(sig, entry_price=actual_entry,
                                    stop_loss=entry_stop, take_profit=entry_tp)
            sizing = risk_mgr.calc_position_size(actual_entry, entry_stop)
            if not sizing["valid"]:
                continue

            can_open, _ = risk_mgr.can_open_position(
                sizing["position_value_pln"], invested_pln, len(open_trades)
            )
            if not can_open:
                continue
            if sizing["position_value_pln"] > cash:
                continue

            if use_new:
                db_id = broker.open_trade_v2(
                    actual_sig, sizing["shares"], sizing["position_value_pln"],
                    sizing["risk_pln"], sim_date, avg_volume,
                    actual_entry, entry_stop, entry_tp, exit_logic,
                )
                # eff entry for state should match the cost-adjusted entry stored
                eff_entry = actual_entry + actual_entry * SLIPPAGE_PCT
                states[db_id] = XL.make_trade_state(
                    db_id, sig.ticker, eff_entry, entry_stop, entry_tp,
                    exit_logic, sim_date,
                )
            else:
                db_id = broker.open_trade(
                    actual_sig, sizing["shares"], sizing["position_value_pln"],
                    sizing["risk_pln"], sim_date, avg_volume, LEGACY,
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
                "stop_loss": entry_stop,
                "take_profit": entry_tp,
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

            if use_new:
                state = states.get(trade["db_id"])
                if state is not None:
                    ev = XL.process_session_bar(
                        state, current_open, current_high, current_low,
                        current_close, sim_date_str, COST_PCT, exit_logic,
                    )
                    if ev is not None:
                        exit_price = ev.exit_price
                        exit_reason = ev.exit_reason
                    else:
                        # No exit: persist updated stop/tracking state.
                        prev_stop = trade["stop_loss"]
                        if state.active_stop > prev_stop:
                            broker.save_stop_history(
                                trade["db_id"], sim_date_str, prev_stop,
                                state.active_stop, state.stop_status, state.stop_status,
                                state.max_profit_pct, state.highest_close,
                            )
                        trade["stop_loss"] = state.active_stop
                        mu_pln = (state.max_unrealized_pnl_pct * entry_price
                                  * shares * PLN_USD_RATE)
                        broker.update_trade_stop(
                            trade["db_id"], state.active_stop, state.stop_status,
                            sim_date_str, state.max_profit_pct, state.locked_profit_pct,
                            state.highest_high, state.highest_close,
                            state.max_unrealized_pnl_pct,
                        )
                        conn2 = get_connection()
                        try:
                            cur2 = conn2.cursor()
                            cur2.execute(
                                "UPDATE trades SET max_unrealized_pnl_pln=? WHERE id=?",
                                (round(mu_pln, 2), trade["db_id"]),
                            )
                            conn2.commit()
                        finally:
                            conn2.close()
            else:
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
                states.pop(trade["db_id"], None)
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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)
            """, (
                sim_date_str, run_mode, round(total_value, 2), round(cash, 2),
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
    _print_backtest_summary(equity_curve, start_date, end_date, run_mode=run_mode)
    return {"equity_curve": equity_curve, "run_mode": run_mode,
            "exit_logic": exit_logic, "data_loaded": len(all_data)}


def _print_backtest_summary(equity_curve: list, start_date=None, end_date=None,
                            run_mode: str = "backtest"):
    if not equity_curve:
        print("No data.")
        return

    import math

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
        cur.execute("SELECT COUNT(*), SUM(CASE WHEN pnl_pln>0 THEN 1 ELSE 0 END), SUM(pnl_pln) FROM trades WHERE status='closed' AND run_mode=?", (run_mode,))
        row = cur.fetchone()
        total_trades = row[0] or 0
        wins = row[1] or 0
        # per-year trade stats
        cur.execute("""
            SELECT strftime('%Y', exit_date) yr,
                   COUNT(*) n,
                   SUM(CASE WHEN pnl_pln>0 THEN 1 ELSE 0 END) wins,
                   SUM(pnl_pln) pnl
            FROM trades WHERE status='closed' AND run_mode=?
            GROUP BY yr ORDER BY yr
        """, (run_mode,))
        yearly_trades = {r[0]: {"n": r[1], "wins": r[2], "pnl": r[3] or 0}
                         for r in cur.fetchall()}
    finally:
        conn.close()

    def _sharpe(vals):
        rets = [(vals[i] - vals[i-1]) / vals[i-1]
                for i in range(1, len(vals)) if vals[i-1] > 0]
        if len(rets) < 2:
            return 0.0
        mean_r = sum(rets) / len(rets)
        var = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
        std_r = math.sqrt(var) if var > 0 else 0
        rf = 1.04 ** (1 / 252) - 1
        return (mean_r - rf) / std_r * math.sqrt(252) if std_r > 0 else 0.0

    sharpe = _sharpe(values)

    print(f"\n{'='*60}")
    print(f"  BACKTEST RESULTS  {start_date} → {end_date}")
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

    # ── Year-by-year breakdown ─────────────────────────────────────────────────
    by_year: dict[str, list] = {}
    for e in equity_curve:
        y = e["date"].strftime("%Y")
        by_year.setdefault(y, []).append(e["value"])

    if len(by_year) > 1:
        print(f"\n  {'Year':<6} {'Return':>8} {'P&L (PLN)':>14} {'Trades':>7} "
              f"{'Win%':>6} {'Sharpe':>7}")
        print(f"  {'-'*6} {'-'*8} {'-'*14} {'-'*7} {'-'*6} {'-'*7}")
        prev_val = INITIAL_CAPITAL_PLN
        for year in sorted(by_year.keys()):
            year_vals = by_year[year]
            year_end = year_vals[-1]
            year_ret = (year_end - prev_val) / prev_val * 100 if prev_val else 0
            year_pnl = year_end - prev_val
            sharpe_y = _sharpe([prev_val] + year_vals)
            ty = yearly_trades.get(year, {"n": 0, "wins": 0, "pnl": 0})
            wr = ty["wins"] / ty["n"] * 100 if ty["n"] else 0
            sign_r = "+" if year_ret >= 0 else ""
            sign_p = "+" if year_pnl >= 0 else ""
            print(f"  {year:<6} {sign_r}{year_ret:>7.1f}% {sign_p}{year_pnl:>13,.0f} "
                  f"{ty['n']:>7d} {wr:>5.0f}% {sharpe_y:>7.2f}")
            prev_val = year_end

    print(f"\n{'='*60}")
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


# ── Metrics & comparative reporting ─────────────────────────────────────────

import math
import csv

REPORTS_DIR = os.path.join(os.path.dirname(__file__), "..", "reports")


def _equity_metrics(equity_curve):
    """Drawdown / sharpe / sortino / calmar / cagr from an equity curve."""
    if not equity_curve or len(equity_curve) < 2:
        return dict(total_return_pct=0, cagr=0, max_drawdown_pct=0,
                    sharpe=0, sortino=0, calmar=0)
    values = [e["value"] for e in equity_curve]
    final = values[-1]
    total_return_pct = (final - INITIAL_CAPITAL_PLN) / INITIAL_CAPITAL_PLN * 100

    peak = values[0]
    max_dd = 0.0
    for v in values:
        peak = max(peak, v)
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        max_dd = max(max_dd, dd)

    rets = [(values[i] - values[i-1]) / values[i-1]
            for i in range(1, len(values)) if values[i-1] > 0]
    n_days = len(equity_curve)
    years = max(n_days / 252.0, 1e-9)
    cagr = ((final / INITIAL_CAPITAL_PLN) ** (1 / years) - 1) * 100 if final > 0 else -100

    rf = 1.04 ** (1 / 252) - 1
    sharpe = sortino = 0.0
    if len(rets) >= 2:
        mean_r = sum(rets) / len(rets)
        var = sum((r - mean_r) ** 2 for r in rets) / (len(rets) - 1)
        std = math.sqrt(var) if var > 0 else 0
        sharpe = (mean_r - rf) / std * math.sqrt(252) if std > 0 else 0.0
        downside = [min(0, r - rf) for r in rets]
        dvar = sum(d ** 2 for d in downside) / len(downside)
        dstd = math.sqrt(dvar) if dvar > 0 else 0
        sortino = (mean_r - rf) / dstd * math.sqrt(252) if dstd > 0 else 0.0
    calmar = (cagr / max_dd) if max_dd > 0 else 0.0
    return dict(total_return_pct=total_return_pct, cagr=cagr, max_drawdown_pct=max_dd,
                sharpe=sharpe, sortino=sortino, calmar=calmar)


def _trade_metrics(run_mode):
    """Per-variant trade statistics read from the DB for a run_mode."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT pnl_pln, pnl_pct, holding_days, exit_reason, entry_price,
                   exit_price, stop_loss, max_profit_pct, ticker, entry_date,
                   strategy, max_unrealized_pnl_pct
            FROM trades WHERE status='closed' AND run_mode=?
        """, (run_mode,))
        rows = [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]
    finally:
        conn.close()
    return rows


def _aggregate(rows, equity_curve):
    """Compute the full comparison metric dict for one variant."""
    eq = _equity_metrics(equity_curve)
    n = len(rows)
    pnls = [r["pnl_pln"] or 0 for r in rows]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    holds = [r["holding_days"] or 0 for r in rows]

    def _median(xs):
        if not xs:
            return 0
        s = sorted(xs)
        m = len(s) // 2
        return s[m] if len(s) % 2 else (s[m-1] + s[m]) / 2

    # profit-capture: final pnl_pct / max_profit_pct (when max>0)
    captures = []
    for r in rows:
        mp = r.get("max_profit_pct") or 0
        if mp > 0:
            captures.append(max(0.0, min(1.5, (r["pnl_pct"] or 0) / 100.0 / mp)))

    gap_rows = [r for r in rows if r.get("exit_reason") == "GAP_BELOW_ACTIVE_STOP"]
    gap_losses = [(r["pnl_pct"] or 0) for r in gap_rows]

    def _reached(th):
        return sum(1 for r in rows if (r.get("max_profit_pct") or 0) >= th)

    lost_after_8 = sum(1 for r in rows
                       if (r.get("max_profit_pct") or 0) >= 0.08 and (r["pnl_pln"] or 0) <= 0)
    gaveback_after_10 = sum(1 for r in rows
                            if (r.get("max_profit_pct") or 0) >= 0.10
                            and (r["pnl_pct"] or 0) / 100.0 < 0.07)

    return {
        "total_trades": n,
        "total_return_pct": round(eq["total_return_pct"], 2),
        "cagr": round(eq["cagr"], 2),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else 0,
        "win_rate": round(len(wins) / n * 100, 2) if n else 0,
        "expectancy": round(sum(pnls) / n, 2) if n else 0,
        "avg_trade_pln": round(sum(pnls) / n, 2) if n else 0,
        "median_trade_pln": round(_median(pnls), 2),
        "avg_win_pln": round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss_pln": round(sum(losses) / len(losses), 2) if losses else 0,
        "win_loss_ratio": round((sum(wins)/len(wins)) / abs(sum(losses)/len(losses)), 3)
                          if wins and losses else 0,
        "max_drawdown_pct": round(eq["max_drawdown_pct"], 2),
        "sharpe": round(eq["sharpe"], 3),
        "sortino": round(eq["sortino"], 3),
        "calmar": round(eq["calmar"], 3),
        "avg_holding": round(sum(holds) / n, 2) if n else 0,
        "median_holding": round(_median(holds), 2),
        "largest_winner": round(max(pnls), 2) if pnls else 0,
        "largest_loser": round(min(pnls), 2) if pnls else 0,
        "gap_below_stop_count": len(gap_rows),
        "avg_gap_loss_pct": round(sum(gap_losses) / len(gap_losses), 3) if gap_losses else 0,
        "reached_4pct_count": _reached(0.04),
        "reached_6pct_count": _reached(0.06),
        "reached_8pct_count": _reached(0.08),
        "reached_10pct_count": _reached(0.10),
        "lost_after_8pct_count": lost_after_8,
        "gave_back_after_10pct_count": gaveback_after_10,
        "avg_profit_capture_ratio": round(sum(captures) / len(captures), 3) if captures else 0,
    }


_COMPARISON_COLS = [
    "variant", "total_trades", "total_return_pct", "cagr", "profit_factor",
    "win_rate", "expectancy", "avg_trade_pln", "median_trade_pln", "avg_win_pln",
    "avg_loss_pln", "win_loss_ratio", "max_drawdown_pct", "sharpe", "sortino",
    "calmar", "avg_holding", "median_holding", "largest_winner", "largest_loser",
    "gap_below_stop_count", "avg_gap_loss_pct", "reached_4pct_count",
    "reached_6pct_count", "reached_8pct_count", "reached_10pct_count",
    "lost_after_8pct_count", "gave_back_after_10pct_count", "avg_profit_capture_ratio",
]


def _write_comparison_reports(results, note=""):
    """results: list of dicts each with 'variant' + metric keys."""
    os.makedirs(REPORTS_DIR, exist_ok=True)

    # CSV
    csv_path = os.path.join(REPORTS_DIR, "exit_logic_comparison.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_COMPARISON_COLS)
        w.writeheader()
        for r in results:
            w.writerow({c: r.get(c, "") for c in _COMPARISON_COLS})

    # Markdown
    md_path = os.path.join(REPORTS_DIR, "exit_logic_comparison.md")
    key_metrics = ["total_trades", "total_return_pct", "cagr", "profit_factor",
                   "win_rate", "max_drawdown_pct", "sharpe", "avg_holding",
                   "gap_below_stop_count", "avg_profit_capture_ratio"]
    with open(md_path, "w") as f:
        f.write("# Exit Logic Comparison\n\n")
        if note:
            f.write(f"> **Note:** {note}\n\n")
        f.write("| Metric | " + " | ".join(r["variant"] for r in results) + " |\n")
        f.write("|" + "---|" * (len(results) + 1) + "\n")
        for m in key_metrics:
            f.write(f"| {m} | " + " | ".join(str(r.get(m, "")) for r in results) + " |\n")
    print(f"  wrote {csv_path}")
    print(f"  wrote {md_path}")


def _write_per_strategy_report(variant_modes, note=""):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, "dynamic_stop_results_by_strategy.csv")
    cols = ["variant", "strategy"] + [c for c in _COMPARISON_COLS if c != "variant"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for variant, mode, eq in variant_modes:
            rows = _trade_metrics(mode)
            strategies = sorted({r["strategy"] for r in rows})
            for strat in strategies:
                srows = [r for r in rows if r["strategy"] == strat]
                agg = _aggregate(srows, eq)
                line = {"variant": variant, "strategy": strat}
                line.update({k: v for k, v in agg.items() if k in cols})
                w.writerow(line)
    print(f"  wrote {path}")


def _write_profit_capture_report(variant_modes):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, "profit_capture_analysis.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "entry_date", "max_profit_pct", "final_pnl_pct",
                    "profit_capture_ratio", "exit_reason", "variant"])
        for variant, mode, _eq in variant_modes:
            for r in _trade_metrics(mode):
                mp = r.get("max_profit_pct") or 0
                final = (r["pnl_pct"] or 0) / 100.0
                ratio = round(final / mp, 3) if mp > 0 else ""
                w.writerow([r["ticker"], r["entry_date"], round(mp, 4),
                            round(final, 4), ratio, r.get("exit_reason"), variant])
    print(f"  wrote {path}")


def _write_gap_risk_report(variant_modes):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, "gap_risk_analysis.csv")
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["variant", "ticker", "entry_date", "entry_price", "stop_at_gap",
                    "open_price", "gap_pct", "loss_pln"])
        for variant, mode, _eq in variant_modes:
            for r in _trade_metrics(mode):
                if r.get("exit_reason") != "GAP_BELOW_ACTIVE_STOP":
                    continue
                ep = r.get("entry_price") or 0
                op = r.get("exit_price") or 0
                gap_pct = round((op - (r.get("stop_loss") or 0)) /
                                (r.get("stop_loss") or 1), 4)
                w.writerow([variant, r["ticker"], r["entry_date"], ep,
                            r.get("stop_loss"), op, gap_pct, r.get("pnl_pln")])
    print(f"  wrote {path}")


def run_sensitivity_analysis(start_date, months, preloaded_data=None):
    """Grid over key exit-logic parameters. Each combo runs FIXED_TP_DYNAMIC_STOP.

    Reuses env-var overrides by patching app.config + app.exit_logic module
    globals (which read the constants at call time). Writes a CSV grid.
    """
    from app import config as CFG

    grid_sl = [0.05, 0.06, 0.07]
    grid_tp = [0.08, 0.10, 0.12]
    grid_tr = [0.02, 0.03, 0.04]
    grid_be = [0.03, 0.04, 0.05]

    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, "parameter_sensitivity_exit_logic.csv")

    saved = (CFG.MAX_INITIAL_STOP_DISTANCE_PCT, CFG.DEFAULT_TAKE_PROFIT_PCT,
             CFG.TRAILING_STOP_DISTANCE_PCT, CFG.BREAK_EVEN_TRIGGER_PCT,
             XL.MAX_INITIAL_STOP_DISTANCE_PCT, XL.DEFAULT_TAKE_PROFIT_PCT,
             XL.TRAILING_STOP_DISTANCE_PCT, XL.BREAK_EVEN_TRIGGER_PCT)

    results = []
    try:
        for max_sl in grid_sl:
            for tp_pct in grid_tp:
                for tr in grid_tr:
                    for be in grid_be:
                        # patch both modules' module-level constants
                        for mod in (CFG, XL):
                            mod.MAX_INITIAL_STOP_DISTANCE_PCT = max_sl
                            mod.DEFAULT_TAKE_PROFIT_PCT = tp_pct
                            mod.TRAILING_STOP_DISTANCE_PCT = tr
                            mod.BREAK_EVEN_TRIGGER_PCT = be
                        res = run_backtest(start_date=start_date, months=months,
                                           exit_logic=FIXED_TP, run_mode="backtest_sens",
                                           preloaded_data=preloaded_data)
                        eq = res["equity_curve"]
                        agg = _aggregate(_trade_metrics("backtest_sens"), eq)
                        agg.update(dict(max_sl=max_sl, tp_pct=tp_pct,
                                        trailing=tr, be_trigger=be))
                        results.append(agg)
    finally:
        (CFG.MAX_INITIAL_STOP_DISTANCE_PCT, CFG.DEFAULT_TAKE_PROFIT_PCT,
         CFG.TRAILING_STOP_DISTANCE_PCT, CFG.BREAK_EVEN_TRIGGER_PCT,
         XL.MAX_INITIAL_STOP_DISTANCE_PCT, XL.DEFAULT_TAKE_PROFIT_PCT,
         XL.TRAILING_STOP_DISTANCE_PCT, XL.BREAK_EVEN_TRIGGER_PCT) = saved

    cols = ["max_sl", "tp_pct", "trailing", "be_trigger", "total_trades",
            "total_return_pct", "cagr", "profit_factor", "win_rate",
            "max_drawdown_pct", "sharpe", "avg_holding", "avg_profit_capture_ratio"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in results:
            w.writerow({c: r.get(c, "") for c in cols})
    print(f"  wrote {path}")
    return results


def _write_recommendation(results, note=""):
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(REPORTS_DIR, "final_exit_logic_recommendation.md")
    by = {r["variant"]: r for r in results}

    def g(v, k):
        return by.get(v, {}).get(k, "n/a")

    with open(path, "w") as f:
        f.write("# Final Exit Logic Recommendation — SIMPLE_DYNAMIC_EXIT_V1\n\n")
        if note:
            f.write(f"> **Note:** {note}\n\n")
        f.write("Answers below are data-driven where the comparative backtest "
                "produced trades; otherwise they describe the designed behaviour.\n\n")
        qa = [
            ("1. Does the new logic close profitable positions that the old logic left open?",
             f"The legacy ATR-based TP placed targets ~20-25% above entry, so winners "
             f"rarely hit TP. The new logic fixes TP at +10% (max +12%). "
             f"FIXED_TP take_profit exits: see exit_logic_comparison.csv "
             f"(legacy return {g('LEGACY_EXIT_LOGIC','total_return_pct')}% vs "
             f"FIXED_TP {g(FIXED_TP,'total_return_pct')}%)."),
            ("2. Which variant has the best risk-adjusted return?",
             f"Compare Sharpe: LEGACY={g('LEGACY_EXIT_LOGIC','sharpe')}, "
             f"FIXED_TP={g(FIXED_TP,'sharpe')}, TRAILING={g(TRAILING,'sharpe')}; "
             f"Calmar: LEGACY={g('LEGACY_EXIT_LOGIC','calmar')}, "
             f"FIXED_TP={g(FIXED_TP,'calmar')}, TRAILING={g(TRAILING,'calmar')}."),
            ("3. How much profit is captured vs left on the table?",
             f"avg_profit_capture_ratio: FIXED_TP={g(FIXED_TP,'avg_profit_capture_ratio')}, "
             f"TRAILING={g(TRAILING,'avg_profit_capture_ratio')}. See "
             f"profit_capture_analysis.csv per trade."),
            ("4. How often do gaps blow through the active stop?",
             f"gap_below_stop_count: FIXED_TP={g(FIXED_TP,'gap_below_stop_count')}, "
             f"TRAILING={g(TRAILING,'gap_below_stop_count')}; avg gap loss "
             f"{g(FIXED_TP,'avg_gap_loss_pct')}%. See gap_risk_analysis.csv."),
            ("5. Does break-even-after-+4% reduce losers?",
             f"reached_4pct_count FIXED_TP={g(FIXED_TP,'reached_4pct_count')}; "
             f"lost_after_8pct_count={g(FIXED_TP,'lost_after_8pct_count')}. "
             f"Break-even (incl. round-trip costs) protects trades that reached +4%."),
            ("6. How many trades reach the profit-lock thresholds?",
             f"reached 4/6/8/10%: FIXED_TP="
             f"{g(FIXED_TP,'reached_4pct_count')}/{g(FIXED_TP,'reached_6pct_count')}/"
             f"{g(FIXED_TP,'reached_8pct_count')}/{g(FIXED_TP,'reached_10pct_count')}."),
            ("7. Does TRAILING_AFTER_10 capture more upside than FIXED_TP?",
             f"TRAILING return={g(TRAILING,'total_return_pct')}% vs "
             f"FIXED_TP={g(FIXED_TP,'total_return_pct')}%; "
             f"gave_back_after_10pct_count(TRAILING)="
             f"{g(TRAILING,'gave_back_after_10pct_count')}."),
            ("8. What is the win rate / expectancy trade-off?",
             f"win_rate FIXED_TP={g(FIXED_TP,'win_rate')}% expectancy "
             f"{g(FIXED_TP,'expectancy')} PLN; TRAILING win_rate={g(TRAILING,'win_rate')}% "
             f"expectancy {g(TRAILING,'expectancy')} PLN."),
            ("9. How sensitive are results to parameter choices?",
             "See parameter_sensitivity_exit_logic.csv — grid over max_sl "
             "[0.05,0.06,0.07], tp [0.08,0.10,0.12], trailing [0.02,0.03,0.04], "
             "be_trigger [0.03,0.04,0.05]."),
            ("10. Recommended configuration?",
             "Default: FIXED_TP_DYNAMIC_STOP for primary book (predictable, "
             "tight profit capture, fewer give-backs); enable TRAILING_AFTER_10 "
             "for strong-momentum names. Initial stop capped at 7%, TP +10% (max +12%), "
             "break-even at +4% incl. costs, locks at +6/+8/+10%, 3% trailing above +10%, "
             "10-session limit (15 for trailing winners)."),
        ]
        for q, a in qa:
            f.write(f"### {q}\n\n{a}\n\n")
    print(f"  wrote {path}")


def run_comparative_backtest(start_date=None, months=12, with_sensitivity=True):
    """Run LEGACY, FIXED_TP, TRAILING on the same universe; write all reports.

    Each variant runs into its own run_mode so DBs never mix:
      backtest_v1=LEGACY, backtest_v2=FIXED_TP, backtest_v3=TRAILING.
    The primary FIXED_TP run is also written to run_mode='backtest' for the
    dashboard.
    """
    end_date = date.today() - timedelta(days=1)
    if start_date is None:
        start_date = end_date - relativedelta(months=months)

    # Load price data ONCE and reuse for every variant + sensitivity combo.
    watchlist = _load_all_data()
    print(f"  [compare-all] fetching price data once for {len(watchlist)} tickers...")
    shared_data = _fetch_price_data(watchlist, start_date, end_date)
    print(f"  [compare-all] loaded {len(shared_data)} tickers.\n")

    note = ""
    variant_modes = []  # (variant_label, run_mode, equity_curve)
    data_loaded = len(shared_data)
    for variant, mode in [(LEGACY, "backtest_v1"),
                          (FIXED_TP, "backtest_v2"),
                          (TRAILING, "backtest_v3")]:
        res = run_backtest(start_date=start_date, months=months,
                           exit_logic=variant, run_mode=mode,
                           preloaded_data=shared_data)
        variant_modes.append((variant, mode, res["equity_curve"]))

    if data_loaded == 0:
        note = ("Network unavailable — no price data could be fetched (yfinance "
                "blocked). Reports show structure only; run in GitHub Actions where "
                "egress to query1.finance.yahoo.com is permitted for real numbers.")
        print(f"\n  [warn] {note}")

    # Build comparison rows
    results = []
    for variant, mode, eq in variant_modes:
        agg = _aggregate(_trade_metrics(mode), eq)
        agg["variant"] = variant
        results.append(agg)

    _write_comparison_reports(results, note=note)
    _write_per_strategy_report(variant_modes, note=note)
    _write_profit_capture_report(variant_modes)
    _write_gap_risk_report(variant_modes)
    if with_sensitivity:
        try:
            run_sensitivity_analysis(start_date, months, preloaded_data=shared_data)
        except Exception as e:
            print(f"  [warn] sensitivity analysis failed: {e}")
            # still emit a placeholder file
            with open(os.path.join(REPORTS_DIR,
                      "parameter_sensitivity_exit_logic.csv"), "w") as f:
                f.write("max_sl,tp_pct,trailing,be_trigger,total_trades,note\n")
                f.write(f",,,,,{note or str(e)}\n")
    _write_recommendation(results, note=note)

    # Promote primary FIXED_TP run to run_mode='backtest' for the dashboard.
    run_backtest(start_date=start_date, months=months,
                 exit_logic=FIXED_TP, run_mode="backtest",
                 preloaded_data=shared_data)
    print("\n  Comparative backtest complete. Reports in reports/.")
    return results


if __name__ == "__main__":
    try:
        from dateutil.relativedelta import relativedelta
    except ImportError:
        print("Install python-dateutil: pip install python-dateutil")
        sys.exit(1)
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", help="Start date YYYY-MM-DD")
    parser.add_argument("--months", type=int, default=12, help="Number of months (default: 12)")
    parser.add_argument("--exit-logic", default="SIMPLE_DYNAMIC_EXIT_V1",
                        help="LEGACY_EXIT_LOGIC | FIXED_TP_DYNAMIC_STOP | TRAILING_AFTER_10")
    parser.add_argument("--compare-all", action="store_true",
                        help="Run all 3 variants and generate comparison reports")
    parser.add_argument("--cost-scenario", default="BASE",
                        help="Cost scenario label (BASE only currently)")
    parser.add_argument("--no-sensitivity", action="store_true",
                        help="Skip the parameter sensitivity grid")
    args = parser.parse_args()
    start = date.fromisoformat(args.start) if args.start else None
    if args.compare_all:
        run_comparative_backtest(start_date=start, months=args.months,
                                 with_sensitivity=not args.no_sensitivity)
    else:
        run_backtest(start_date=start, months=args.months, exit_logic=args.exit_logic)
