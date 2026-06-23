from datetime import date, datetime
import pandas as pd
from app.database import db_cursor, get_connection
from app.strategies import Signal
from app.config import PLN_USD_RATE, COMMISSION_PCT, SLIPPAGE_PCT


def _apply_costs(price: float, side: str,
                 shares: float = 0, avg_volume: float = 0) -> tuple[float, float]:
    """Return (adjusted_price, cost_usd_per_share) after slippage + commission.

    Slippage scales with participation rate (shares / avg_daily_volume):
      < 0.1% of daily volume  → 1.0× base slippage
      0.1–1%                  → 2.0×
      1–5%                    → 3.5×
      > 5%                    → 6.0×
    """
    if avg_volume > 0 and shares > 0:
        p = shares / avg_volume
        slip_mult = 6.0 if p >= 0.05 else 3.5 if p >= 0.01 else 2.0 if p >= 0.001 else 1.0
    else:
        slip_mult = 1.0

    slip = price * SLIPPAGE_PCT * slip_mult
    comm = price * COMMISSION_PCT
    total_cost_per_share = slip + comm
    if side == "buy":
        effective_price = price + slip
    else:
        effective_price = price - slip
    return effective_price, total_cost_per_share


class PaperBroker:
    def __init__(self, pln_usd_rate: float = PLN_USD_RATE, run_mode: str = "backtest"):
        self.rate = pln_usd_rate
        self.run_mode = run_mode

    def open_trade(self, signal: Signal, shares: float, position_value_pln: float,
                   risk_pln: float, trade_date: date, avg_volume: float = 0,
                   exit_logic_version: str = "LEGACY_EXIT_LOGIC") -> int:
        eff_entry, cost_per_share = _apply_costs(signal.entry_price, "buy", shares, avg_volume)
        entry_cost_pln = cost_per_share * shares * self.rate
        actual_position_pln = eff_entry * shares * self.rate
        # New fields for SIMPLE_DYNAMIC_EXIT_V1
        initial_stop_loss = signal.stop_loss  # will be overridden if using new logic
        active_stop_loss = signal.stop_loss
        with db_cursor() as cur:
            cur.execute("""
                INSERT INTO trades
                (ticker, strategy, status, entry_date, entry_price, stop_loss, take_profit,
                 shares, position_value_pln, risk_pln, score, entry_reason, max_holding_days,
                 pnl_pln, pnl_pct, r_multiple, holding_days, run_mode,
                 initial_stop_loss, active_stop_loss, exit_logic_version,
                 highest_high_since_entry, highest_close_since_entry,
                 stop_status, active_stop_effective_date)
                VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?,
                        ?, ?, ?, ?, ?, 'INITIAL', ?)
            """, (
                signal.ticker, signal.strategy, str(trade_date),
                round(eff_entry, 4), round(signal.stop_loss, 4),
                round(signal.take_profit, 4), round(shares, 4),
                round(actual_position_pln, 2), round(risk_pln, 2),
                round(signal.score, 1), signal.reason, signal.max_holding_days,
                round(-entry_cost_pln, 2),  # initial pnl already negative (commission)
                self.run_mode,
                round(initial_stop_loss, 4), round(active_stop_loss, 4),
                exit_logic_version, round(eff_entry, 4), round(eff_entry, 4),
                str(trade_date),
            ))
            return cur.lastrowid

    def open_trade_v2(self, signal: Signal, shares: float, position_value_pln: float,
                      risk_pln: float, trade_date: date, avg_volume: float,
                      actual_entry: float, initial_stop: float, take_profit_price: float,
                      exit_logic_version: str) -> int:
        """Open a trade with validated SIMPLE_DYNAMIC_EXIT_V1 parameters.

        `actual_entry` is the realised T+1 open *before* slippage; costs are
        applied here. `initial_stop` / `take_profit_price` come from
        exit_logic.validate_signal_for_new_logic().
        """
        eff_entry, cost_per_share = _apply_costs(actual_entry, "buy", shares, avg_volume)
        entry_cost_pln = cost_per_share * shares * self.rate
        actual_position_pln = eff_entry * shares * self.rate
        with db_cursor() as cur:
            cur.execute("""
                INSERT INTO trades
                (ticker, strategy, status, entry_date, entry_price, stop_loss, take_profit,
                 shares, position_value_pln, risk_pln, score, entry_reason, max_holding_days,
                 pnl_pln, pnl_pct, r_multiple, holding_days, run_mode,
                 initial_stop_loss, active_stop_loss, exit_logic_version,
                 highest_high_since_entry, highest_close_since_entry,
                 max_profit_pct, locked_profit_pct, stop_status,
                 active_stop_effective_date, max_unrealized_pnl_pln, max_unrealized_pnl_pct)
                VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?,
                        ?, ?, ?, ?, ?, 0, 0, 'INITIAL', ?, 0, 0)
            """, (
                signal.ticker, signal.strategy, str(trade_date),
                round(eff_entry, 4), round(initial_stop, 4),
                round(take_profit_price, 4), round(shares, 4),
                round(actual_position_pln, 2), round(risk_pln, 2),
                round(signal.score, 1), signal.reason, signal.max_holding_days,
                round(-entry_cost_pln, 2),
                self.run_mode,
                round(initial_stop, 4), round(initial_stop, 4),
                exit_logic_version, round(eff_entry, 4), round(eff_entry, 4),
                str(trade_date),
            ))
            return cur.lastrowid

    def update_trade_stop(self, trade_id: int, new_active_stop: float, stop_status: str,
                          session_date, max_profit_pct: float, locked_profit_pct: float,
                          highest_high: float, highest_close: float,
                          max_unrealized_pnl_pct: float):
        """Persist the current dynamic-stop / tracking state for an open trade."""
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute("""
                UPDATE trades SET
                  active_stop_loss=?, stop_loss=?, stop_status=?,
                  active_stop_effective_date=?, max_profit_pct=?, locked_profit_pct=?,
                  highest_high_since_entry=?, highest_close_since_entry=?,
                  max_unrealized_pnl_pct=?
                WHERE id=?
            """, (
                round(new_active_stop, 4), round(new_active_stop, 4), stop_status,
                str(session_date), round(max_profit_pct, 6), round(locked_profit_pct, 6),
                round(highest_high, 4), round(highest_close, 4),
                round(max_unrealized_pnl_pct, 6), trade_id,
            ))
            conn.commit()
        finally:
            conn.close()

    def save_stop_history(self, trade_id: int, session_date, prev_stop: float,
                          new_stop: float, stop_status: str, reason: str,
                          max_profit_pct: float, highest_close: float):
        """Insert a row recording a stop change (effective next session)."""
        with db_cursor() as cur:
            cur.execute("""
                INSERT INTO stop_history
                (trade_id, session_date, effective_from_session, previous_stop, new_stop,
                 stop_status, reason, max_profit_pct, highest_close)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                trade_id, str(session_date), str(session_date),
                round(prev_stop, 4), round(new_stop, 4), stop_status, reason,
                round(max_profit_pct, 6), round(highest_close, 4),
            ))

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

    def close_trade(self, trade_id: int, exit_price_usd: float, exit_date: date, reason: str,
                    avg_volume: float = 0):
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

            eff_exit, cost_per_share = _apply_costs(exit_price_usd, "sell", shares, avg_volume)
            exit_cost_pln = cost_per_share * shares * self.rate

            pnl_usd = (eff_exit - entry_price) * shares
            pnl_pln = pnl_usd * self.rate - exit_cost_pln  # subtract exit commission
            pnl_pct = (eff_exit - entry_price) / entry_price * 100
            r_multiple = pnl_pln / risk_pln if risk_pln > 0 else 0

            entry_dt = date.fromisoformat(entry_date_str)
            holding_days = (exit_date - entry_dt).days

            cur.execute("""
                UPDATE trades SET
                  status='closed', exit_date=?, exit_price=?, exit_reason=?,
                  pnl_pln=?, pnl_pct=?, r_multiple=?, holding_days=?
                WHERE id=?
            """, (
                str(exit_date), round(eff_exit, 4), reason,
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
