import sqlite3
from contextlib import contextmanager
from app.config import DB_PATH


def get_connection():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def db_cursor():
    conn = get_connection()
    try:
        cur = conn.cursor()
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with db_cursor() as cur:
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS watchlist (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL UNIQUE,
            sector TEXT,
            is_etf INTEGER DEFAULT 0,
            sector_etf TEXT,
            active INTEGER DEFAULT 1,
            added_date TEXT DEFAULT (date('now'))
        );

        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            strategy TEXT NOT NULL,
            signal_date TEXT NOT NULL,
            score REAL NOT NULL,
            entry_price REAL,
            stop_loss REAL,
            take_profit REAL,
            atr REAL,
            rsi REAL,
            volume_ratio REAL,
            reason TEXT,
            acted INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker TEXT NOT NULL,
            strategy TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            entry_date TEXT NOT NULL,
            entry_price REAL NOT NULL,
            stop_loss REAL NOT NULL,
            take_profit REAL NOT NULL,
            exit_date TEXT,
            exit_price REAL,
            shares REAL NOT NULL,
            position_value_pln REAL NOT NULL,
            risk_pln REAL NOT NULL,
            score REAL NOT NULL,
            entry_reason TEXT,
            exit_reason TEXT,
            pnl_pln REAL DEFAULT 0,
            pnl_pct REAL DEFAULT 0,
            r_multiple REAL DEFAULT 0,
            holding_days INTEGER DEFAULT 0,
            max_holding_days INTEGER DEFAULT 10
        );

        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL UNIQUE,
            total_value_pln REAL NOT NULL,
            cash_pln REAL NOT NULL,
            invested_pln REAL NOT NULL,
            open_positions INTEGER NOT NULL,
            daily_pnl_pln REAL DEFAULT 0,
            total_pnl_pln REAL DEFAULT 0,
            total_return_pct REAL DEFAULT 0,
            max_drawdown_pct REAL DEFAULT 0,
            win_trades INTEGER DEFAULT 0,
            loss_trades INTEGER DEFAULT 0,
            total_closed_trades INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS strategy_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL UNIQUE,
            total_trades INTEGER DEFAULT 0,
            win_trades INTEGER DEFAULT 0,
            loss_trades INTEGER DEFAULT 0,
            total_pnl_pln REAL DEFAULT 0,
            avg_win_pln REAL DEFAULT 0,
            avg_loss_pln REAL DEFAULT 0,
            profit_factor REAL DEFAULT 0,
            win_rate REAL DEFAULT 0,
            avg_r_multiple REAL DEFAULT 0,
            avg_holding_days REAL DEFAULT 0,
            last_updated TEXT DEFAULT (datetime('now'))
        );
        """)
    print("Database initialized.")


def reset_trading_data():
    """Delete all trades, signals, snapshots and stats (keeps watchlist)."""
    with db_cursor() as cur:
        cur.execute("DELETE FROM trades")
        cur.execute("DELETE FROM signals")
        cur.execute("DELETE FROM portfolio_snapshots")
        cur.execute("DELETE FROM strategy_stats")
