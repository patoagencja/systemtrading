import pandas as pd
from datetime import date, timedelta
from dataclasses import replace as dc_replace
from app.database import get_connection, db_cursor
from app.data_provider import fetch_ohlcv
from app.indicators import compute_indicators, get_latest_row
from app.strategies import run_all_strategies, Signal
from app.risk_manager import RiskManager
from app.paper_broker import PaperBroker
from app.portfolio import Portfolio
from app.config import MIN_SCORE_TO_OPEN, MIN_HISTORY_BARS, PLN_USD_RATE, MIN_AVG_VOLUME


def load_watchlist() -> list[dict]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT ticker, sector, is_etf, sector_etf FROM watchlist WHERE active=1")
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _get_pending_signals(run_mode: str) -> list[dict]:
    """Return unacted signals from all previous dates."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM signals WHERE acted=0 AND run_mode=? ORDER BY signal_date DESC",
            (run_mode,)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _mark_signal_acted(ticker: str, strategy: str, signal_date: str, run_mode: str):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE signals SET acted=1 WHERE ticker=? AND strategy=? AND signal_date=? AND run_mode=?",
            (ticker, strategy, signal_date, run_mode)
        )


def run_scanner(today: date = None, verbose: bool = True, run_mode: str = "live",
                skip_pending_entry: bool = False) -> dict:
    """EOD scan: close positions, optionally enter pending signals, generate new signals.

    skip_pending_entry=True when morning-entry workflow handles entries separately.
    """
    if today is None:
        today = date.today()

    portfolio = Portfolio(run_mode=run_mode)
    broker = PaperBroker(PLN_USD_RATE, run_mode=run_mode)
    risk = RiskManager(portfolio.total_value_pln, PLN_USD_RATE)

    watchlist = load_watchlist()
    open_tickers = portfolio.get_open_tickers()

    if verbose:
        print(f"\n{'='*60}")
        print(f"  SCAN: {today}  |  Portfolio: {portfolio.total_value_pln:,.0f} PLN")
        print(f"  Open positions: {portfolio.open_positions}  |  Cash: {portfolio.cash_pln:,.0f} PLN")
        print(f"{'='*60}")

    spy_df = fetch_ohlcv("SPY")
    if not spy_df.empty:
        spy_df = compute_indicators(spy_df)

    # Phase 1: Update & close open positions
    closed_count = _update_open_positions(portfolio, broker, today, verbose)
    portfolio._refresh()

    # Phase 2: Enter pending signals (only if not handled by morning-entry workflow)
    if not skip_pending_entry:
        opened_pending = _enter_pending_signals(portfolio, broker, risk, today, run_mode, verbose)
        portfolio._refresh()
        risk = RiskManager(portfolio.total_value_pln, PLN_USD_RATE)
        open_tickers = portfolio.get_open_tickers()
    else:
        opened_pending = 0
        open_tickers = portfolio.get_open_tickers()

    # Phase 3: Scan for new signals → save as pending (entered tomorrow)
    all_signals: list[tuple[Signal, float]] = []
    scanned = 0
    tickers_with_data = 0

    for item in watchlist:
        ticker = item["ticker"]
        scanned += 1

        df = fetch_ohlcv(ticker)
        if df.empty or len(df) < MIN_HISTORY_BARS:
            continue

        avg_vol = float(df["volume"].tail(20).mean())
        if avg_vol < MIN_AVG_VOLUME:
            continue

        tickers_with_data += 1
        df = compute_indicators(df)
        if get_latest_row(df) is None:
            continue

        sector_etf_df = None
        if item.get("sector_etf"):
            etf_df = fetch_ohlcv(item["sector_etf"])
            if not etf_df.empty:
                sector_etf_df = compute_indicators(etf_df)

        signals = run_all_strategies(ticker, df, spy_df, sector_etf_df)
        for sig in signals:
            if sig.score >= MIN_SCORE_TO_OPEN:
                all_signals.append((sig, avg_vol))

    all_signals.sort(key=lambda x: x[0].score, reverse=True)

    # Save new signals as pending (acted=False) — will be entered tomorrow at open
    portfolio._refresh()
    saved_count = 0
    for sig, avg_vol in all_signals:
        broker.save_signal(sig, today, acted=False)
        saved_count += 1
        if verbose:
            acted_marker = "✓ queued" if sig.ticker not in open_tickers else "  skip (open)"
            print(f"  ~ SIG   {sig.ticker:8s} {sig.strategy:25s} score={sig.score:.0f}  {acted_marker}")

    # Phase 4: Snapshot
    portfolio._refresh()
    prev_value = _get_prev_snapshot_value(run_mode)
    daily_pnl = portfolio.total_value_pln - prev_value
    portfolio.save_snapshot(today, daily_pnl)
    broker.update_strategy_stats()

    stats = {
        "date": today,
        "scanned": scanned,
        "tickers_with_data": tickers_with_data,
        "signals_found": len(all_signals),
        "new_positions": opened_pending,
        "closed_positions": closed_count,
        "portfolio_value": portfolio.total_value_pln,
        "cash": portfolio.cash_pln,
        "daily_pnl": daily_pnl,
        "total_pnl": portfolio.total_value_pln - __import__("app.config", fromlist=["INITIAL_CAPITAL_PLN"]).INITIAL_CAPITAL_PLN,
        "total_return_pct": portfolio.total_return_pct,
        "open_positions": portfolio.open_positions,
        "top_signals": [s for s, _ in all_signals[:5]],
    }
    return stats


def _enter_pending_signals(
    portfolio: Portfolio,
    broker: PaperBroker,
    risk: RiskManager,
    today: date,
    run_mode: str,
    verbose: bool,
) -> int:
    pending = _get_pending_signals(run_mode)
    if not pending:
        return 0

    open_tickers = portfolio.get_open_tickers()
    opened = 0

    for ps in pending:
        ticker = ps["ticker"]
        if ticker in open_tickers:
            _mark_signal_acted(ticker, ps["strategy"], ps["signal_date"], run_mode)
            continue

        # Fetch today's data to get open price
        df = fetch_ohlcv(ticker, period="3d")
        if df.empty:
            continue
        df = compute_indicators(df)
        today_rows = df[df.index.date == today]  # type: ignore
        if today_rows.empty:
            continue

        actual_entry = float(today_rows.iloc[0]["open"])
        avg_vol = float(df["volume"].tail(20).mean())

        if actual_entry <= 0:
            _mark_signal_acted(ticker, ps["strategy"], ps["signal_date"], run_mode)
            continue

        from app.strategies import Signal
        sig = Signal(
            ticker=ticker,
            strategy=ps["strategy"],
            score=ps["score"],
            entry_price=actual_entry,
            stop_loss=ps["stop_loss"],
            take_profit=ps["take_profit"],
            atr=ps["atr"],
            rsi=ps["rsi"],
            volume_ratio=ps["volume_ratio"],
            reason=ps["reason"],
            max_holding_days=10,
        )

        sizing = risk.calc_position_size(actual_entry, ps["stop_loss"])
        if not sizing["valid"]:
            _mark_signal_acted(ticker, ps["strategy"], ps["signal_date"], run_mode)
            continue

        can_open, _ = risk.can_open_position(
            sizing["position_value_pln"],
            portfolio.invested_pln,
            portfolio.open_positions,
        )
        if not can_open:
            _mark_signal_acted(ticker, ps["strategy"], ps["signal_date"], run_mode)
            continue

        broker.open_trade(sig, sizing["shares"], sizing["position_value_pln"],
                          sizing["risk_pln"], today, avg_vol)
        _mark_signal_acted(ticker, ps["strategy"], ps["signal_date"], run_mode)

        open_tickers.add(ticker)
        portfolio._refresh()
        risk = RiskManager(portfolio.total_value_pln, PLN_USD_RATE)
        opened += 1

        if verbose:
            print(f"  + OPEN  {ticker:8s} {ps['strategy']:25s} entry={actual_entry:.2f} "
                  f"(next-day open)  pos={sizing['position_value_pln']:,.0f} PLN")

    return opened


def _update_open_positions(
    portfolio: Portfolio,
    broker: PaperBroker,
    today: date,
    verbose: bool,
) -> int:
    trades = portfolio.get_open_trades()
    closed_count = 0

    for trade in trades:
        ticker = trade["ticker"]
        trade_id = trade["id"]

        df = fetch_ohlcv(ticker, period="5d")
        if df.empty:
            continue
        df = compute_indicators(df)
        if df.empty:
            continue

        latest = df.iloc[-1]
        current_close = float(latest["close"])
        current_open  = float(latest["open"])
        current_high  = float(latest["high"])
        current_low   = float(latest["low"])
        current_sma50 = latest.get("sma50")
        avg_vol = float(df["volume"].tail(20).mean())

        entry_date = date.fromisoformat(trade["entry_date"])
        holding_days = (today - entry_date).days
        stop_loss = trade["stop_loss"]
        take_profit = trade["take_profit"]
        max_hold = trade.get("max_holding_days", 10)
        entry_price = trade["entry_price"]

        broker.update_open_trade_pnl(trade_id, current_close, holding_days)

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
        elif (not pd.isna(current_sma50) and
              current_close < current_sma50 and
              current_close < entry_price):
            exit_price = current_close
            exit_reason = "technical_exit_below_sma50"

        if exit_price is not None:
            broker.close_trade(trade_id, exit_price, today, exit_reason, avg_vol)
            closed_count += 1
            if verbose:
                pnl_usd = (exit_price - entry_price) * trade["shares"]
                pnl_pln = pnl_usd * PLN_USD_RATE
                arrow = "+" if pnl_pln >= 0 else ""
                print(f"  - CLOSE {ticker:8s} [{exit_reason:30s}] "
                      f"{arrow}{pnl_pln:,.0f} PLN")

    return closed_count


def _get_prev_snapshot_value(run_mode: str = "live") -> float:
    from app.config import INITIAL_CAPITAL_PLN
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT total_value_pln FROM portfolio_snapshots WHERE run_mode=? ORDER BY snapshot_date DESC LIMIT 1",
            (run_mode,)
        )
        row = cur.fetchone()
        return row[0] if row else INITIAL_CAPITAL_PLN
    finally:
        conn.close()
