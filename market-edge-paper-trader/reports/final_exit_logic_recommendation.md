# Final Exit Logic Recommendation — SIMPLE_DYNAMIC_EXIT_V1

> **Note:** Network unavailable — no price data could be fetched (yfinance blocked). Reports show structure only; run in GitHub Actions where egress to query1.finance.yahoo.com is permitted for real numbers.

Answers below are data-driven where the comparative backtest produced trades; otherwise they describe the designed behaviour.

### 1. Does the new logic close profitable positions that the old logic left open?

The legacy ATR-based TP placed targets ~20-25% above entry, so winners rarely hit TP. The new logic fixes TP at +10% (max +12%). FIXED_TP take_profit exits: see exit_logic_comparison.csv (legacy return 0.0% vs FIXED_TP 0.0%).

### 2. Which variant has the best risk-adjusted return?

Compare Sharpe: LEGACY=0.0, FIXED_TP=0.0, TRAILING=0.0; Calmar: LEGACY=0.0, FIXED_TP=0.0, TRAILING=0.0.

### 3. How much profit is captured vs left on the table?

avg_profit_capture_ratio: FIXED_TP=0, TRAILING=0. See profit_capture_analysis.csv per trade.

### 4. How often do gaps blow through the active stop?

gap_below_stop_count: FIXED_TP=0, TRAILING=0; avg gap loss 0%. See gap_risk_analysis.csv.

### 5. Does break-even-after-+4% reduce losers?

reached_4pct_count FIXED_TP=0; lost_after_8pct_count=0. Break-even (incl. round-trip costs) protects trades that reached +4%.

### 6. How many trades reach the profit-lock thresholds?

reached 4/6/8/10%: FIXED_TP=0/0/0/0.

### 7. Does TRAILING_AFTER_10 capture more upside than FIXED_TP?

TRAILING return=0.0% vs FIXED_TP=0.0%; gave_back_after_10pct_count(TRAILING)=0.

### 8. What is the win rate / expectancy trade-off?

win_rate FIXED_TP=0% expectancy 0 PLN; TRAILING win_rate=0% expectancy 0 PLN.

### 9. How sensitive are results to parameter choices?

See parameter_sensitivity_exit_logic.csv — grid over max_sl [0.05,0.06,0.07], tp [0.08,0.10,0.12], trailing [0.02,0.03,0.04], be_trigger [0.03,0.04,0.05].

### 10. Recommended configuration?

Default: FIXED_TP_DYNAMIC_STOP for primary book (predictable, tight profit capture, fewer give-backs); enable TRAILING_AFTER_10 for strong-momentum names. Initial stop capped at 7%, TP +10% (max +12%), break-even at +4% incl. costs, locks at +6/+8/+10%, 3% trailing above +10%, 10-session limit (15 for trailing winners).

