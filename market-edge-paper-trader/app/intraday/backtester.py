"""Historical intraday backtest engine — bar-by-bar, no look-ahead bias."""
import datetime
import logging
from typing import Optional

import numpy as np
import pandas as pd

from app.intraday.config import (
    INTRADAY_INITIAL_CAPITAL_PLN,
    INTRADAY_PLN_USD_RATE,
    INTRADAY_MIN_SCORE,
    COST_SCENARIOS,
    INTRADAY_AMBIGUOUS_BAR_POLICY,
    MIN_REWARD_RISK,
)
from app.intraday.data_provider import IntradayDataProvider, _interval_to_minutes
from app.intraday.data_quality import DataQualityChecker
from app.intraday.indicators import compute_intraday_indicators, compute_daily_indicators_for_filter
from app.intraday.risk_manager import IntradayRiskManager
from app.intraday.scoring import score_signal
from app.intraday.session_manager import SessionManager, NY_TZ
from app.intraday.strategies import run_all_intraday_strategies, IntradaySignal
from app.intraday.universe import IntradayUniverse, SECTOR_MAP

log = logging.getLogger(__name__)


# ── In-memory trade representation ───────────────────────────────────────────

class _Trade:
    __slots__ = [
        "ticker","strategy","sector","session_date","entry_ts","entry_price",
        "stop_price","target_price","shares","pos_val_pln","planned_risk_pln",
        "market_regime","score","exit_ts","exit_price","exit_reason",
        "gross_pnl_pln","net_pnl_pln","pnl_pct","r_multiple",
        "holding_minutes","commission_pln","slippage_cost_pln","spread_cost_pln",
    ]

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)
        # default exit fields
        for f in ["exit_ts","exit_price","exit_reason","gross_pnl_pln",
                  "net_pnl_pln","pnl_pct","r_multiple","holding_minutes",
                  "commission_pln","slippage_cost_pln","spread_cost_pln"]:
            if not hasattr(self, f) or getattr(self, f, None) is None:
                setattr(self, f, None)

    def to_dict(self) -> dict:
        return {s: getattr(self, s, None) for s in self.__slots__}


# ── Cost model ────────────────────────────────────────────────────────────────

_BPS = 10_000

def _apply_costs(price: float, shares: int, scenario: dict) -> tuple[float, float, float]:
    """Returns (comm_usd, spread_usd, slip_usd) for one side."""
    comm  = price * shares * scenario["commission_bps"] / _BPS
    spread = price * shares * 0.5 / _BPS
    slip  = price * shares * scenario["slippage_bps"] / _BPS
    return comm, spread, slip


def _fill_price(price: float, side: str, scenario: dict) -> float:
    slip = price * scenario["slippage_bps"] / _BPS
    return price + slip if side == "buy" else price - slip


# ── Main backtest function ────────────────────────────────────────────────────

