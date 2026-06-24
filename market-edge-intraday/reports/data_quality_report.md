# Data Quality Report

> **PLACEHOLDER — auto-generated content.** This file is a template. It will be
> filled in after real data is downloaded and a run is performed (via
> `scripts/download_historical_data.py` and `scripts/generate_reports.py`).
> Until then the values below are illustrative blanks, not real measurements.

_Last generated: (not yet run)_

## Coverage

| Field | Value |
| --- | --- |
| Provider | _e.g. alpaca (iex feed)_ |
| Date range | _start → end_ |
| Bar interval | 15Min |
| Symbols requested | _n_ |
| Symbols with data | _n_ |
| Total bars | _n_ |

## Completeness checks

- **Missing bars** (gaps within session hours): _to be measured_
- **Symbols below `MIN_HISTORY_DAYS`**: _to be measured_
- **Zero/negative prices or volumes**: _to be measured_
- **Duplicate timestamps**: _to be measured_
- **Out-of-session bars** (outside 09:30–16:00 ET): _to be measured_

## Liquidity snapshot (universe filters)

| Filter | Threshold | # passing |
| --- | --- | --- |
| Min price | `MIN_PRICE_USD` | _n_ |
| Max price | `MAX_PRICE_USD` | _n_ |
| Min avg daily volume | `MIN_AVG_DAILY_VOLUME` | _n_ |
| Min avg dollar volume | `MIN_AVG_DOLLAR_VOLUME_USD` | _n_ |

## Known limitations

- The free IEX feed is a partial tape; volumes/spreads can differ from the full
  consolidated tape. Treat results as research, not production reality.
- No survivorship‑bias control unless the universe is reconstructed per date.

## Verdict

_PASS / WARN / FAIL — set after a real data pull._
