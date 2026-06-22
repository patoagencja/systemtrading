"""Intraday paper broker: simulate fills, costs, and P&L."""
import datetime
import logging
from typing import Optional

from app.intraday.config import (
    COST_SCENARIOS,
    INTRADAY_PLN_USD_RATE,
    MIN_REWARD_RISK,
)
from app.database import db_cursor, get_connection

log = logging.getLogger(__name__)

_BPS = 10_000  # basis points divisor


class IntradayPaperBroker:
    """Simulate intraday order execution with realistic costs."""

    def __init__(
        self,
        pln_usd_rate: float = INTRADAY_PLN_USD_RATE,
        run_mode: str = "live",
        cost_scenario: str = "BASE",
    ):
        self.rate = pln_usd_rate
        self.run_mode = run_mode
        scenario = COST_SCENARIOS.get(cost_scenario, COST_SCENARIOS["BASE"])
        self.commission_bps = scenario["commission_bps"]
        self.slippage_bps   = scenario["slippage_bps"]

    # ── Internals ─────────────────────────────────────────────────────────────

    def _compute_fill_price(
        self,
        price: float,
        side: str,
        shares: int,
        avg_dollar_volume: float = 1e8,
    ) -> tuple[float, float]:
        """Apply slippage. Returns (fill_price, actual_slippage_bps)."""
        # Scale slippage by participation rate
        trade_val = price * shares
        part_rate = trade_val / avg_dollar_volume if avg_dollar_volume > 0 else 0
        mult = 1.0
        if part_rate >= 0.05:
            mult = 6.0
        elif part_rate >= 0.01:
            mult = 3.5
        elif part_rate >= 0.001:
            mult = 2.0

        actual_slip_bps = self.slippage_bps * mult
        slip = price * actual_slip_bps / _BPS

        if side == "buy":
            fill = price + slip
        else:
            fill = price - slip

        return round(fill, 4), round(actual_slip_bps, 2)

    def _compute_costs(
        self,
        fill_price: float,
        shares: int,
    ) -> tuple[float, float]:
        """Returns (commission_usd, spread_usd_est)."""
        comm_usd = fill_price * shares * self.commission_bps / _BPS
        spread_usd = fill_price * shares * 0.5 / _BPS  # ~0.5 bps estimated spread
        return round(comm_usd, 4), round(spread_usd, 4)

    # ── Public methods ────────────────────────────────────────────────────────

    def fill_order(
        self,
        signal_id: int,
        ticker: str,
        strategy: str,
        sector: str,
        session_date,
        entry_timestamp,
        planned_entry: float,
        bar_open: float,
        stop_price: float,
        target_price: float,
        shares: int,
        position_value_pln: float,
        planned_risk_pln: float,
        market_regime: str,
        score: float,
        atr_usd: float = 0.0,
    ) -> dict:
        """Fill order at bar_open + slippage. Cancels if gap too large or R:R fails."""
        # Cancel if gap from planned entry > 0.5 * ATR
        gap = abs(bar_open - planned_entry)
        if atr_usd > 0 and gap > 0.5 * atr_usd:
            self.cancel_order(signal_id, f"gap {gap:.3f} > 0.5*ATR {0.5*atr_usd:.3f}")
            return {"cancelled": True, "reason": f"gap too large: {gap:.4f}"}

        fill_price, slip_bps = self._compute_fill_price(bar_open, "buy", shares)
        comm_usd, spread_usd = self._compute_costs(fill_price, shares)

        # Recalculate R:R after fill
        if fill_price >= stop_price:
            self.cancel_order(signal_id, "stop >= fill_price after slippage")
            return {"cancelled": True, "reason": "stop >= fill after slippage"}

        rr_after_fill = (target_price - fill_price) / (fill_price - stop_price)
        if rr_after_fill < MIN_REWARD_RISK:
            self.cancel_order(signal_id, f"R:R {rr_after_fill:.2f} < {MIN_REWARD_RISK}")
            return {"cancelled": True, "reason": f"R:R too low after fill: {rr_after_fill:.2f}"}

        actual_pos_pln = fill_price * shares * self.rate
        entry_comm_pln = comm_usd * self.rate

        ts_str = entry_timestamp.isoformat() if hasattr(entry_timestamp, "isoformat") else str(entry_timestamp)
        sd_str = session_date.isoformat() if hasattr(session_date, "isoformat") else str(session_date)

        with db_cursor() as cur:
            cur.execute("""
                INSERT INTO intraday_trades
                (signal_id, ticker, strategy, sector, session_date, entry_timestamp,
                 entry_price, stop_price, target_price, shares, position_value_pln,
                 planned_risk_pln, market_regime, run_mode, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open',
                        datetime('now'), datetime('now'))
            """, (
                signal_id, ticker, strategy, sector, sd_str, ts_str,
                round(fill_price, 4), round(stop_price, 4), round(target_price, 4),
                shares, round(actual_pos_pln, 2), round(planned_risk_pln, 2),
                market_regime, self.run_mode,
            ))
            trade_id = cur.lastrowid

        # Update signal status
        with db_cursor() as cur:
            cur.execute(
                "UPDATE intraday_signals SET status='FILLED' WHERE id=?",
                (signal_id,),
            )

        return {
            "cancelled": False,
            "trade_id": trade_id,
            "actual_fill_usd": fill_price,
            "slippage_bps": slip_bps,
            "spread_bps": 0.5,
            "position_value_pln": round(actual_pos_pln, 2),
            "reason": "",
        }

    def close_trade(
        self,
        trade_id: int,
        exit_price_usd: float,
        exit_timestamp,
        exit_reason: str,
        is_forced_close: bool = False,
    ) -> dict:
        """Close trade, compute P&L components."""
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """SELECT entry_price, shares, stop_price, target_price,
                          planned_risk_pln, position_value_pln, entry_timestamp
                   FROM intraday_trades WHERE id=? AND status='open'""",
                (trade_id,),
            )
            row = cur.fetchone()
            if not row:
                return {"error": f"trade {trade_id} not found or not open"}

            entry_price, shares, stop_price, target_price, planned_risk_pln, pos_val_pln, entry_ts_str = row

            fill_exit, slip_bps = self._compute_fill_price(exit_price_usd, "sell", shares)
            comm_entry_usd, spread_entry_usd = self._compute_costs(entry_price, shares)
            comm_exit_usd, spread_exit_usd = self._compute_costs(fill_exit, shares)

            gross_pnl_usd = (fill_exit - entry_price) * shares
            comm_usd = comm_entry_usd + comm_exit_usd
            spread_usd = spread_entry_usd + spread_exit_usd
            slip_cost_usd = abs(fill_exit - exit_price_usd) * shares

            gross_pnl_pln  = gross_pnl_usd * self.rate
            comm_pln       = comm_usd * self.rate
            spread_pln     = spread_usd * self.rate
            slip_pln       = slip_cost_usd * self.rate
            net_pnl_pln    = gross_pnl_pln - comm_pln - spread_pln

            pnl_pct = (fill_exit - entry_price) / entry_price * 100
            r_multiple = net_pnl_pln / planned_risk_pln if planned_risk_pln > 0 else 0

            # Holding time
            holding_minutes = 0.0
            try:
                entry_dt = datetime.datetime.fromisoformat(entry_ts_str)
                exit_dt = exit_timestamp if isinstance(exit_timestamp, datetime.datetime) else datetime.datetime.fromisoformat(str(exit_timestamp))
                holding_minutes = (exit_dt - entry_dt).total_seconds() / 60.0
            except Exception:
                pass

            ts_str = exit_timestamp.isoformat() if hasattr(exit_timestamp, "isoformat") else str(exit_timestamp)

            cur.execute("""
                UPDATE intraday_trades SET
                    status='closed',
                    exit_timestamp=?,
                    exit_price=?,
                    exit_reason=?,
                    gross_pnl_pln=?,
                    commission_pln=?,
                    spread_cost_pln=?,
                    slippage_cost_pln=?,
                    net_pnl_pln=?,
                    pnl_pct=?,
                    r_multiple=?,
                    holding_minutes=?,
                    updated_at=datetime('now')
                WHERE id=?
            """, (
                ts_str, round(fill_exit, 4), exit_reason,
                round(gross_pnl_pln, 2), round(comm_pln, 2),
                round(spread_pln, 2), round(slip_pln, 2),
                round(net_pnl_pln, 2), round(pnl_pct, 4),
                round(r_multiple, 3), round(holding_minutes, 1),
                trade_id,
            ))
            conn.commit()

            return {
                "gross_pnl_pln": round(gross_pnl_pln, 2),
                "commission_pln": round(comm_pln, 2),
                "spread_cost_pln": round(spread_pln, 2),
                "slippage_cost_pln": round(slip_pln, 2),
                "net_pnl_pln": round(net_pnl_pln, 2),
                "pnl_pct": round(pnl_pct, 4),
                "r_multiple": round(r_multiple, 3),
                "holding_minutes": round(holding_minutes, 1),
            }
        finally:
            conn.close()

    def update_open_pnl(
        self,
        trade_id: int,
        current_price_usd: float,
        holding_minutes: int,
    ):
        """Update unrealized P&L for open trade."""
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                "SELECT entry_price, shares, planned_risk_pln FROM intraday_trades WHERE id=?",
                (trade_id,),
            )
            row = cur.fetchone()
            if not row:
                return
            entry_price, shares, risk_pln = row
            pnl_usd = (current_price_usd - entry_price) * shares
            pnl_pln = pnl_usd * self.rate
            pnl_pct = (current_price_usd - entry_price) / entry_price * 100 if entry_price else 0
            r_mult  = pnl_pln / risk_pln if risk_pln > 0 else 0

            cur.execute("""
                UPDATE intraday_trades SET
                    gross_pnl_pln=?, net_pnl_pln=?, pnl_pct=?, r_multiple=?,
                    holding_minutes=?, updated_at=datetime('now')
                WHERE id=? AND status='open'
            """, (
                round(pnl_pln, 2), round(pnl_pln, 2),
                round(pnl_pct, 4), round(r_mult, 3),
                holding_minutes, trade_id,
            ))
            conn.commit()
        finally:
            conn.close()

    def cancel_order(self, signal_id: int, reason: str):
        """Mark a signal/order as cancelled."""
        with db_cursor() as cur:
            cur.execute(
                "UPDATE intraday_signals SET status='CANCELLED', rejection_reason=? WHERE id=?",
                (reason, signal_id),
            )
        log.debug(f"Signal {signal_id} cancelled: {reason}")
