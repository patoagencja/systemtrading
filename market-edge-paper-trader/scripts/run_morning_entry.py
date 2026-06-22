"""
Morning entry — runs ~5 min after US market open (14:35 UTC / 9:35 ET).

Enters all pending paper-trading signals at the actual market open price.
Uses 5-minute intraday bars to get the first bar's open (= official open).
Falls back to the daily bar's open if intraday data isn't available yet.
"""
import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import date
import pandas as pd

from app.database import get_connection, db_cursor
from app.data_provider import fetch_ohlcv
from app.indicators import compute_indicators
from app.strategies import Signal
from app.risk_manager import RiskManager
from app.paper_broker import PaperBroker
from app.portfolio import Portfolio
from app.config import PLN_USD_RATE, MIN_AVG_VOLUME


def _get_pending_signals(run_mode: str) -> list[dict]:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT * FROM signals WHERE acted=0 AND run_mode=? ORDER BY score DESC",
            (run_mode,)
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]
    finally:
        conn.close()


def _mark_acted(ticker: str, strategy: str, signal_date: str, run_mode: str):
    with db_cursor() as cur:
        cur.execute(
            "UPDATE signals SET acted=1 "
            "WHERE ticker=? AND strategy=? AND signal_date=? AND run_mode=?",
            (ticker, strategy, signal_date, run_mode),
        )


def _get_open_price(ticker: str, today: date) -> float | None:
    """
    Return today's opening price.
    Tries 5-min bars first (accurate open), falls back to daily bar.
    """
    try:
        # 5-min bars: first bar of the session = market open
        df5 = fetch_ohlcv(ticker, period="1d", interval="5m")
        if not df5.empty:
            today_5m = df5[df5.index.date == today]
            if not today_5m.empty:
                return float(today_5m.iloc[0]["open"])
    except Exception:
        pass

    try:
        # Daily bar fallback
        df_day = fetch_ohlcv(ticker, period="3d")
        if not df_day.empty:
            rows = df_day[df_day.index.date == today]
            if not rows.empty:
                return float(rows.iloc[0]["open"])
    except Exception:
        pass

    return None


def run_morning_entry(run_mode: str = "live", verbose: bool = True) -> int:
    today = date.today()

    pending = _get_pending_signals(run_mode)
    if not pending:
        if verbose:
            print("  No pending signals — nothing to enter.")
        return 0

    portfolio = Portfolio(run_mode=run_mode)
    broker = PaperBroker(PLN_USD_RATE, run_mode=run_mode)
    risk = RiskManager(portfolio.total_value_pln, PLN_USD_RATE)
    open_tickers = portfolio.get_open_tickers()

    if verbose:
        print(f"\n{'='*56}")
        print(f"  MORNING ENTRY  {today}")
        print(f"  Pending signals: {len(pending)}  |  Portfolio: {portfolio.total_value_pln:,.0f} PLN")
        print(f"{'='*56}")

    opened = 0

    for ps in pending:
        ticker = ps["ticker"]
        strategy = ps["strategy"]
        signal_date = ps["signal_date"]

        if ticker in open_tickers:
            _mark_acted(ticker, strategy, signal_date, run_mode)
            continue

        actual_entry = _get_open_price(ticker, today)
        time.sleep(0.2)  # rate-limit yfinance

        if actual_entry is None or actual_entry <= 0:
            if verbose:
                print(f"  ? {ticker:8s}  no open price yet — skipped")
            continue

        # Historical avg volume for slippage calculation
        try:
            df_hist = fetch_ohlcv(ticker, period="1mo")
            avg_vol = float(df_hist["volume"].tail(20).mean()) if not df_hist.empty else 0.0
        except Exception:
            avg_vol = 0.0

        sig = Signal(
            ticker=ticker,
            strategy=strategy,
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
            _mark_acted(ticker, strategy, signal_date, run_mode)
            continue

        can_open, reason = risk.can_open_position(
            sizing["position_value_pln"],
            portfolio.invested_pln,
            portfolio.open_positions,
        )
        if not can_open:
            if verbose:
                print(f"  ~ SKIP  {ticker:8s}  {reason}")
            _mark_acted(ticker, strategy, signal_date, run_mode)
            continue

        broker.open_trade(
            sig, sizing["shares"], sizing["position_value_pln"],
            sizing["risk_pln"], today, avg_vol,
        )
        _mark_acted(ticker, strategy, signal_date, run_mode)

        open_tickers.add(ticker)
        portfolio._refresh()
        risk = RiskManager(portfolio.total_value_pln, PLN_USD_RATE)
        opened += 1

        if verbose:
            print(f"  + OPEN  {ticker:8s} [{strategy}]  "
                  f"entry={actual_entry:.2f}  "
                  f"SL={ps['stop_loss']:.2f}  TP={ps['take_profit']:.2f}  "
                  f"pos={sizing['position_value_pln']:,.0f} PLN")

    if verbose:
        print(f"\n  Entered {opened} new position(s).")

    return opened


if __name__ == "__main__":
    run_morning_entry(run_mode="live", verbose=True)