def run_intraday_backtest(
    start_date: datetime.date,
    end_date: datetime.date,
    interval: str = "30m",
    cost_scenario: str = "BASE",
    min_score: float = None,
    verbose: bool = True,
) -> dict:
    """
    Bar-by-bar intraday backtest. No look-ahead bias.
    Returns comprehensive results dict.
    """
    if min_score is None:
        min_score = INTRADAY_MIN_SCORE

    scenario = COST_SCENARIOS.get(cost_scenario, COST_SCENARIOS["BASE"])
    interval_minutes = _interval_to_minutes(interval)
    provider = IntradayDataProvider()
    checker  = DataQualityChecker()
    universe = IntradayUniverse()
    tickers_info = universe.load()

    if verbose:
        log.info(f"Backtest {start_date} → {end_date} | {len(tickers_info)} tickers | {interval} | {cost_scenario}")

    # ── 1. Fetch daily data for all tickers ───────────────────────────────────
    daily_start = start_date - datetime.timedelta(days=400)
    if verbose:
        log.info("Fetching daily bars...")
    daily_cache: dict[str, pd.DataFrame] = {}
    for ti in tickers_info:
        t = ti["ticker"]
        try:
            df = provider.get_daily_bars(t, daily_start, end_date)
            if not df.empty:
                df, _ = checker.validate_dataframe(df)
                df = compute_daily_indicators_for_filter(df)
                daily_cache[t] = df
        except Exception as e:
            log.debug(f"Daily fetch {t}: {e}")

    # ── 2. Fetch intraday bars ────────────────────────────────────────────────
    if verbose:
        log.info("Fetching intraday bars...")
    intraday_cache: dict[str, pd.DataFrame] = {}
    for ti in tickers_info:
        t = ti["ticker"]
        try:
            df = provider.get_intraday_bars(t, start_date, end_date + datetime.timedelta(days=1), interval)
            if not df.empty:
                df, _ = checker.validate_dataframe(df)
                df = checker.check_for_duplicates(df)
                intraday_cache[t] = df
        except Exception as e:
            log.debug(f"Intraday fetch {t}: {e}")

    if verbose:
        log.info(f"Data loaded: {len(daily_cache)} daily, {len(intraday_cache)} intraday tickers")

    # ── 3. Build trading calendar ─────────────────────────────────────────────
    trading_days = []
    d = start_date
    while d <= end_date:
        if SessionManager.is_trading_day(d):
            trading_days.append(d)
        d += datetime.timedelta(days=1)

    # ── 4. Simulation state ───────────────────────────────────────────────────
    equity = INTRADAY_INITIAL_CAPITAL_PLN
    cash   = equity
    open_trades: list[_Trade] = []
    closed_trades: list[_Trade] = []
    pending_signals: list[dict] = []  # signals queued for next bar execution
    snapshots: list[dict] = []

    daily_equity_start = equity  # reset at session start

    # ── 5. Main loop ──────────────────────────────────────────────────────────
    for session_date in trading_days:
        daily_equity_start = equity
        daily_pnl = 0.0
        session_turnover = 0.0

        # Collect bars for this session
        session_bars_per_ticker: dict[str, pd.DataFrame] = {}
        for t, df in intraday_cache.items():
            sdf = df[df.index.date == session_date]
            if not sdf.empty:
                session_bars_per_ticker[t] = sdf

        if not session_bars_per_ticker:
            continue

        # Build chronological bar timeline for SPY (to iterate)
        spy_bars = session_bars_per_ticker.get("SPY", pd.DataFrame())
        if spy_bars.empty:
            # fall back to any ticker
            spy_bars = next(iter(session_bars_per_ticker.values()))

        # Compute intraday indicators for all tickers up-front (per session)
        intraday_session: dict[str, pd.DataFrame] = {}
        for t, sdf in session_bars_per_ticker.items():
            try:
                intraday_session[t] = compute_intraday_indicators(sdf, session_date)
            except Exception:
                intraday_session[t] = sdf

        bar_times = sorted(spy_bars.index)

        for bar_ts in bar_times:
            # Convert to ET
            if bar_ts.tzinfo is None:
                bar_ts_et = bar_ts.replace(tzinfo=NY_TZ)
            else:
                bar_ts_et = bar_ts.astimezone(NY_TZ)

            t_et = bar_ts_et.time()
            is_force_close = t_et >= datetime.time(15, 40)
            can_enter = datetime.time(10, 0) <= t_et <= datetime.time(14, 30)

            # Build current-bar OHLC snapshot for each ticker (data up to this bar)
            cur_bars: dict[str, pd.Series] = {}
            for t, sdf in intraday_session.items():
                bars_to_now = sdf[sdf.index <= bar_ts]
                if not bars_to_now.empty:
                    cur_bars[t] = bars_to_now.iloc[-1]

            # ── a. Check open positions for SL/TP ────────────────────────────
            still_open = []
            for trade in open_trades:
                bar = cur_bars.get(trade.ticker)
                if bar is None:
                    still_open.append(trade)
                    continue

                h = bar.get("high", np.nan)
                l = bar.get("low", np.nan)
                if pd.isna(h) or pd.isna(l):
                    still_open.append(trade)
                    continue

                exit_reason = None
                exit_px     = None

                if INTRADAY_AMBIGUOUS_BAR_POLICY == "STOP_FIRST":
                    if l <= trade.stop_price:
                        exit_reason = "STOP_LOSS"
                        exit_px     = trade.stop_price
                    elif h >= trade.target_price:
                        exit_reason = "TAKE_PROFIT"
                        exit_px     = trade.target_price
                else:
                    if h >= trade.target_price:
                        exit_reason = "TAKE_PROFIT"
                        exit_px     = trade.target_price
                    elif l <= trade.stop_price:
                        exit_reason = "STOP_LOSS"
                        exit_px     = trade.stop_price

                if is_force_close and exit_reason is None:
                    exit_reason = "FORCED_CLOSE_EOD"
                    exit_px     = bar.get("close", trade.entry_price)

                if exit_reason:
                    _close_trade_bt(trade, exit_px, bar_ts_et, exit_reason, scenario, equity)
                    daily_pnl += trade.net_pnl_pln or 0.0
                    equity    += trade.net_pnl_pln or 0.0
                    cash      += (trade.pos_val_pln or 0) + (trade.net_pnl_pln or 0)
                    session_turnover += trade.pos_val_pln or 0
                    closed_trades.append(trade)
                else:
                    still_open.append(trade)

            open_trades = still_open

            # ── b. Execute pending orders (from previous bar) ─────────────────
            new_pending = []
            for ps in pending_signals:
                bar = cur_bars.get(ps["ticker"])
                if bar is None:
                    new_pending.append(ps)
                    continue
                bar_open = bar.get("open", ps["planned_entry"])
                atr = bar.get("atr14", 0.0) or 0.0

                # Gap check
                gap = abs(bar_open - ps["planned_entry"])
                if atr > 0 and gap > 0.5 * atr:
                    continue  # cancel

                fill_px = _fill_price(bar_open, "buy", scenario)
                if fill_px >= ps["stop_price"]:
                    continue

                rr = (ps["target_price"] - fill_px) / (fill_px - ps["stop_price"])
                if rr < MIN_REWARD_RISK:
                    continue

                rm = IntradayRiskManager(equity, INTRADAY_PLN_USD_RATE)
                sizing = rm.calc_position_size(fill_px, ps["stop_price"])
                if not sizing["valid"]:
                    continue

                shares = sizing["shares"]
                pos_val = fill_px * shares * INTRADAY_PLN_USD_RATE
                risk_pln = sizing["planned_risk_pln"]

                if pos_val > cash:
                    continue

                trade = _Trade(
                    ticker=ps["ticker"],
                    strategy=ps["strategy"],
                    sector=ps.get("sector", ""),
                    session_date=session_date,
                    entry_ts=bar_ts_et,
                    entry_price=fill_px,
                    stop_price=ps["stop_price"],
                    target_price=ps["target_price"],
                    shares=shares,
                    pos_val_pln=pos_val,
                    planned_risk_pln=risk_pln,
                    market_regime=ps.get("market_regime", "NEUTRAL"),
                    score=ps.get("score", 0.0),
                )
                open_trades.append(trade)
                cash -= pos_val
                session_turnover += pos_val

            pending_signals = new_pending

            # ── c. Scan for new signals ────────────────────────────────────────
            if can_enter and not is_force_close:
                spy_idf = intraday_session.get("SPY", pd.DataFrame())
                qqq_idf = intraday_session.get("QQQ", pd.DataFrame())
                regime = _regime_bt(spy_idf, bar_ts)

                for ti in tickers_info:
                    t = ti["ticker"]
                    sector = ti.get("sector", "")
                    idf = intraday_session.get(t, pd.DataFrame())
                    ddf = daily_cache.get(t, pd.DataFrame())
                    if idf.empty or ddf.empty:
                        continue

                    # Use only data up to current bar (no look-ahead)
                    idf_to_now = idf[idf.index <= bar_ts]
                    ddf_to_now = ddf[ddf.index.date <= session_date]
                    if idf_to_now.empty or ddf_to_now.empty:
                        continue

                    try:
                        sigs = run_all_intraday_strategies(
                            t, idf_to_now, ddf_to_now, spy_idf, sector, session_date
                        )
                    except Exception:
                        continue

                    for sig in sigs:
                        sig.sector = sector
                        sig.market_regime = regime
                        sc = score_signal(sig, idf_to_now, ddf_to_now, regime)
                        sig.score = sc
                        if sc < min_score:
                            continue
                        pending_signals.append({
                            "ticker": t,
                            "strategy": sig.strategy,
                            "sector": sector,
                            "planned_entry": sig.signal_bar_close,
                            "stop_price": sig.stop_price,
                            "target_price": sig.target_price,
                            "score": sc,
                            "market_regime": regime,
                        })

        # ── End of session: force close any remaining ─────────────────────────
        still_open2 = []
        for trade in open_trades:
            bar = cur_bars.get(trade.ticker) if cur_bars else None
            if bar is not None:
                exit_px = bar.get("close", trade.entry_price)
                _close_trade_bt(trade, exit_px, bar_ts_et, "FORCED_CLOSE_EOD", scenario, equity)
                daily_pnl += trade.net_pnl_pln or 0.0
                equity    += trade.net_pnl_pln or 0.0
                cash      += (trade.pos_val_pln or 0) + (trade.net_pnl_pln or 0)
                closed_trades.append(trade)
            else:
                still_open2.append(trade)
        open_trades = still_open2
        pending_signals = []  # cancel all pending signals at EOD

        # Daily snapshot
        snapshots.append({
            "date": session_date.isoformat(),
            "equity_pln": round(equity, 2),
            "daily_pnl_pln": round(daily_pnl, 2),
            "turnover_pln": round(session_turnover, 2),
            "open_positions": len(open_trades),
        })

        if verbose:
            log.info(f"{session_date}: equity={equity:.0f} daily_pnl={daily_pnl:.0f} "
                     f"closed={len(closed_trades)} open={len(open_trades)}")

    # ── 6. Final P&L reconciliation ───────────────────────────────────────────
    trades_list = [t.to_dict() for t in closed_trades]

    return {
        "trades": trades_list,
        "snapshots": snapshots,
        "initial_capital": INTRADAY_INITIAL_CAPITAL_PLN,
        "final_equity": round(equity, 2),
        "total_trades": len(trades_list),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "interval": interval,
        "cost_scenario": cost_scenario,
        "min_score": min_score,
        "tickers_scanned": len(tickers_info),
        "trading_days": len(trading_days),
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _close_trade_bt(
    trade: _Trade,
    exit_px: float,
    exit_ts: datetime.datetime,
    reason: str,
    scenario: dict,
    equity: float,
):
    """Compute and set P&L fields on a _Trade in-place."""
    fill_exit = _fill_price(exit_px, "sell", scenario)
    comm_e, spread_e, slip_e = _apply_costs(trade.entry_price, trade.shares, scenario)
    comm_x, spread_x, slip_x = _apply_costs(fill_exit, trade.shares, scenario)

    gross = (fill_exit - trade.entry_price) * trade.shares * INTRADAY_PLN_USD_RATE
    comm_pln   = (comm_e + comm_x) * INTRADAY_PLN_USD_RATE
    spread_pln = (spread_e + spread_x) * INTRADAY_PLN_USD_RATE
    slip_pln   = (slip_e + slip_x) * INTRADAY_PLN_USD_RATE
    net = gross - comm_pln - spread_pln

    holding = 0.0
    if trade.entry_ts and exit_ts:
        holding = (exit_ts - trade.entry_ts).total_seconds() / 60.0

    trade.exit_ts         = exit_ts
    trade.exit_price      = round(fill_exit, 4)
    trade.exit_reason     = reason
    trade.gross_pnl_pln   = round(gross, 2)
    trade.commission_pln  = round(comm_pln, 2)
    trade.spread_cost_pln = round(spread_pln, 2)
    trade.slippage_cost_pln = round(slip_pln, 2)
    trade.net_pnl_pln     = round(net, 2)
    trade.pnl_pct         = round((fill_exit - trade.entry_price) / trade.entry_price * 100, 4)
    trade.r_multiple      = round(net / trade.planned_risk_pln, 3) if trade.planned_risk_pln else 0.0
    trade.holding_minutes = round(holding, 1)


def _regime_bt(spy_idf: pd.DataFrame, bar_ts) -> str:
    """Simple regime from SPY intraday bars up to bar_ts."""
    if spy_idf is None or spy_idf.empty:
        return "NEUTRAL"
    df = spy_idf[spy_idf.index <= bar_ts]
    if df.empty:
        return "NEUTRAL"
    sr = df["session_return"].iloc[-1] if "session_return" in df.columns else np.nan
    vol = df["intraday_volatility"].iloc[-1] if "intraday_volatility" in df.columns else np.nan
    if not pd.isna(vol) and vol > 0.02:
        return "HIGH_VOLATILITY"
    if pd.isna(sr):
        return "NEUTRAL"
    if sr > 0.005:
        return "RISK_ON"
    if sr < -0.005:
        return "RISK_OFF"
    return "NEUTRAL"
