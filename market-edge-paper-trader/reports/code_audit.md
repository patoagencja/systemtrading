# Code Audit — Market Edge Paper Trader

Audit date: 2026-06-22

## 1. Look-ahead bias

**Status: MITIGATED**

- Backtest slices each ticker's DataFrame to `df[df.index.date <= sim_date]` before running strategies.
- Indicators are computed on the sliced frame, so SMA200 / RSI / ATR are always calculated from data available through `sim_date`.
- The 220-bar minimum history requirement (`MIN_HISTORY_BARS`) prevents signals on days with insufficient history.

**Residual risk:** `fetch_ohlcv(period="2y")` is called once upfront. If today's bar is the last row and indicators use adjusted prices that are retroactively corrected by yfinance, a very small look-ahead bias could enter. Impact is considered negligible for swing-trade timeframes.

## 2. Signal T → entry T+1 open

**Status: CORRECTLY IMPLEMENTED**

- `run_scanner` / `run_backtest` Phase 3 generates signals and stores them as `acted=False` (pending).
- Phase 1 on the *next* simulation day fetches `open` price for `sim_date` and fills at that open.
- The `dc_replace(sig, entry_price=actual_entry)` call ensures the fill price (not the close price at signal time) is used for P&L, R-multiple and sizing.

## 3. Position sizing from actual fill price

**Status: CORRECT**

- `risk_mgr.calc_position_size(actual_entry, sig.stop_loss)` is called with the actual open fill price, not the signal-day close.
- ATR-based stop loss is computed at signal time (prior day close), which is correct — the stop level shouldn't change based on where we actually filled.

## 4. Double-update of positions on the same day

**Status: NO ISSUE**

- Phase 1 enters new positions, Phase 2 updates/closes existing ones.  Positions entered in Phase 1 are added to `open_trades`; Phase 2 iterates `still_open` which does not include trades just entered (they won't have today's close bar in slice up to today — or if they do, entry_date == sim_date so holding_days == 0 and exit conditions won't trigger).

## 5. GitHub Actions concurrency

**Status: SAFE**

- Both `daily-scan.yml` and `backtest.yml` use `concurrency: group: paper-trader-db, cancel-in-progress: false`.
- This serialises runs and prevents simultaneous writes to `database.sqlite`.

## 6. Backtest / live data separation

**Status: CORRECT**

- Every DB table (`trades`, `signals`, `portfolio_snapshots`, `strategy_stats`) has a `run_mode` column.
- All queries filter by `run_mode=?` parameter.
- `reset_trading_data(run_mode='backtest')` clears only backtest rows before each run.
- Live data is never touched by the backtest script.

## 7. Duplicate snapshots

**Status: PROTECTED**

- `portfolio_snapshots` has `UNIQUE(snapshot_date, run_mode)`.
- Inserts use `INSERT OR REPLACE`, so re-running on the same day is idempotent.
- Backtest deletes all previous backtest snapshots before starting, so there's no accumulation across runs.

## 8. Idempotency

**Status: PARTIAL**

- Backtest: fully idempotent (clears data then re-runs).
- Live scan: if run twice on the same day, `INSERT OR REPLACE` on snapshots is safe, but new signals may be duplicated in the `signals` table (no unique constraint on `ticker+signal_date+strategy+run_mode`). Impact: minor — duplicate signals would be entered at next-day open, which the scanner handles via `open_tickers` check.

## 9. Recommendations

| Priority | Item |
|---|---|
| Medium | Add `UNIQUE(ticker, signal_date, strategy, run_mode)` constraint to `signals` table to prevent duplicate signal inserts on re-runs. |
| Low | Add sector column to `trades` table for sector-level exposure reporting. |
| Low | Store benchmark (SPY) equity curve in DB so dashboard doesn't need live network fetch. |
| Low | Walk-forward validation: split backtest window into in-sample / out-of-sample segments. |
