# CLAUDE.md — instructions for future Claude sessions

**Always read this file first** before changing anything in
`market-edge-intraday/`. It encodes the non‑negotiable safety and correctness
rules for this project.

---

## What this project is

An intraday **paper‑trading research system** for US equities. It scans a
universe of liquid stocks on 15‑minute bars, generates scored signals from a few
strategies, sizes them through a risk manager, simulates fills with a paper
broker, and records everything in PostgreSQL. A FastAPI service and a Streamlit
dashboard expose the state. Capital is virtual (PLN‑denominated, USD trades
converted via USD/PLN).

## SAFETY RULES (non‑negotiable)

1. **No real transactions, ever.** Never add code that places a real broker
   order or connects a live trading account. Market‑data access is **read‑only**.
2. **Paper only.** All fills are simulated. The word "live" here means
   "live market data", never "live money".
3. **No leverage.** No borrowing, no margin multipliers. Long‑only in V1.
4. **No options / futures / CFD / forex / crypto.** Regular stocks and ETFs only.
5. **Flat by end of day.** Positions are force‑closed before the US close; no
   overnight exposure.

## Correctness rules

6. **No look‑ahead bias.** A signal is produced on the **close of bar T** and may
   only be filled at the **open of bar T+1** (or later). Never use a bar's own
   close — or any future bar — to make or fill a decision on that bar. The
   `SignalCandidate.signal_time` is the close time of bar T; entries happen after.
7. **Backtest and live share the SAME modules.** The strategy, risk, sizing and
   paper‑broker/fill logic used by `app/backtest` must be the exact same code
   used by `app/engine` (live paper). Do **not** fork or duplicate trading logic
   for one path — divergence makes backtests meaningless.
8. **Never fabricate data.** If there is no API key and no cached data, scripts
   must fail clearly (non‑zero exit) rather than invent prices or results.
9. **Two streams never mix.** `RunMode.BACKTEST` and `RunMode.LIVE_PAPER` data
   are kept separate in the DB and in the dashboard.
10. **UTC in storage, ET for session logic.** All timestamps are stored
    timezone‑aware in UTC; session timing uses America/New_York wall clock.

## Anti‑overfitting rules

11. **Do not optimise on out‑of‑sample data.** Tune only on in‑sample windows;
    keep OOS / walk‑forward windows untouched for honest evaluation.
12. **Prefer robustness over peak performance.** Sweep parameters (e.g. score
    thresholds 65/70/75/80/85, cost scenarios LOW/BASE/STRESS) and report
    sensitivity rather than cherry‑picking the best single run.
13. **Report failures.** "This strategy does not work" is a valid, valuable
    result. Never tune until a number looks good.

## Working order (stages)

1. Foundation: config, enums, models, database, domain, indicators. *(done)*
2. Strategies + 0–100 scoring.
3. Risk manager + execution (paper broker, fill model, slippage/costs).
4. Backtester event loop + engine + metrics.
5. Data providers + universe builder.
6. Analytics + alerts + API.
7. Dashboard + operational scripts + Docker + CI + docs. *(this layer)*
8. Tests, integration pass, run a real backtest, generate reports.

Files under `app/` are owned by other workstreams; the ops/dashboard/docs layer
(`dashboard/`, `scripts/`, `Dockerfile`, `docker-compose.yml`, `reports/*.md`,
root docs, `*.github/workflows/mei-intraday-*.yml`) must only call documented
entrypoints and import them **lazily inside functions** so partial builds still
import cleanly.

## Test / quality commands

```bash
pytest -v --cov=app        # run tests with coverage (use INTRADAY_TEST_DB_URL=sqlite:///./test.db)
ruff check .               # lint (line length 100; config in pyproject.toml)
mypy app                   # type check (non-blocking in CI but keep it clean)
```

Run the dashboard locally:

```bash
streamlit run dashboard/streamlit_app.py
```

## Code standards

- Python 3.11, `from __future__ import annotations`, type hints where natural.
- Module + public‑function docstrings. Keep functions small and pure where
  possible (especially strategies, risk, fill model — they must be unit‑testable
  without a database).
- Ruff‑clean (line length 100). isort with `app` as first‑party.
- Configuration lives in `app/config.py` (pydantic‑settings). **Do not** hardcode
  tunables; read from `settings` so backtest and live agree on the same numbers.
- Secrets come from environment / `.env`. Never commit secrets or reference real
  keys in code or workflows (use GitHub secrets).

## Documentation requirements

- Keep `README.md` non‑technical and accurate (it has 17 required sections).
- Keep this `CLAUDE.md` current when architecture or rules change.
- Reports under `reports/*.md` are templates until a real backtest fills them via
  `scripts/generate_reports.py`; mark generated content clearly.

**Reminder: always read CLAUDE.md first.**
