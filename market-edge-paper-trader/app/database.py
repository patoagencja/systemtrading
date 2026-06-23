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
            run_mode TEXT DEFAULT 'backtest',
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
            max_holding_days INTEGER DEFAULT 10,
            run_mode TEXT DEFAULT 'backtest'
        );

        CREATE TABLE IF NOT EXISTS portfolio_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date TEXT NOT NULL,
            run_mode TEXT NOT NULL DEFAULT 'backtest',
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
            total_closed_trades INTEGER DEFAULT 0,
            UNIQUE(snapshot_date, run_mode)
        );

        CREATE TABLE IF NOT EXISTS strategy_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            strategy TEXT NOT NULL,
            run_mode TEXT NOT NULL DEFAULT 'backtest',
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
            last_updated TEXT DEFAULT (datetime('now')),
            UNIQUE(strategy, run_mode)
        );
        """)
        # Migrate existing tables (add run_mode if missing)
        for table in ("trades", "signals"):
            try:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN run_mode TEXT DEFAULT 'backtest'")
            except Exception:
                pass
        try:
            cur.execute("ALTER TABLE portfolio_snapshots ADD COLUMN run_mode TEXT DEFAULT 'backtest'")
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_snap_date_mode ON portfolio_snapshots(snapshot_date, run_mode)")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE strategy_stats ADD COLUMN run_mode TEXT DEFAULT 'backtest'")
        except Exception:
            pass

        # ── Exit logic v2 (SIMPLE_DYNAMIC_EXIT_V1) migrations ───────────────
        _exit_v2_columns = [
            "ALTER TABLE trades ADD COLUMN initial_stop_loss REAL",
            "ALTER TABLE trades ADD COLUMN active_stop_loss REAL",
            "ALTER TABLE trades ADD COLUMN highest_high_since_entry REAL DEFAULT 0",
            "ALTER TABLE trades ADD COLUMN highest_close_since_entry REAL DEFAULT 0",
            "ALTER TABLE trades ADD COLUMN max_profit_pct REAL DEFAULT 0",
            "ALTER TABLE trades ADD COLUMN locked_profit_pct REAL DEFAULT 0",
            "ALTER TABLE trades ADD COLUMN stop_status TEXT DEFAULT 'INITIAL'",
            "ALTER TABLE trades ADD COLUMN exit_logic_version TEXT DEFAULT 'LEGACY_EXIT_LOGIC'",
            "ALTER TABLE trades ADD COLUMN active_stop_effective_date TEXT",
            "ALTER TABLE trades ADD COLUMN max_unrealized_pnl_pln REAL DEFAULT 0",
            "ALTER TABLE trades ADD COLUMN max_unrealized_pnl_pct REAL DEFAULT 0",
        ]
        for stmt in _exit_v2_columns:
            try:
                cur.execute(stmt)
            except Exception:
                pass  # column already exists

        cur.executescript("""
        CREATE TABLE IF NOT EXISTS stop_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL,
            session_date TEXT NOT NULL,
            effective_from_session TEXT NOT NULL,
            previous_stop REAL NOT NULL,
            new_stop REAL NOT NULL,
            stop_status TEXT NOT NULL,
            reason TEXT NOT NULL,
            max_profit_pct REAL DEFAULT 0,
            highest_close REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (trade_id) REFERENCES trades(id)
        );
        CREATE INDEX IF NOT EXISTS idx_stop_history_trade ON stop_history(trade_id);
        """)

        # ── Intraday tables ──────────────────────────────────────────────────
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS intraday_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER,
            ticker TEXT NOT NULL,
            strategy TEXT NOT NULL,
            sector TEXT,
            session_date TEXT,
            signal_timestamp TEXT,
            signal_bar_close REAL,
            score REAL,
            planned_entry REAL,
            stop_price REAL,
            target_price REAL,
            reward_risk REAL,
            market_regime TEXT DEFAULT 'NEUTRAL',
            status TEXT DEFAULT 'PENDING',
            rejection_reason TEXT,
            run_mode TEXT DEFAULT 'live',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_intraday_signal_key
            ON intraday_signals(run_mode, session_date, signal_timestamp, ticker, strategy);

        CREATE TABLE IF NOT EXISTS intraday_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER REFERENCES intraday_signals(id),
            status TEXT DEFAULT 'PENDING',
            planned_execution_timestamp TEXT,
            actual_execution_timestamp TEXT,
            planned_price REAL,
            fill_price REAL,
            slippage_bps REAL,
            spread_bps REAL,
            cancellation_reason TEXT,
            run_mode TEXT DEFAULT 'live',
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS intraday_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER REFERENCES intraday_signals(id),
            ticker TEXT NOT NULL,
            strategy TEXT NOT NULL,
            sector TEXT,
            session_date TEXT,
            entry_timestamp TEXT,
            entry_price REAL,
            exit_timestamp TEXT,
            exit_price REAL,
            stop_price REAL,
            target_price REAL,
            shares INTEGER,
            position_value_pln REAL,
            planned_risk_pln REAL,
            gross_pnl_pln REAL,
            commission_pln REAL,
            spread_cost_pln REAL,
            slippage_cost_pln REAL,
            net_pnl_pln REAL,
            pnl_pct REAL,
            r_multiple REAL,
            holding_minutes REAL,
            exit_reason TEXT,
            market_regime TEXT,
            run_mode TEXT DEFAULT 'live',
            status TEXT DEFAULT 'open',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_intraday_trade_key
            ON intraday_trades(run_mode, session_date, ticker, entry_timestamp)
            WHERE status='open';

        CREATE TABLE IF NOT EXISTS intraday_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            session_date TEXT,
            run_mode TEXT DEFAULT 'live',
            equity_pln REAL,
            cash_pln REAL,
            invested_pln REAL,
            realized_pnl_today REAL,
            unrealized_pnl REAL,
            total_open_risk REAL,
            total_exposure REAL,
            open_positions INTEGER,
            daily_drawdown REAL,
            daily_turnover REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS intraday_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT UNIQUE,
            session_date TEXT,
            bar_timestamp TEXT,
            started_at TEXT,
            completed_at TEXT,
            status TEXT DEFAULT 'running',
            run_mode TEXT DEFAULT 'live',
            scanned_instruments INTEGER DEFAULT 0,
            signals_generated INTEGER DEFAULT 0,
            orders_created INTEGER DEFAULT 0,
            positions_opened INTEGER DEFAULT 0,
            positions_closed INTEGER DEFAULT 0,
            warnings TEXT,
            errors TEXT,
            error_message TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        """)
    print("Database initialized.")


def reset_trading_data(run_mode: str = None):
    """Delete trades/signals/snapshots/stats. If run_mode given, only that mode."""
    with db_cursor() as cur:
        if run_mode:
            cur.execute(
                "DELETE FROM stop_history WHERE trade_id IN "
                "(SELECT id FROM trades WHERE run_mode=?)", (run_mode,)
            )
            cur.execute("DELETE FROM trades WHERE run_mode=?", (run_mode,))
            cur.execute("DELETE FROM signals WHERE run_mode=?", (run_mode,))
            cur.execute("DELETE FROM portfolio_snapshots WHERE run_mode=?", (run_mode,))
            cur.execute("DELETE FROM strategy_stats WHERE run_mode=?", (run_mode,))
        else:
            cur.execute("DELETE FROM stop_history")
            cur.execute("DELETE FROM trades")
            cur.execute("DELETE FROM signals")
            cur.execute("DELETE FROM portfolio_snapshots")
            cur.execute("DELETE FROM strategy_stats")
