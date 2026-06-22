"""Intraday portfolio state, loaded from the database."""
import datetime
import logging

from app.database import db_cursor, get_connection
from app.intraday.config import INTRADAY_INITIAL_CAPITAL_PLN

log = logging.getLogger(__name__)


class IntradayPortfolio:
    """Read-through portfolio backed by intraday_trades + intraday_snapshots."""

    def __init__(self, run_mode: str = "live"):
        self.run_mode = run_mode
        self._equity = INTRADAY_INITIAL_CAPITAL_PLN
        self._cash = INTRADAY_INITIAL_CAPITAL_PLN
        self._invested = 0.0
        self._realized_today = 0.0
        self._unrealized = 0.0
        self._open_risk = 0.0
        self._open_positions = 0
        self._dd = 0.0
        self._consec_losses = 0
        self._session_high = INTRADAY_INITIAL_CAPITAL_PLN

    # ── Properties ─────────────────────────────────────────────────────────

    @property
    def equity_pln(self) -> float:
        return self._equity

    @property
    def cash_pln(self) -> float:
        return self._cash

    @property
    def invested_pln(self) -> float:
        return self._invested

    @property
    def open_positions_count(self) -> int:
        return self._open_positions

    @property
    def realized_pnl_today_pln(self) -> float:
        return self._realized_today

    @property
    def unrealized_pnl_pln(self) -> float:
        return self._unrealized

    @property
    def total_open_risk_pln(self) -> float:
        return self._open_risk

    @property
    def daily_drawdown_pct(self) -> float:
        return self._dd

    @property
    def consecutive_losses_today(self) -> int:
        return self._consec_losses

    # ── Refresh ─────────────────────────────────────────────────────────────

    def refresh(self, session_date=None):
        """Reload all state from DB."""
        conn = get_connection()
        try:
            cur = conn.cursor()
            sd = session_date.isoformat() if hasattr(session_date, "isoformat") else str(session_date or "")

            # Open trades
            cur.execute("""
                SELECT position_value_pln, planned_risk_pln,
                       COALESCE(net_pnl_pln, 0) as unreal,
                       entry_price, stop_price, shares
                FROM intraday_trades
                WHERE run_mode=? AND status='open'
            """, (self.run_mode,))
            open_rows = cur.fetchall()

            self._invested = sum(r[0] for r in open_rows)
            self._unrealized = sum(r[2] for r in open_rows)
            self._open_positions = len(open_rows)

            # Open risk = sum of (entry - stop) * shares for open trades
            self._open_risk = 0.0
            for r in open_rows:
                pos_val, risk, unreal, entry, stop, shares = r
                if entry and stop and shares:
                    self._open_risk += max(0, (entry - stop) * shares)

            # Realized P&L today
            if sd:
                cur.execute("""
                    SELECT COALESCE(SUM(net_pnl_pln), 0)
                    FROM intraday_trades
                    WHERE run_mode=? AND status='closed' AND session_date=?
                """, (self.run_mode, sd))
                self._realized_today = cur.fetchone()[0] or 0.0
            else:
                self._realized_today = 0.0

            # Equity = initial + all closed PnL + unrealized
            cur.execute("""
                SELECT COALESCE(SUM(net_pnl_pln), 0)
                FROM intraday_trades
                WHERE run_mode=? AND status='closed'
            """, (self.run_mode,))
            total_closed_pnl = cur.fetchone()[0] or 0.0
            self._equity = INTRADAY_INITIAL_CAPITAL_PLN + total_closed_pnl + self._unrealized
            self._cash = self._equity - self._invested

            # Daily drawdown
            session_high = INTRADAY_INITIAL_CAPITAL_PLN + total_closed_pnl - self._realized_today
            session_high = max(session_high, session_high + self._realized_today)
            if session_high > 0:
                self._dd = max(0.0, (session_high - self._equity) / session_high)
            else:
                self._dd = 0.0

            # Consecutive losses today
            if sd:
                cur.execute("""
                    SELECT net_pnl_pln FROM intraday_trades
                    WHERE run_mode=? AND status='closed' AND session_date=?
                    ORDER BY exit_timestamp DESC LIMIT 20
                """, (self.run_mode, sd))
                recents = [r[0] for r in cur.fetchall()]
                consec = 0
                for pnl in recents:
                    if pnl is not None and pnl <= 0:
                        consec += 1
                    else:
                        break
                self._consec_losses = consec
            else:
                self._consec_losses = 0

        finally:
            conn.close()

    def get_open_trades(self) -> list[dict]:
        """Return list of open trade dicts."""
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT id, ticker, strategy, sector, entry_timestamp, entry_price,
                       stop_price, target_price, shares, position_value_pln,
                       planned_risk_pln, COALESCE(net_pnl_pln, 0), market_regime
                FROM intraday_trades
                WHERE run_mode=? AND status='open'
            """, (self.run_mode,))
            cols = ["id","ticker","strategy","sector","entry_timestamp","entry_price",
                    "stop_price","target_price","shares","position_value_pln",
                    "planned_risk_pln","unrealized_pnl","market_regime"]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_sector_exposure(self, sector: str) -> dict:
        """Return {invested_pln, open_risk_pln, count} for a sector."""
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT position_value_pln, planned_risk_pln, entry_price, stop_price, shares
                FROM intraday_trades
                WHERE run_mode=? AND status='open' AND sector=?
            """, (self.run_mode, sector))
            rows = cur.fetchall()
            invested = sum(r[0] for r in rows)
            risk = sum(max(0, (r[2] - r[3]) * r[4]) if r[2] and r[3] and r[4] else r[1]
                       for r in rows)
            return {"invested_pln": invested, "open_risk_pln": risk, "count": len(rows)}
        finally:
            conn.close()

    def save_snapshot(
        self,
        timestamp,
        session_date,
        daily_turnover: float = 0,
    ):
        """Persist portfolio snapshot."""
        ts_str = timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp)
        sd_str = session_date.isoformat() if hasattr(session_date, "isoformat") else str(session_date)
        with db_cursor() as cur:
            cur.execute("""
                INSERT INTO intraday_snapshots
                (timestamp, session_date, run_mode, equity_pln, cash_pln,
                 invested_pln, realized_pnl_today, unrealized_pnl,
                 total_open_risk, total_exposure, open_positions,
                 daily_drawdown, daily_turnover)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                ts_str, sd_str, self.run_mode,
                round(self._equity, 2), round(self._cash, 2),
                round(self._invested, 2), round(self._realized_today, 2),
                round(self._unrealized, 2), round(self._open_risk, 2),
                round(self._invested / self._equity if self._equity > 0 else 0, 4),
                self._open_positions,
                round(self._dd, 6), round(daily_turnover, 2),
            ))

    def get_weekly_pnl(self) -> float:
        """Return net P&L for the current calendar week (Mon-Sun)."""
        conn = get_connection()
        try:
            cur = conn.cursor()
            today = datetime.date.today()
            week_start = today - datetime.timedelta(days=today.weekday())
            cur.execute("""
                SELECT COALESCE(SUM(net_pnl_pln), 0)
                FROM intraday_trades
                WHERE run_mode=? AND status='closed' AND session_date >= ?
            """, (self.run_mode, week_start.isoformat()))
            return cur.fetchone()[0] or 0.0
        finally:
            conn.close()

    def get_daily_pnl(self, session_date) -> float:
        """Return net P&L for a specific session date."""
        conn = get_connection()
        try:
            cur = conn.cursor()
            sd = session_date.isoformat() if hasattr(session_date, "isoformat") else str(session_date)
            cur.execute("""
                SELECT COALESCE(SUM(net_pnl_pln), 0)
                FROM intraday_trades
                WHERE run_mode=? AND status='closed' AND session_date=?
            """, (self.run_mode, sd))
            return cur.fetchone()[0] or 0.0
        finally:
            conn.close()
