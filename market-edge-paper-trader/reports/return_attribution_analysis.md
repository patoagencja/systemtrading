# Return Attribution Analysis
## Swing Trading Backtest 2020-01-01 to 2026-06-22

**Generated:** 2026-06-23 11:12

---

## Executive Summary

The backtest achieved **34.0% total return** (~**4.62% CAGR**)
on 1,000,000 PLN initial capital over 6.5 years, ending at 1,339,665 PLN.

**Root causes of underperformance (in order of impact):**

1. **Low average exposure (35%)** — cash drag alone reduces potential returns by ~79pp
2. **Small position sizes (3.6% per trade)** — each winning trade contributes only ~0.16% to portfolio
3. **Trade frequency too low** (467 trades/year) — insufficient compounding opportunities
4. **Profit capture below potential** — trades reach 3.9% unrealized but realize only 54.6%
5. **High concentration of P&L in few trades** — top 10 trades = 17% of all P&L

---

## 1. Portfolio Overview

| Metric | Value |
|--------|-------|
| Initial Capital | 1,000,000 PLN |
| Final Value | 1,339,665 PLN |
| Total P&L (closed trades) | 393,021 PLN |
| Total Return | 33.97% |
| CAGR | 4.62% |
| Max Drawdown | -8.4% (on 2025-05-12) |
| Total Backtest Period | 6.47 years |
| Total Closed Trades | 3016 |
| Open Trades at End | 9 |

---

## 2. Exposure & Cash Drag

| Metric | Value |
|--------|-------|
| Mean Exposure | 34.8% |
| Median Exposure | 36.0% |
| Time in Market | 96.2% of days |
| Mean Open Positions | 11.6 |
| Max Open Positions | 26 |
| Mean Cash Level | 766,668 PLN (65% of capital) |
| Avg Invested Capital | 413,242 PLN |

### Exposure Distribution
| Exposure Band | % of Days |
|--------------|-----------|
| 0–10% | 12.9% |
| 10–25% | 20.0% |
| 25–50% | 42.3% |
| 50–80% | 24.9% |
| 80%+ | 0.0% |

### Cash Drag Calculation
- **Actual return:** 34.0%
- **P&L / initial capital:** 39.3%
- **Mean exposure fraction:** 34.8%
- **Hypothetical fully-invested return:** 113.0%
- **Cash drag estimate:** ~79 percentage points
- **Return on deployed capital only:** 95.1%

The system kept an average of **65%** of capital in cash.
If that cash had been deployed with the same edge as the invested portion,
returns would be ~79pp higher. Cash drag is the #1 performance killer.

---

## 3. Trade Frequency

| Year | Trades |
|------|--------|
| 2020 | 485 |
| 2021 | 622 |
| 2022 | 195 |
| 2023 | 499 |
| 2024 | 556 |
| 2025 | 433 |
| 2026 | 235 |

| **Average/year** | **467** |
| **Average/month** | **38.9** |

At 467 trades/year and 3.6% position size with 53% win rate,
the system generates approximately **0.019% per trade** in portfolio return.
This requires 618 trades to double the portfolio — which at current frequency
would take **1 years** of pure wins (ignoring compounding).

---

## 4. Win/Loss Statistics

| Metric | Value |
|--------|-------|
| Win Rate | 53.1% |
| Total Winners | 1600 |
| Total Losers | 1416 |
| Average Winner | 1,551 PLN |
| Average Loser | -1,475 PLN |
| Win/Loss Ratio | 1.05x |
| Profit Factor | 1.19 |
| Expectancy | 130 PLN/trade |

---

## 5. Strategy Contribution

### ETF_RELATIVE_STRENGTH
- Trades: 1551 | Win Rate: 54.4% | Profit Factor: 1.27
- Total P&L: 288,025 PLN (73.3% of total)
- Expectancy: 186 PLN/trade | Avg Position: 35,563 PLN
- Avg Winner: 1,629 | Avg Loser: -1,537

### MEAN_REVERSION_UPTREND
- Trades: 121 | Win Rate: 54.5% | Profit Factor: 1.50
- Total P&L: 41,541 PLN (10.6% of total)
- Expectancy: 343 PLN/trade | Avg Position: 34,450 PLN
- Avg Winner: 1,876 | Avg Loser: -1,496

### MOMENTUM_BREAKOUT
- Trades: 91 | Win Rate: 58.2% | Profit Factor: 1.93
- Total P&L: 39,968 PLN (10.2% of total)
- Expectancy: 439 PLN/trade | Avg Position: 35,492 PLN
- Avg Winner: 1,563 | Avg Loser: -1,129

### PULLBACK_TREND
- Trades: 1253 | Win Rate: 50.8% | Profit Factor: 1.03
- Total P&L: 23,488 PLN (6.0% of total)
- Expectancy: 19 PLN/trade | Avg Position: 35,578 PLN
- Avg Winner: 1,413 | Avg Loser: -1,423

---

## 6. Top/Bottom Trade Impact

| Metric | Value |
|--------|-------|
| Total P&L | 393,021 PLN (34.0%) |
| Return excl. top 5 trades | 356,956 PLN (35.7%) |
| Return excl. top 10 trades | 327,761 PLN (32.8%) |
| Top 5 trades' share | 9.2% of total P&L (36,065 PLN) |
| Top 10 trades' share | 16.6% of total P&L (65,260 PLN) |
| Bottom 5 trades' P&L | -40,599 PLN |
| Bottom 10 trades' P&L | -64,507 PLN |

**Implication:** P&L is highly concentrated. If top 10 trades are missed, return
drops from 34.0% to 32.8%. The system is fragile to missing key setups.

---

