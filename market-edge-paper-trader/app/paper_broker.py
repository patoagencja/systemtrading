from datetime import date, datetime
import pandas as pd
from app.database import db_cursor, get_connection
from app.strategies import Signal
from app.config import PLN_USD_RATE


class PaperBroker:
    def __init__(self, pln_usd_rate: float = PLN_USD_RATE, run_mode: str = "backtest"):
        self.rate = pln_usd_rate
        self.run_mode = run_mode

    def open_trade(self, signal: Signal, shares: float, position_value_pln: float,
                   risk_pln: float, trade_date: date) -> int:
        with db_cursor() as cur:
            cur.execute("""
                INSERT INTO trades
                (ticker, strategy, status, entry_date, entry_price, stop_loss, take_profit,
                 shares, position_value_pln, risk_pln, score, entry_reason, max_holding_days,
                 pnl_pln, pnl_pct, r_multiple, holding_days, run_mode)
                VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, 0, ?)
            """, (
                signal.ticker, signal.strategy, str(trade_date),
                round(signal.entry_price, 4), round(signal.stop_loss, 4),
                round(signal.take_profit, 4), round(shares, 4),
                round(position_value_pln, 2), round(risk_pln, 2),
                round(signal.score, 1), signal.reason, signal.max_holding_days,
                self.run_mode,
            ))
            return cur.lastrowid

    def save_signal(self, signal: Signal, signal_date: date, acted: bool = False):
        with db_cursor() as cur:
            cur.execute("""
                INSERT INTO signals
                (ticker, strategy, signal_date, score, entry_price, stop_loss,
                 take_profit, atr, rsi, volume_ratio, reason, acted, run_mode)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                signal.ticker, signal.strategy, str(signal_date),
                round(signal.score, 1), round(signal.entry_price, 4),
                round(signal.stop_loss, 4), round(signal.take_profit, 4),
                round(signal.atr, 4), round(signal.rsi, 2),
                round(signal.volume_ratio, 3), signal.reason, int(acted),
                self.run_mode,
            ))

    def update_open_trade_pnl(self, trade_id: int, current_price_usd: float, holding_days: int):
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("SELECT entry_price, shares, risk_pln FROM trades WHERE id=?", (trade_id,))
            row = cur.fetchone()
            if not row:
                return
            entry_price, shares, risk_pln = row
            pnl_usd = (current_price_usd - entry_price) * shares
            pnl_pln = pnl_usd * self.rate
            pnl_pct = (current_price_usd - entry_price) / entry_price * 100
            r_multiple = pnl_pln / risk_pln if risk_pln > 0 else 0
            cur.execute("""
                UPDATE trades SET pnl_pln=?, pnl_pct=?, r_multiple=?, holding_days=?
                WHERE id=?
            """, (round(pnl_pln, 2), round(pnl_pct, 4), round(r_multiple, 3),
                  holding_days, trade_id))
            conn.commit()
        finally:
            conn.close()

    def close_trade(self, trade_id: int, exit_price_usd: float, exit_date: date, reason: str):
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT entry_price, shares, risk_pln, entry_date FROM trades WHERE id=?",
                (trade_id,)
            )
            row = cur.fetchone()
            if not row:
                return
            entry_price, shares, risk_pln, entry_date_str = row
            pnl_usd = (exit_price_usd - entry_price) * shares
            pnl_pln = pnl_usd * self.rate
            pnl_pct = (exit_price_usd - entry_price) / entry_price * 100
            r_multiple = pnl_pln / risk_pln if risk_pln > 0 else 0

            entry_dt = date.fromisoformat(entry_date_str)
            holding_days = (exit_date - entry_dt).days

            cur.execute("""
                UPDATE trades SET
                  status='closed', exit_date=?, exit_price=?, exit_reason=?,
                  pnl_pln=?, pnl_pct=?, r_multiple=?, holding_days=?
                WHERE id=?
            """, (
                str(exit_date), round(exit_price_usd, 4), reason,
                round(pnl_pln, 2), round(pnl_pct, 4), round(r_multiple, 3),
                holding_days, trade_id,
            ))
            conn.commit()
        finally:
            conn.close()

    def update_strategy_stats(self):
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT DISTINCT strategy FROM trades WHERE status='closed' AND run_mode=?",
                (self.run_mode,)
            )
            strategies = [r[0] for r in cur.fetchall()]
            for strat in strategies:
                cur.execute("""
                    SELECT pnl_pln, r_multiple, holding_days
                    FROM trades WHERE status='closed' AND strategy=? AND run_mode=?
                """, (strat, self.run_mode))
                rows = cur.fetchall()
                total = len(rows)
                wins = [r for r in rows if r[0] > 0]
                losses = [r for r in rows if r[0] <= 0]
                total_pnl = sum(r[0] for r in rows)
                avg_win = sum(r[0] for r in wins) / len(wins) if wins else 0
                avg_loss = sum(r[0] for r in losses) / len(losses) if losses else 0
                gross_profit = sum(r[0] for r in wins)
                gross_loss = abs(sum(r[0] for r in losses))
                profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0
                win_rate = len(wins) / total * 100 if total > 0 else 0
                avg_r = sum(r[1] for r in rows) / total if total > 0 else 0
                avg_hold = sum(r[2] for r in rows) / total if total > 0 else 0
                cur.execute("""
                    INSERT OR REPLACE INTO strategy_stats
                    (strategy, run_mode, total_trades, win_trades, loss_trades, total_pnl_pln,
                     avg_win_pln, avg_loss_pln, profit_factor, win_rate, avg_r_multiple,
                     avg_holding_days, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """, (strat, self.run_mode, total, len(wins), len(losses), round(total_pnl, 2),
                      round(avg_win, 2), round(avg_loss, 2), round(profit_factor, 3),
                      round(win_rate, 2), round(avg_r, 3), round(avg_hold, 2)))
            conn.commit()
        finally:
            conn.close()
