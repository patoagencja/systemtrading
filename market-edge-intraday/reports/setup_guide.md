# Setup Guide — market-edge-intraday

Detailed setup for running the system, both with Docker (recommended) and
locally with Python. For the non‑technical walkthrough see the project
`README.md`.

## 0. Prerequisites

- Docker (Desktop on Windows/Mac, Engine on Linux) — recommended path, **or**
- Python 3.11 + a reachable PostgreSQL (local path).
- A free Alpaca account for market data (read‑only).

## 1. Get market-data credentials

1. Sign up at https://alpaca.markets (free).
2. Dashboard → **Paper Trading → API Keys** → Generate.
3. Copy the **API Key ID** and **Secret Key**.

The free `iex` feed is sufficient. No real brokerage account is used.

## 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set at least:

```
MARKET_DATA_PROVIDER=alpaca
ALPACA_API_KEY=...
ALPACA_SECRET_KEY=...
POSTGRES_PASSWORD=choose_something
```

Everything else has sensible defaults (capital, risk limits, session times,
scoring threshold `MIN_SIGNAL_SCORE=75`). All tunables live in `app/config.py`.

## 3. Run with Docker (recommended)

```bash
docker compose up -d
```

Services started:

| Service | Purpose | Port |
| --- | --- | --- |
| `postgres` | database (named volume `pgdata`) | 5432 |
| `worker` | live paper engine (default command) | — |
| `api` | FastAPI (`/health`, data) | 8000 |
| `dashboard` | Streamlit dashboard | 8501 |

Open:
- Dashboard: http://localhost:8501
- API docs: http://localhost:8000/docs

`restart: unless-stopped` means the stack resumes automatically after a reboot.

Tear down (data kept): `docker compose down`. Remove data too:
`docker compose down -v`.

## 4. Run locally (without Docker)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Use a throwaway SQLite DB if you have no Postgres:
export INTRADAY_TEST_DB_URL="sqlite:///./local.db"

python scripts/init_database.py
```

## 5. Common commands

```bash
# Create / ensure schema
python scripts/init_database.py

# Build today's universe (needs provider key)
python scripts/build_universe.py --top 25

# Pre-download historical bars to the parquet cache
python scripts/download_historical_data.py --symbols AAPL,MSFT \
    --start 2026-01-01 --end 2026-03-31 --timeframe 15Min

# Run a backtest and write reports
python scripts/run_backtest.py --start 2026-01-01 --end 2026-03-31 \
    --symbols AAPL,MSFT --cost-scenario BASE

# Walk-forward analysis
python scripts/run_walk_forward.py --start 2025-01-01 --end 2026-03-31 \
    --symbols AAPL,MSFT

# Generate reports from stored trades
python scripts/generate_reports.py --from-db --run-mode BACKTEST

# Live paper loop (PAPER ONLY)
python scripts/run_live_paper.py

# Reset the paper account
python scripts/reset_paper_account.py --yes

# Health check (exit 0 healthy / 1 unhealthy)
python scripts/health_check.py
```

## 6. Verifying the install

```bash
ruff check .
mypy app
INTRADAY_TEST_DB_URL="sqlite:///./test.db" pytest -v --cov=app
python scripts/health_check.py
```

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| "no market-data credentials" | `.env` keys missing | add `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` |
| backtest exits non‑zero with "no data" | no key and empty cache | download data or add a key — the system never fabricates results |
| dashboard shows empty pages | DB empty / no runs yet | run a backtest or the worker first |
| DB unreachable | Postgres not up | `docker compose up -d postgres` or set `INTRADAY_TEST_DB_URL` |

## 8. CI (GitHub Actions)

At the repo root, `.github/workflows/`:
- `mei-intraday-tests.yml` — ruff + mypy (non‑blocking) + pytest on changes.
- `mei-intraday-backtest.yml` — manual backtest (needs `ALPACA_API_KEY` /
  `ALPACA_SECRET_KEY` repo secrets).
- `mei-intraday-docker-build.yml` — builds the image (no registry push).