## 7. Cost Analysis

| Metric | Value |
|--------|-------|
| Total Commission (estimated) | 214,271 PLN |
| Total Slippage (estimated) | 107,136 PLN |
| Total Costs | 321,407 PLN |
| Return Before Costs | 71.4% |
| Return After Costs | 34.0% |
| Cost Drag | 37.48pp |
| Avg Cost per Trade | 107 PLN |
| Costs as % Gross Profit | 13.0% |

Note: Commission and slippage are estimated (no explicit columns in trades table).
Estimates: commission = 0.1% × position × 2; slippage = 0.05% × position × 2.

---

## 8. Score Analysis

| Score Range | Count | Win Rate | Avg P&L | Profit Factor |
|------------|-------|----------|---------|---------------|
| 75-79 | 1021 | 51.7% | 113 | 1.17 |
| 80-84 | 1004 | 55.0% | 191 | 1.28 |
| 85+ | 991 | 52.5% | 87 | 1.12 |

All backtest trades have scores ≥ 75. No signals table data available for rejected signals.
The score range is **75–100** (mean: 82.6).

### Score Threshold Simulation
- **Score ≥ 65:** 3016 trades | Return: 39.3% | Win Rate: 53.1%
- **Score ≥ 70:** 3016 trades | Return: 39.3% | Win Rate: 53.1%
- **Score ≥ 75:** 3016 trades | Return: 39.3% | Win Rate: 53.1%
- **Score ≥ 80:** 1995 trades | Return: 27.8% | Win Rate: 53.7%
- **Score ≥ 85:** 991 trades | Return: 8.6% | Win Rate: 52.5%


---

## 9. Exit Logic / Profit Capture

### Max Profit Capture Statistics
- Trades reaching +4% unrealized: **1365** (45%)
- Trades reaching +6% unrealized: **769** (25%)
- Trades reaching +8% unrealized: **347** (12%)
- Trades reaching +10% unrealized: **0** (0%)
- Trades that hit +8% but closed below +4%: **102** (gave back gains)

### Exit Logic Summary

**FIXED_TP_DYNAMIC_STOP** (3016 trades):
- Win Rate: 53.1% | Profit Factor: 1.19 | Avg P&L: 130 PLN
- Avg Holding: 9.1 days
- Avg Max Profit: 3.87% | Avg Realized: 0.55% | **Capture Rate: 14%**

---

## 10. Position Size Impact Math

| Scenario | Value |
|----------|-------|
| Avg Position Size | 3.6% of capital |
| Avg Winner Gain | 4.55% |
| Portfolio contribution per winning trade | 0.162% |
| Net contribution per trade (all trades) | 0.019% |
| Trades needed for +10% portfolio (pure wins) | 61 |
| Trades needed for +50% portfolio (pure wins) | 309 |
| Trades needed for +100% portfolio (pure wins) | 618 |
| At 467 trades/year: years for +10% | 0.1y |
| At 467 trades/year: years for +100% | 1.3y |

**Key Insight:** With 3% position size and 10% win, each winning trade contributes 0.3%
to the portfolio. Needing 33 wins for +10% portfolio at ~467 trades/year
and 53% win rate means ~1 months for +10% net —
realistic but slow. With losers included (expectancy = 0.019%/trade),
it takes ~515 trades or
13 months for +10%.

---

## 11. Plain-Language Q&A

### Q1: Is cash drag the primary performance killer?
**Yes.** Mean exposure of 35% means 65% of capital sat idle.
Hypothetically deploying all capital with the same edge would yield ~113%
vs the actual 34%. Cash drag costs ~79pp of return.

### Q2: Are the signals weak?
**Partially.** Profit factor of 1.19 is modest but positive. Win rate of 53%
is decent. The edge exists but is thin, and very concentrated in top trades.

### Q3: Are costs a significant drag?
**Minor.** Estimated total costs of 321,407 PLN (13% of gross profit) are
manageable. Cost drag is only ~37.5pp of total return.

### Q4: Is exit logic hurting performance?
**Yes, somewhat.** The 14% profit capture rate means the system
captures only part of unrealized gains. 102 trades hit +8% then closed below +4%.

### Q5: Is position sizing appropriate?
**No — positions are too small.** At 3.6% average position, even a 10% winning trade
adds only 0.36% to the portfolio. To meaningfully grow capital, either increase
position size or trade much more frequently.

### Q6: Is trade frequency sufficient?
**No.** 467 trades/year at current position sizes cannot compound fast enough.
Doubling trade frequency or increasing position size to 5-6% would dramatically improve CAGR.

### Q7: Is P&L well-distributed?
**No.** Top 10 trades account for 17% of all profit. The system relies on occasional
large winners. Without those, return would be only 32.8%.

### Q8: What would most improve performance?
In order of impact:
1. **Increase exposure** from 35% to 60-80% (deploy cash)
2. **Increase position size** from 3.6% to 4-6% per trade
3. **Add more strategies** or widen the universe to increase trade frequency
4. **Improve exit logic** to capture more of the unrealized gains
5. **Filter to high-score setups** — scores 80+ show better characteristics

---

## Files Generated

| File | Description |
|------|-------------|
| `exposure_analysis.csv` | Daily exposure data (1,689 rows) |
| `cash_drag_analysis.csv` | Cash drag quantification |
| `strategy_contribution.csv` | Per-strategy P&L and metrics |
| `score_threshold_analysis.csv` | Score bucket analysis + threshold simulation |
| `exit_logic_attribution.csv` | Exit logic comparison + profit capture |
| `benchmark_validation.md` | SPY benchmark bug audit and fix |
| `return_attribution_analysis.md` | This comprehensive report |
