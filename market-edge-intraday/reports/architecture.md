# Architecture — market-edge-intraday

A map of the system: what each module does and how data flows through it. This
document is hand‑maintained (not auto‑generated).

## High-level flow

```
data provider -> universe -> strategies (scored signals) -> risk manager
   -> execution / paper broker -> backtest OR live engine -> PostgreSQL
   -> FastAPI + Streamlit dashboard
```

The **same** strategy / risk / broker code runs under both the backtest and the
live‑paper engine. What you test is what you run.

## Module map (`app/`)

| Module | Responsibility |
| --- | --- |
| `app/config.py` | Single source of all tunables (pydantic‑settings, from `.env`). Backtest and live read the same numbers. |
| `app/enums.py` | Canonical vocabulary: run modes, statuses, rejection/exit reasons, cost table. |
| `app/models.py` | SQLAlchemy ORM: instruments, universe_snapshots, trading_sessions, signals, orders, trades, positions, portfolio_snapshots, system_events, heartbeats. UTC timestamps. |
| `app/database.py` | Engine/session management, `session_scope`, `init_db`, `reset_db`. Postgres in prod, SQLite for tests via `INTRADAY_TEST_DB_URL`. |
| `app/domain.py` | In‑memory dataclasses (Bar, SignalCandidate, Fill, PaperPosition, ClosedTrade, …) decoupled from the DB for unit testing. |
| `app/logging_config.py` | `configure_logging()` / `get_logger()`. |
| `app/indicators/` | Technical indicators (VWAP, ATR, relative strength, etc.). |
| `app/data/` | Market‑data providers (`get_provider`), historical bars, bar cache (parquet). Read‑only. |
| `app/universe/` | Daily universe builder (liquidity / price / dollar‑volume filters). |
| `app/strategies/` | Strategy implementations + 0–100 signal scoring. |
| `app/risk/` | Position sizing and portfolio risk limits (daily loss, exposure, sector caps, consecutive losses, kill switch). |
| `app/execution/` | Paper broker, fill model, slippage and cost application. |
| `app/engine/` | Live paper engine (APScheduler loop) — `run_live_paper()`. |
| `app/backtest/` | Event‑loop backtester (`run_backtest`, `BacktestConfig`), walk‑forward, report generation. |
| `app/analytics/` | Metrics computation (`compute_metrics`). |
| `app/alerts/` | Optional Telegram / Slack notifications. |
| `app/api/` | FastAPI app (`app.api.main:app`) exposing `/health` and data endpoints. |

## Operations layer (this workstream)

| Path | Responsibility |
| --- | --- |
| `dashboard/streamlit_app.py` | Multi‑view dark dashboard reading the DB directly; RUN_MODE filter. |
| `dashboard/components/charts.py` | Reusable Plotly figures (equity, drawdown, heatmap, …). |
| `dashboard/components/tables.py` | DataFrame styling helpers. |
| `scripts/*.py` | CLI entrypoints (init DB, build universe, download data, backtest, walk‑forward, live paper, reset, reports, health check). |
| `Dockerfile`, `docker-compose.yml` | Container image + local stack (postgres, worker, api, dashboard). |
| `.github/workflows/mei-intraday-*.yml` | CI: tests, on‑demand backtest, docker build. |

## Data stores

- **PostgreSQL** — system of record (all tables in `app/models.py`), UTC.
- **Parquet cache** (`PARQUET_DIR`) — historical bars for fast backtests.
- **DuckDB** (`DUCKDB_PATH`) — optional fast local analytics.

## Time handling

- Storage: timezone‑aware **UTC** everywhere.
- Session logic: **America/New_York** wall clock (open 09:30, close 16:00,
  last new entry 15:15, force‑close window 15:45–15:50 ET).

## Key invariants

- No look‑ahead: signal on close of bar T, fill at open of T+1.
- Long‑only, flat by end of day, no leverage, no real orders.
- BACKTEST and LIVE_PAPER streams never mixed.
