# System Health Report

> **PLACEHOLDER — auto-generated content.** A point‑in‑time snapshot is produced
> by `scripts/health_check.py` and the dashboard **System Health** view. Values
> below are a template until a real check runs.

_Last generated: (not yet run)_

## Component status

| Component | Status | Detail |
| --- | --- | --- |
| Database | _OK / FAIL_ | connectivity via `session_scope` |
| Data provider | _OK / WARN_ | credentials configured? |
| Worker heartbeat | _OK / WARN_ | last heartbeat age |
| Data freshness | _OK / WARN_ | newest snapshot age vs `MAX_DATA_STALENESS_SECONDS` |

## Recent activity

| Metric | Value |
| --- | --- |
| Last successful run | _—_ |
| Errors (last 24h) | _—_ |
| Warnings (last 24h) | _—_ |
| Open positions | _—_ |

## How to refresh

```bash
python scripts/health_check.py          # exit 0 healthy / 1 unhealthy
python scripts/health_check.py --strict # warnings also fail
```

In Docker the `worker` and `api` services run this (or `/health`) as their
container healthcheck.
