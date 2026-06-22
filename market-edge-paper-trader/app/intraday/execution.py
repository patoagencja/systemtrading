"""Intraday execution engine — one scan per 30m bar."""
import datetime
import logging
import uuid
from typing import Optional

import numpy as np
import pandas as pd

from app.database import db_cursor, get_connection
from app.intraday.config import (
    INTRADAY_MIN_SCORE,
    INTRADAY_PLN_USD_RATE,
)
from app.intraday.data_provider import IntradayDataProvider
from app.intraday.indicators import compute_intraday_indicators, compute_daily_indicators_for_filter
from app.intraday.paper_broker import IntradayPaperBroker
from app.intraday.portfolio import IntradayPortfolio
from app.intraday.risk_manager import IntradayRiskManager
from app.intraday.scoring import score_signal
from app.intraday.session_manager import SessionManager
from app.intraday.strategies import run_all_intraday_strategies, IntradaySignal
from app.intraday.universe import IntradayUniverse

log = logging.getLogger(__name__)


class IntradayExecutionEngine:
    """Drive one full 30m bar scan: close positions, fill orders, generate signals."""

    def __init__(self, run_mode: str = "live"):
        self.run_mode = run_mode
        self.provider  = IntradayDataProvider()
        self.portfolio = IntradayPortfolio(run_mode)
        self.broker    = IntradayPaperBroker(run_mode=run_mode)
        self.universe  = IntradayUniverse()
        self._tickers: list[dict] = []
        self._daily_cache: dict[str, pd.DataFrame] = {}

    # ── Main entry point ─────────────────────────────────────────────────────

    def run_scan(
        self,
        bar_timestamp: datetime.datetime,
        session_date: datetime.date,
        force_close: bool = False,
    ) -> dict:
        """Execute one full bar scan. Returns summary dict."""
        run_id = str(uuid.uuid4())[:16]
        started_at = datetime.datetime.utcnow().isoformat()
        stats = {"positions_closed": 0, "orders_filled": 0,
                 "signals_generated": 0, "errors": [], "warnings": []}

        self._log_run_start(run_id, bar_timestamp, session_date, started_at)

        try:
            # 1. Refresh portfolio state
            self.portfolio.refresh(session_date)

            # 2. Load universe if needed
            if not self._tickers:
                self._tickers = self.universe.load()

            # 3. Fetch bar data for all tickers (intraday + daily)
            bar_data = self._build_bar_data(bar_timestamp, session_date)

            # 4. Determine market regime
            spy_df  = bar_data.get("SPY", {}).get("intraday", pd.DataFrame())
            qqq_df  = bar_data.get("QQQ", {}).get("intraday", pd.DataFrame())
            regime = self._determine_market_regime(spy_df, qqq_df)

            # 5. Check open positions against current bar OHLC
            closed = self._check_open_positions(bar_data)
            stats["positions_closed"] += len(closed)

            # 6. Force close if needed
            if force_close or SessionManager.should_force_close(bar_timestamp):
                forced = self._force_close_all(bar_timestamp)
                stats["positions_closed"] += len(forced)

            # 7. Execute pending orders
            filled = self._execute_pending_orders(bar_timestamp, bar_data)
            stats["orders_filled"] += len(filled)

            # 8. Scan for new signals (if entry window allows)
            if (not force_close
                    and SessionManager.can_enter_new_position(bar_timestamp)
                    and not SessionManager.should_force_close(bar_timestamp)):
                new_signals = self._scan_for_signals(bar_timestamp, session_date, bar_data, regime)
                stats["signals_generated"] += len(new_signals)

            # 9. Save snapshot
            self.portfolio.refresh(session_date)
            self.portfolio.save_snapshot(bar_timestamp, session_date)

        except Exception as e:
            log.exception(f"run_scan error: {e}")
            stats["errors"].append(str(e))

        self._log_run_end(run_id, stats, session_date, bar_timestamp)
        return stats

    # ── Open position management ──────────────────────────────────────────────

    def _check_open_positions(self, bar_data: dict) -> list[dict]:
        """Check SL/TP on each open position; close if hit."""
        open_trades = self.portfolio.get_open_trades()
        closed = []
        for trade in open_trades:
            ticker = trade["ticker"]
            ticker_data = bar_data.get(ticker, {})
            intraday = ticker_data.get("intraday", pd.DataFrame())
            if intraday.empty:
                continue
            bar = intraday.iloc[-1]
            bar_high = bar.get("high", np.nan)
            bar_low  = bar.get("low", np.nan)
            bar_close = bar.get("close", np.nan)
            if any(pd.isna(v) for v in [bar_high, bar_low, bar_close]):
                continue

            stop   = trade["stop_price"]
            target = trade["target_price"]
            ts     = intraday.index[-1]

            reason = None
            exit_px = None

            # Per INTRADAY_AMBIGUOUS_BAR_POLICY = STOP_FIRST
            if bar_low <= stop:
                reason  = "STOP_LOSS"
                exit_px = stop
            elif bar_high >= target:
                reason  = "TAKE_PROFIT"
                exit_px = target

            if reason and exit_px:
                result = self.broker.close_trade(
                    trade["id"], exit_px, ts, reason
                )
                closed.append({"trade_id": trade["id"], "ticker": ticker, **result})

        return closed

    def _force_close_all(self, bar_timestamp: datetime.datetime) -> list[dict]:
        """Close all remaining open positions at market."""
        open_trades = self.portfolio.get_open_trades()
        closed = []
        for trade in open_trades:
            ticker = trade["ticker"]
            try:
                # Use entry price as fallback; real system would use last close
                exit_px = trade["entry_price"]
                result = self.broker.close_trade(
                    trade["id"], exit_px, bar_timestamp,
                    "FORCED_CLOSE_EOD", is_forced_close=True
                )
                closed.append(result)
            except Exception as e:
                log.warning(f"Force close {ticker}: {e}")
        return closed

    # ── Order execution ───────────────────────────────────────────────────────

    def _execute_pending_orders(
        self,
        bar_timestamp: datetime.datetime,
        bar_data: dict,
    ) -> list[dict]:
        """Fill PENDING signals from previous bar at this bar's open."""
        filled = []
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, ticker, strategy, sector, session_date,
                       planned_entry, stop_price, target_price, score, market_regime
                FROM intraday_signals
                WHERE run_mode=? AND status='PENDING'
                ORDER BY score DESC
            """, (self.run_mode,))
            pending = cur.fetchall()
        finally:
            conn.close()

        for row in pending:
            (sig_id, ticker, strategy, sector, sd_str,
             planned_entry, stop_price, target_price, score, regime) = row

            ticker_data = bar_data.get(ticker, {})
            intraday = ticker_data.get("intraday", pd.DataFrame())
            if intraday.empty:
                self.broker.cancel_order(sig_id, "no bar data")
                continue

            bar_open  = intraday.iloc[-1].get("open", planned_entry)
            atr_usd   = intraday.iloc[-1].get("atr14", 0.0) or 0.0

            # Risk sizing
            rm = IntradayRiskManager(self.portfolio.equity_pln, INTRADAY_PLN_USD_RATE)
            sizing = rm.calc_position_size(bar_open, stop_price)
            if not sizing["valid"]:
                self.broker.cancel_order(sig_id, sizing["reason"])
                continue

            shares = rm.apply_volatility_adjustment(sizing["shares"], regime or "NEUTRAL")

            # Check portfolio constraints
            sec_exp = self.portfolio.get_sector_exposure(sector or "")
            ok, reason = rm.can_open_position(
                position_value_pln=sizing["position_value_pln"],
                planned_risk_pln=sizing["planned_risk_pln"],
                sector=sector or "",
                current_invested_pln=self.portfolio.invested_pln,
                current_open_risk_pln=self.portfolio.total_open_risk_pln,
                open_positions_count=self.portfolio.open_positions_count,
                sector_invested_pln=sec_exp["invested_pln"],
                sector_open_risk_pln=sec_exp["open_risk_pln"],
                sector_position_count=sec_exp["count"],
                daily_pnl_pln=self.portfolio.realized_pnl_today_pln,
                daily_drawdown_pct=self.portfolio.daily_drawdown_pct,
                consecutive_losses_today=self.portfolio.consecutive_losses_today,
                weekly_pnl_pln=self.portfolio.get_weekly_pnl(),
                market_regime=regime or "NEUTRAL",
            )
            if not ok:
                self.broker.cancel_order(sig_id, reason)
                continue

            result = self.broker.fill_order(
                signal_id=sig_id,
                ticker=ticker,
                strategy=strategy,
                sector=sector or "",
                session_date=sd_str,
                entry_timestamp=bar_timestamp,
                planned_entry=planned_entry,
                bar_open=bar_open,
                stop_price=stop_price,
                target_price=target_price,
                shares=shares,
                position_value_pln=sizing["position_value_pln"],
                planned_risk_pln=sizing["planned_risk_pln"],
                market_regime=regime or "NEUTRAL",
                score=score or 0.0,
                atr_usd=atr_usd,
            )
            if not result.get("cancelled"):
                filled.append(result)
                self.portfolio.refresh()

        return filled

    # ── Signal scanning ───────────────────────────────────────────────────────

    def _scan_for_signals(
        self,
        bar_timestamp: datetime.datetime,
        session_date: datetime.date,
        bar_data: dict,
        market_regime: str,
    ) -> list[IntradaySignal]:
        """Run strategies on all tickers, save signals above min score."""
        spy_intraday = bar_data.get("SPY", {}).get("intraday", pd.DataFrame())
        all_signals: list[IntradaySignal] = []

        for ticker_info in self._tickers:
            ticker = ticker_info["ticker"]
            sector = ticker_info.get("sector", "")
            td = bar_data.get(ticker, {})
            intraday = td.get("intraday", pd.DataFrame())
            daily    = td.get("daily", pd.DataFrame())

            if intraday.empty or daily.empty:
                continue

            try:
                sigs = run_all_intraday_strategies(
                    ticker, intraday, daily, spy_intraday, sector, session_date
                )
            except Exception as e:
                log.debug(f"Strategy scan {ticker}: {e}")
                continue

            for sig in sigs:
                sig.sector = sector
                sig.market_regime = market_regime
                score = score_signal(sig, intraday, daily, market_regime)
                sig.score = score

                if score < INTRADAY_MIN_SCORE:
                    continue

                self._save_signal(sig, session_date)
                all_signals.append(sig)

        return all_signals

    def _save_signal(self, sig: IntradaySignal, session_date: datetime.date):
        """Persist signal to DB."""
        sd_str = session_date.isoformat() if hasattr(session_date, "isoformat") else str(session_date)
        ts_str = sig.signal_timestamp.isoformat() if hasattr(sig.signal_timestamp, "isoformat") else str(sig.signal_timestamp)
        with db_cursor() as cur:
            try:
                cur.execute("""
                    INSERT OR IGNORE INTO intraday_signals
                    (ticker, strategy, sector, session_date, signal_timestamp,
                     signal_bar_close, score, planned_entry, stop_price, target_price,
                     reward_risk, market_regime, status, run_mode)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
                """, (
                    sig.ticker, sig.strategy, sig.sector, sd_str, ts_str,
                    round(sig.signal_bar_close, 4), round(sig.score, 1),
                    round(sig.planned_entry, 4), round(sig.stop_price, 4),
                    round(sig.target_price, 4), round(sig.reward_risk, 3),
                    sig.market_regime, self.run_mode,
                ))
            except Exception as e:
                log.debug(f"save_signal {sig.ticker}: {e}")

    # ── Market regime ─────────────────────────────────────────────────────────

    def _determine_market_regime(
        self,
        spy_df: pd.DataFrame,
        qqq_df: pd.DataFrame,
    ) -> str:
        """RISK_ON, NEUTRAL, RISK_OFF, or HIGH_VOLATILITY."""
        if spy_df is None or spy_df.empty:
            return "NEUTRAL"

        def _last(df, col):
            if col in df.columns and len(df) > 0:
                v = df[col].iloc[-1]
                return float(v) if not pd.isna(v) else np.nan
            return np.nan

        vwap_z = _last(spy_df, "vwap_zscore")
        rvol   = _last(spy_df, "relative_volume")
        sr     = _last(spy_df, "session_return")
        vol    = _last(spy_df, "intraday_volatility")

        # High volatility: intraday vol spike
        if not pd.isna(vol) and vol > 0.02:
            return "HIGH_VOLATILITY"

        if pd.isna(sr):
            return "NEUTRAL"

        if sr > 0.005 and (pd.isna(vwap_z) or vwap_z > 0):
            return "RISK_ON"
        if sr < -0.005 and (pd.isna(vwap_z) or vwap_z < 0):
            return "RISK_OFF"

        return "NEUTRAL"

    # ── Data loading ─────────────────────────────────────────────────────────

    def _build_bar_data(
        self,
        bar_timestamp: datetime.datetime,
        session_date: datetime.date,
    ) -> dict:
        """Fetch intraday + daily data for all tickers up to bar_timestamp."""
        result = {}
        intraday_start = session_date - datetime.timedelta(days=5)
        daily_start    = session_date - datetime.timedelta(days=400)

        for ticker_info in self._tickers:
            ticker = ticker_info["ticker"]
            try:
                # Intraday
                idf = self.provider.get_intraday_bars(
                    ticker, intraday_start, session_date + datetime.timedelta(days=1)
                )
                if not idf.empty:
                    idf = idf[idf.index <= bar_timestamp]
                    idf = compute_intraday_indicators(idf, session_date)

                # Daily (cache to avoid re-fetching every bar)
                if ticker not in self._daily_cache:
                    ddf = self.provider.get_daily_bars(ticker, daily_start, session_date)
                    if not ddf.empty:
                        ddf = compute_daily_indicators_for_filter(ddf)
                    self._daily_cache[ticker] = ddf
                ddf = self._daily_cache.get(ticker, pd.DataFrame())

                result[ticker] = {"intraday": idf, "daily": ddf}
            except Exception as e:
                log.debug(f"Data load {ticker}: {e}")

        return result

    # ── DB logging ────────────────────────────────────────────────────────────

    def _log_run_start(self, run_id, bar_ts, session_date, started_at):
        sd_str = session_date.isoformat() if hasattr(session_date, "isoformat") else str(session_date)
        bt_str = bar_ts.isoformat() if hasattr(bar_ts, "isoformat") else str(bar_ts)
        with db_cursor() as cur:
            try:
                cur.execute("""
                    INSERT OR IGNORE INTO intraday_runs
                    (run_id, session_date, bar_timestamp, started_at, status, run_mode)
                    VALUES (?, ?, ?, ?, 'running', ?)
                """, (run_id, sd_str, bt_str, started_at, self.run_mode))
            except Exception:
                pass
        self._current_run_id = run_id

    def _log_run_end(self, run_id, stats, session_date, bar_ts):
        completed_at = datetime.datetime.utcnow().isoformat()
        sd_str = session_date.isoformat() if hasattr(session_date, "isoformat") else str(session_date)
        with db_cursor() as cur:
            try:
                cur.execute("""
                    UPDATE intraday_runs SET
                        completed_at=?, status='completed',
                        signals_generated=?, positions_closed=?,
                        orders_created=?,
                        warnings=?, errors=?
                    WHERE run_id=?
                """, (
                    completed_at,
                    stats["signals_generated"],
                    stats["positions_closed"],
                    stats["orders_filled"],
                    ",".join(stats["warnings"]) if stats["warnings"] else None,
                    ",".join(stats["errors"]) if stats["errors"] else None,
                    run_id,
                ))
            except Exception:
                pass
