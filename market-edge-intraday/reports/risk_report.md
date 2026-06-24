# Risk Report

> **PLACEHOLDER — auto-generated content.** Populated from stored runs by
> `scripts/generate_reports.py`. Values below are templates until a real run.

_Last generated: (not yet run)_

## Configured limits (from `app/config.py`)

| Limit | Setting | Default |
| --- | --- | --- |
| Risk per trade | `RISK_PER_TRADE_PCT` | 0.10% of equity |
| Max position value | `MAX_POSITION_VALUE_PLN` | 30,000 PLN |
| Max open positions | `MAX_OPEN_POSITIONS` | 20 |
| Max gross exposure | `MAX_GROSS_EXPOSURE_PCT` | 50% |
| Max sector exposure | `MAX_SECTOR_EXPOSURE_PCT` | 15% |
| Max positions per sector | `MAX_POSITIONS_PER_SECTOR` | 3 |
| Max total open risk | `MAX_TOTAL_OPEN_RISK_PCT` | 2% |
| Max daily loss | `MAX_DAILY_LOSS_PCT` | 1% |
| Max strategy daily loss | `MAX_STRATEGY_DAILY_LOSS_PCT` | 0.4% |
| Max consecutive losses | `MAX_CONSECUTIVE_LOSSES` | 8 |

## Observed risk usage

| Metric | Value |
| --- | --- |
| Peak gross exposure | _—_ |
| Peak open risk (PLN) | _—_ |
| Worst daily loss | _—_ |
| Max consecutive losses hit | _—_ |
| Kill‑switch activations | _—_ |
| Rejections by reason | _see table_ |

## Rejections by reason

| Rejection reason | Count |
| --- | --- |
| `SCORE_TOO_LOW` | |
| `DAILY_LOSS_LIMIT` | |
| `PORTFOLIO_RISK_LIMIT` | |
| `SECTOR_LIMIT` | |
| `MAX_POSITIONS` | |
| `SPREAD_TOO_WIDE` | |
| `LIQUIDITY_TOO_LOW` | |
| _…_ | |

## Verdict

_Did risk controls behave as designed? Any limit breached? Set after a run._
