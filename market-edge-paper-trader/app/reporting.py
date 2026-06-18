from app.config import INITIAL_CAPITAL_PLN
from app.database import get_connection


def print_terminal_report(stats: dict):
    print(f"\n{'='*60}")
    print(f"  SCAN REPORT — {stats['date']}")
    print(f"{'='*60}")
    print(f"  Scanned tickers    : {stats['scanned']}")
    print(f"  Tickers with data  : {stats['tickers_with_data']}")
    print(f"  Signals found      : {stats['signals_found']}")
    print(f"  New positions      : {stats['new_positions']}")
    print(f"  Closed positions   : {stats['closed_positions']}")
    print(f"{'─'*60}")
    print(f"  Portfolio value    : {stats['portfolio_value']:>15,.2f} PLN")
    print(f"  Cash               : {stats['cash']:>15,.2f} PLN")
    print(f"  Open positions     : {stats['open_positions']}")
    print(f"{'─'*60}")
    daily_sign = "+" if stats['daily_pnl'] >= 0 else ""
    total_sign = "+" if stats['total_pnl'] >= 0 else ""
    print(f"  Daily P&L          : {daily_sign}{stats['daily_pnl']:>14,.2f} PLN")
    print(f"  Total P&L          : {total_sign}{stats['total_pnl']:>14,.2f} PLN")
    print(f"  Total return       : {total_sign}{stats['total_return_pct']:>13.2f} %")

    # Max drawdown from DB
    dd = _get_max_drawdown()
    print(f"  Max drawdown       : {dd:>14.2f} %")

    top = stats.get("top_signals", [])
    if top:
        print(f"{'─'*60}")
        print(f"  TOP {len(top)} SIGNALS:")
        for sig in top:
            print(f"    {sig.ticker:8s} {sig.strategy:25s} score={sig.score:.0f} "
                  f"entry={sig.entry_price:.2f}")
    print(f"{'='*60}\n")


def _get_max_drawdown() -> float:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT max_drawdown_pct FROM portfolio_snapshots ORDER BY snapshot_date DESC LIMIT 1")
        row = cur.fetchone()
        return row[0] if row else 0.0
    finally:
        conn.close()


def get_performance_summary() -> dict:
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN pnl_pln > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl_pln <= 0 THEN 1 ELSE 0 END) as losses,
                SUM(pnl_pln) as total_pnl,
                AVG(CASE WHEN pnl_pln > 0 THEN pnl_pln END) as avg_win,
                AVG(CASE WHEN pnl_pln <= 0 THEN pnl_pln END) as avg_loss,
                AVG(r_multiple) as avg_r,
                AVG(holding_days) as avg_hold
            FROM trades WHERE status='closed'
        """)
        row = cur.fetchone()
        if not row or row[0] == 0:
            return {}

        total, wins, losses, total_pnl, avg_win, avg_loss, avg_r, avg_hold = row
        gross_profit = avg_win * wins if avg_win else 0
        gross_loss = abs(avg_loss * losses) if avg_loss else 0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0

        return {
            "total_trades": total,
            "wins": wins or 0,
            "losses": losses or 0,
            "win_rate": (wins / total * 100) if total > 0 else 0,
            "total_pnl": total_pnl or 0,
            "avg_win": avg_win or 0,
            "avg_loss": avg_loss or 0,
            "profit_factor": profit_factor,
            "avg_r": avg_r or 0,
            "avg_hold": avg_hold or 0,
        }
    finally:
        conn.close()
