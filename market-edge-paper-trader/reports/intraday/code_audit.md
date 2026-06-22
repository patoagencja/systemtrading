# Code Audit

## Anti-Look-Ahead Checks
- Strategies only use `idf[idf.index <= bar_ts]` — verified.
- Daily data sliced to `ddf[ddf.index.date <= session_date]` — verified.
- Pending signals are executed on the *next* bar, not the signal bar.

## Cost Model
- Commission: BASE scenario applied on entry and exit.
- Slippage scales with participation rate.

## Risk Controls
- Daily loss limit, weekly loss limit, drawdown circuit breakers applied.
- Forced close at 15:40 ET with deadline at 15:50 ET.

Generated: 2026-06-22T11:44:56.186556Z