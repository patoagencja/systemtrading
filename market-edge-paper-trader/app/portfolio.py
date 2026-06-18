import sqlite3
from datetime import date
from app.database import db_cursor, get_connection
from app.config import INITIAL_CAPITAL_PLN, PLN_USD_RATE


class Portfolio:
    def __init__(self):
        self._refresh()

    def _refresh(self):
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT SUM(position_value_pln) FROM trades WHERE status='open'"
            )
            row = cur.fetchone()
            self.invested_pln = row[0] or 0.0

            cur.execute("SELECT SUM(pnl_pln) FROM trades WHERE status='closed'")
            row = cur.fetchone()
            realized_pnl = row[0] or 0.0

            self.total_value_pln = INITIAL_CAPITAL_PLN + realized_pnl + self._unrealized_pnl(cur)
            self.cash_pln = self.total_value_pln - self.invested_pln

            cur.execute("SELECT COUNT(*) FROM trades WHERE status='open'")
            self.open_positions = cur.fetchone()[0]

            cur.execute("SELECT COUNT(*) FROM trades WHERE status='closed'")
            self.closed_trades = cur.fetchone()[0]
        finally:
            conn.close()

    def _unrealized_pnl(self, cur) -> float:
        cur.execute("SELECT pnl_pln FROM trades WHERE status='open'")
        rows = cur.fetchall()
        return sum(r[0] or 0.0 for r in rows)

    @property
    def total_return_pct(self) -> float:
        return (self.total_value_pln - INITIAL_CAPITAL_PLN) / INITIAL_CAPITAL_PLN * 100

    def get_open_tickers(self) -> set[str]:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT DISTINCT ticker FROM trades WHERE status='open'")
            return {row[0] for row in cur.fetchall()}
        finally:
            conn.close()

    def get_open_trades(self) -> list[dict]:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM trades WHERE status='open'")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def get_closed_trades(self) -> list[dict]:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM trades WHERE status='closed' ORDER BY exit_date DESC")
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        finally:
            conn.close()

    def save_snapshot(self, today: date, daily_pnl: float = 0.0):
        self._refresh()
        wins = self._count_results("win")
        losses = self._count_results("loss")
        total_closed = wins + losses
        max_dd = self._calc_max_drawdown()

        with db_cursor() as cur:
            cur.execute("""
                INSERT OR REPLACE INTO portfolio_snapshots
                (snapshot_date, total_value_pln, cash_pln, invested_pln,
                 open_positions, daily_pnl_pln, total_pnl_pln, total_return_pct,
                 max_drawdown_pct, win_trades, loss_trades, total_closed_trades)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(today),
                round(self.total_value_pln, 2),
                round(self.cash_pln, 2),
                round(self.invested_pln, 2),
                self.open_positions,
                round(daily_pnl, 2),
                round(self.total_value_pln - INITIAL_CAPITAL_PLN, 2),
                round(self.total_return_pct, 4),
                round(max_dd, 4),
                wins,
                losses,
                total_closed,
            ))

    def _count_results(self, result: str) -> int:
        conn = get_connection()
        try:
            cur = conn.cursor()
            if result == "win":
                cur.execute("SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl_pln > 0")
            else:
                cur.execute("SELECT COUNT(*) FROM trades WHERE status='closed' AND pnl_pln <= 0")
            return cur.fetchone()[0]
        finally:
            conn.close()

    def _calc_max_drawdown(self) -> float:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT total_value_pln FROM portfolio_snapshots ORDER BY snapshot_date ASC")
            values = [r[0] for r in cur.fetchall()]
            values.append(self.total_value_pln)
        finally:
            conn.close()

        if len(values) < 2:
            return 0.0

        peak = values[0]
        max_dd = 0.0
        for v in values:
            if v > peak:
                peak = v
            dd = (peak - v) / peak
            if dd > max_dd:
                max_dd = dd
        return max_dd * 100
