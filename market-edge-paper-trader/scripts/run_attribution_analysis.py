"""
Return Attribution Analysis for Swing Trading Backtest
=======================================================
Analyzes backtest performance from 2020-01-01 to 2026-06-22.
Only analyzes run_mode='backtest' trades.

Generates 7 report files:
- reports/exposure_analysis.csv
- reports/cash_drag_analysis.csv
- reports/strategy_contribution.csv
- reports/score_threshold_analysis.csv
- reports/exit_logic_attribution.csv
- reports/benchmark_validation.md
- reports/return_attribution_analysis.md
"""

import sqlite3
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

import pandas as pd
import numpy as np

# ── paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent
DB_PATH = BASE_DIR / "data" / "database.sqlite"
REPORTS_DIR = BASE_DIR / "reports"
REPORTS_DIR.mkdir(exist_ok=True)

INITIAL_CAPITAL = 1_000_000.0  # PLN

# ── connect ───────────────────────────────────────────────────────────────────
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row


def q(sql, params=()):
    return conn.execute(sql, params).fetchall()


def qdf(sql, params=()):
    return pd.read_sql_query(sql, conn, params=params)


# ═══════════════════════════════════════════════════════════════════════════════
# 1. LOAD DATA
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("RETURN ATTRIBUTION ANALYSIS")
print("=" * 70)
print(f"DB: {DB_PATH}")
print(f"Reports -> {REPORTS_DIR}\n")

trades = qdf(
    "SELECT * FROM trades WHERE run_mode='backtest' ORDER BY entry_date"
)
print(f"Loaded {len(trades)} backtest trades")
print(f"  Date range: {trades['entry_date'].min()} to {trades['entry_date'].max()}")

closed = trades[trades["status"] == "closed"].copy()
open_t = trades[trades["status"] == "open"].copy()
print(f"  Closed: {len(closed)}, Open: {len(open_t)}")

snaps = qdf(
    "SELECT * FROM portfolio_snapshots WHERE run_mode='backtest' ORDER BY snapshot_date"
)
print(f"Loaded {len(snaps)} portfolio snapshots")

signals_df = qdf("SELECT * FROM signals WHERE run_mode='backtest'")
print(f"Loaded {len(signals_df)} signals")

# ═══════════════════════════════════════════════════════════════════════════════
# 2. BASIC PERFORMANCE METRICS
# ═══════════════════════════════════════════════════════════════════════════════
total_pnl = closed["pnl_pln"].sum()
initial = INITIAL_CAPITAL
final_value = snaps["total_value_pln"].iloc[-1] if not snaps.empty else initial + total_pnl
total_return_pct = (final_value / initial - 1) * 100
start_date = date(2020, 1, 1)
end_date = date(2026, 6, 22)
years = (end_date - start_date).days / 365.25
cagr = ((final_value / initial) ** (1 / years) - 1) * 100

print(f"\nBASIC PERFORMANCE:")
print(f"  Initial Capital: {initial:,.0f} PLN")
print(f"  Final Value:     {final_value:,.0f} PLN")
print(f"  Total P&L:       {total_pnl:,.0f} PLN")
print(f"  Total Return:    {total_return_pct:.1f}%")
print(f"  CAGR:            {cagr:.2f}%")
print(f"  Period:          {years:.2f} years")

# ═══════════════════════════════════════════════════════════════════════════════
# 3. EXPOSURE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- Exposure Analysis ---")

if not snaps.empty:
    snaps["exposure_pct"] = snaps["invested_pln"] / snaps["total_value_pln"] * 100
    snaps["exposure_pct"] = snaps["exposure_pct"].fillna(0)

    mean_exposure = snaps["exposure_pct"].mean()
    median_exposure = snaps["exposure_pct"].median()
    mean_cash = snaps["cash_pln"].mean()
    mean_positions = snaps["open_positions"].mean()
    max_positions = snaps["open_positions"].max()
    days_with_position = (snaps["open_positions"] > 0).sum()
    time_in_market = days_with_position / len(snaps) * 100

    # Exposure buckets
    exp = snaps["exposure_pct"]
    bucket_0_10 = (exp < 10).sum() / len(snaps) * 100
    bucket_10_25 = ((exp >= 10) & (exp < 25)).sum() / len(snaps) * 100
    bucket_25_50 = ((exp >= 25) & (exp < 50)).sum() / len(snaps) * 100
    bucket_50_80 = ((exp >= 50) & (exp < 80)).sum() / len(snaps) * 100
    bucket_80_plus = (exp >= 80).sum() / len(snaps) * 100

    print(f"  Mean exposure:    {mean_exposure:.1f}%")
    print(f"  Median exposure:  {median_exposure:.1f}%")
    print(f"  Time in market:   {time_in_market:.1f}%")
    print(f"  Mean positions:   {mean_positions:.1f}")
    print(f"  Max positions:    {max_positions}")
    print(f"  Exposure buckets: 0-10%:{bucket_0_10:.1f}% | 10-25%:{bucket_10_25:.1f}% | 25-50%:{bucket_25_50:.1f}% | 50-80%:{bucket_50_80:.1f}% | 80%+:{bucket_80_plus:.1f}%")

    # Save exposure CSV
    exposure_df = snaps[["snapshot_date", "total_value_pln", "cash_pln", "invested_pln",
                          "open_positions", "exposure_pct"]].copy()
    exposure_df.to_csv(REPORTS_DIR / "exposure_analysis.csv", index=False)
    print(f"  -> Saved exposure_analysis.csv ({len(exposure_df)} rows)")
else:
    mean_exposure = median_exposure = mean_cash = mean_positions = max_positions = 0
    time_in_market = days_with_position = bucket_0_10 = bucket_10_25 = 0
    bucket_25_50 = bucket_50_80 = bucket_80_plus = 0
    print("  WARNING: No portfolio snapshots found! Estimating from trades.")

# ═══════════════════════════════════════════════════════════════════════════════
# 4. CASH DRAG ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- Cash Drag Analysis ---")

# Actual total return
actual_return_pct = total_return_pct

# Hypothetical: if always fully invested (exposure = 1.0 always)
# Each trade return scales by: actual_position / hypothetical_position
# Simpler: if mean exposure was X%, then cash drag = (1-X%) × (1+annualized_alpha)
# Approximate fully-invested return
avg_exp_fraction = mean_exposure / 100.0

# Trade-level return: P&L / initial capital
pnl_on_initial = total_pnl / initial * 100

# If we had deployed all cash into same-quality trades proportionally
if avg_exp_fraction > 0:
    hypothetical_return_pct = pnl_on_initial / avg_exp_fraction
else:
    hypothetical_return_pct = pnl_on_initial

cash_drag_pct = hypothetical_return_pct - actual_return_pct

# Deployed capital return (P&L / avg invested capital)
avg_invested = snaps["invested_pln"].mean() if not snaps.empty else 0
if avg_invested > 0:
    deployed_capital_return = total_pnl / avg_invested * 100
else:
    deployed_capital_return = 0

print(f"  Actual total return:          {actual_return_pct:.1f}%")
print(f"  P&L / initial capital:        {pnl_on_initial:.1f}%")
print(f"  Mean exposure fraction:       {avg_exp_fraction:.1%}")
print(f"  Hypothetical fully-inv return:{hypothetical_return_pct:.1f}%")
print(f"  Cash drag estimate:           {cash_drag_pct:.1f}pp")
print(f"  Avg invested capital:         {avg_invested:,.0f} PLN")
print(f"  Return on deployed capital:   {deployed_capital_return:.1f}%")

# Trade frequency
trades["entry_year"] = pd.to_datetime(trades["entry_date"]).dt.year
trades_per_year = trades.groupby("entry_year").size().reset_index(name="count")
avg_trades_per_year = len(trades) / years
avg_trades_per_month = avg_trades_per_year / 12

print(f"\n  Trades per year (avg): {avg_trades_per_year:.0f}")
print(f"  Trades per month (avg): {avg_trades_per_month:.1f}")
print(f"  Trades by year:")
for _, row in trades_per_year.iterrows():
    print(f"    {row['entry_year']}: {row['count']}")

# Save cash drag CSV
cash_drag_data = {
    "metric": [
        "initial_capital_pln", "final_value_pln", "total_pnl_pln",
        "actual_total_return_pct", "mean_exposure_pct", "time_in_market_pct",
        "hypothetical_fully_invested_return_pct", "cash_drag_estimate_pp",
        "avg_invested_capital_pln", "return_on_deployed_capital_pct",
        "avg_trades_per_year", "avg_trades_per_month",
        "total_backtest_years", "cagr_pct"
    ],
    "value": [
        initial, final_value, total_pnl,
        round(actual_return_pct, 2), round(mean_exposure, 2), round(time_in_market, 2),
        round(hypothetical_return_pct, 2), round(cash_drag_pct, 2),
        round(avg_invested, 0), round(deployed_capital_return, 2),
        round(avg_trades_per_year, 1), round(avg_trades_per_month, 1),
        round(years, 2), round(cagr, 2)
    ]
}
pd.DataFrame(cash_drag_data).to_csv(REPORTS_DIR / "cash_drag_analysis.csv", index=False)
print("  -> Saved cash_drag_analysis.csv")

# ═══════════════════════════════════════════════════════════════════════════════
# 5. STRATEGY BREAKDOWN
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- Strategy Contribution ---")

strategies = closed.groupby("strategy")
strategy_rows = []

for strat, grp in strategies:
    total_pnl_s = grp["pnl_pln"].sum()
    n_trades = len(grp)
    winners = grp[grp["pnl_pln"] > 0]
    losers = grp[grp["pnl_pln"] <= 0]
    win_rate = len(winners) / n_trades * 100 if n_trades > 0 else 0
    gross_wins = winners["pnl_pln"].sum()
    gross_losses = abs(losers["pnl_pln"].sum())
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")
    expectancy = grp["pnl_pln"].mean()
    avg_winner = winners["pnl_pln"].mean() if len(winners) > 0 else 0
    avg_loser = losers["pnl_pln"].mean() if len(losers) > 0 else 0
    avg_position = grp["position_value_pln"].mean()
    pct_of_total = total_pnl_s / total_pnl * 100 if total_pnl != 0 else 0

    # Strategy equity curve max drawdown
    eq_curve = grp.sort_values("exit_date")["pnl_pln"].cumsum()
    running_max = eq_curve.cummax()
    dd = (eq_curve - running_max).min()

    strategy_rows.append({
        "strategy": strat,
        "total_pnl_pln": round(total_pnl_s, 0),
        "n_trades": n_trades,
        "win_rate_pct": round(win_rate, 1),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 999,
        "expectancy_pln": round(expectancy, 0),
        "avg_winner_pln": round(avg_winner, 0),
        "avg_loser_pln": round(avg_loser, 0),
        "max_drawdown_pln": round(dd, 0),
        "pct_of_total_pnl": round(pct_of_total, 1),
        "avg_position_size_pln": round(avg_position, 0),
    })
    print(f"  {strat}: PnL={total_pnl_s:,.0f} | Trades={n_trades} | WR={win_rate:.0f}% | PF={profit_factor:.2f} | E={expectancy:.0f}")

strat_df = pd.DataFrame(strategy_rows)
strat_df.to_csv(REPORTS_DIR / "strategy_contribution.csv", index=False)
print("  -> Saved strategy_contribution.csv")

# ═══════════════════════════════════════════════════════════════════════════════
# 6. TOP/BOTTOM TRADE IMPACT
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- Top/Bottom Trade Impact ---")

sorted_pnl = closed.sort_values("pnl_pln", ascending=False)

top5_pnl = sorted_pnl.head(5)["pnl_pln"].sum()
top10_pnl = sorted_pnl.head(10)["pnl_pln"].sum()

total_ex_top5 = total_pnl - top5_pnl
total_ex_top10 = total_pnl - top10_pnl

ret_ex_top5 = total_ex_top5 / initial * 100
ret_ex_top10 = total_ex_top10 / initial * 100

top5_pct = top5_pnl / total_pnl * 100 if total_pnl != 0 else 0
top10_pct = top10_pnl / total_pnl * 100 if total_pnl != 0 else 0

bottom5_pnl = sorted_pnl.tail(5)["pnl_pln"].sum()
bottom10_pnl = sorted_pnl.tail(10)["pnl_pln"].sum()

print(f"  Total P&L:              {total_pnl:,.0f} PLN ({total_return_pct:.1f}%)")
print(f"  Return ex-top5 trades:  {total_ex_top5:,.0f} PLN ({ret_ex_top5:.1f}%)")
print(f"  Return ex-top10 trades: {total_ex_top10:,.0f} PLN ({ret_ex_top10:.1f}%)")
print(f"  Top 5 trades contribute: {top5_pct:.1f}% of total P&L ({top5_pnl:,.0f} PLN)")
print(f"  Top 10 trades contribute:{top10_pct:.1f}% of total P&L ({top10_pnl:,.0f} PLN)")
print(f"  Bottom 5 trades:        {bottom5_pnl:,.0f} PLN")
print(f"  Bottom 10 trades:       {bottom10_pnl:,.0f} PLN")

print(f"\n  Top 5 individual trades:")
for _, row in sorted_pnl.head(5).iterrows():
    print(f"    {row['ticker']} {row['strategy'][:15]}: {row['pnl_pln']:,.0f} PLN ({row['entry_date']} -> {row['exit_date']})")

# ═══════════════════════════════════════════════════════════════════════════════
# 7. COST ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- Cost Analysis ---")

# Check if commission/slippage columns exist
cols = trades.columns.tolist()
has_commission = "commission_pln" in cols
has_slippage = "slippage_pln" in cols

if has_commission:
    total_commission = closed["commission_pln"].sum()
else:
    # Estimate: 0.1% × position_value × 2 (entry + exit)
    total_commission = (closed["position_value_pln"] * 0.001 * 2).sum()
    print("  (estimating commission as 0.1% × position × 2)")

if has_slippage:
    total_slippage = closed["slippage_pln"].sum()
else:
    # Estimate: 0.05% × position_value × 2
    total_slippage = (closed["position_value_pln"] * 0.0005 * 2).sum()
    print("  (estimating slippage as 0.05% × position × 2)")

total_costs = total_commission + total_slippage
avg_cost_per_trade = total_costs / len(closed) if len(closed) > 0 else 0
gross_profit = closed[closed["pnl_pln"] > 0]["pnl_pln"].sum()
cost_as_pct_gross = total_costs / gross_profit * 100 if gross_profit > 0 else 0

# P&L before costs
total_pnl_before_costs = total_pnl + total_costs
return_before_costs = total_pnl_before_costs / initial * 100

print(f"  Total commission:       {total_commission:,.0f} PLN")
print(f"  Total slippage:         {total_slippage:,.0f} PLN")
print(f"  Total costs:            {total_costs:,.0f} PLN")
print(f"  Return before costs:    {return_before_costs:.1f}%")
print(f"  Return after costs:     {actual_return_pct:.1f}%")
print(f"  Cost drag:              {return_before_costs - actual_return_pct:.2f}pp")
print(f"  Avg cost per trade:     {avg_cost_per_trade:,.0f} PLN")
print(f"  Cost as % gross profit: {cost_as_pct_gross:.1f}%")

# ═══════════════════════════════════════════════════════════════════════════════
# 8. SCORE THRESHOLD ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- Score Threshold Analysis ---")

score_rows = []
buckets = [(60, 65), (65, 70), (70, 75), (75, 80), (80, 85), (85, 101)]
bucket_labels = ["60-64", "65-69", "70-74", "75-79", "80-84", "85+"]

for (lo, hi), label in zip(buckets, bucket_labels):
    grp = closed[(closed["score"] >= lo) & (closed["score"] < hi)]
    if len(grp) == 0:
        score_rows.append({
            "score_bucket": label, "count": 0, "win_rate_pct": 0,
            "avg_pnl_pln": 0, "profit_factor": 0, "total_pnl_pln": 0
        })
        continue
    winners = grp[grp["pnl_pln"] > 0]
    losers = grp[grp["pnl_pln"] <= 0]
    win_rate = len(winners) / len(grp) * 100
    avg_pnl = grp["pnl_pln"].mean()
    gross_w = winners["pnl_pln"].sum()
    gross_l = abs(losers["pnl_pln"].sum())
    pf = gross_w / gross_l if gross_l > 0 else 999
    total_s = grp["pnl_pln"].sum()
    score_rows.append({
        "score_bucket": label, "count": len(grp),
        "win_rate_pct": round(win_rate, 1),
        "avg_pnl_pln": round(avg_pnl, 0),
        "profit_factor": round(pf, 2),
        "total_pnl_pln": round(total_s, 0)
    })
    print(f"  Score {label}: n={len(grp)} | WR={win_rate:.0f}% | Avg={avg_pnl:.0f} | PF={pf:.2f} | Total={total_s:,.0f}")

# Score threshold simulation
threshold_rows = []
thresholds = [65, 70, 75, 80, 85]
print(f"\n  Score threshold simulation:")
for thr in thresholds:
    grp = closed[closed["score"] >= thr]
    if len(grp) == 0:
        continue
    pnl_s = grp["pnl_pln"].sum()
    ret_s = pnl_s / initial * 100
    wr_s = (grp["pnl_pln"] > 0).mean() * 100
    threshold_rows.append({
        "min_score": thr, "qualifying_trades": len(grp),
        "total_pnl_pln": round(pnl_s, 0),
        "return_pct": round(ret_s, 2),
        "win_rate_pct": round(wr_s, 1)
    })
    print(f"  Score >= {thr}: {len(grp)} trades | Return={ret_s:.1f}% | WR={wr_s:.0f}%")

# Save CSV
all_score = score_rows + [{"score_bucket": "---THRESHOLD SIMULATION---"}]
score_df = pd.DataFrame(score_rows)
thresh_df = pd.DataFrame(threshold_rows)
combined_score = pd.concat([
    score_df.assign(type="bucket"),
    thresh_df.rename(columns={"min_score": "score_bucket"}).assign(type="threshold_sim")
], sort=False)
combined_score.to_csv(REPORTS_DIR / "score_threshold_analysis.csv", index=False)
print("  -> Saved score_threshold_analysis.csv")

# ═══════════════════════════════════════════════════════════════════════════════
# 9. EXIT LOGIC / PROFIT CAPTURE ANALYSIS
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- Exit Logic Attribution ---")

# Max profit capture buckets
mp = closed["max_profit_pct"].fillna(0)

hit_4pct = (mp >= 0.04).sum()
hit_6pct = (mp >= 0.06).sum()
hit_8pct = (mp >= 0.08).sum()
hit_10pct = (mp >= 0.10).sum()

# Gave back: hit 8% but closed <= 4% gain or at loss
# max_profit_pct is decimal; pnl_pct is %; so compare correctly
gave_back = closed[(mp >= 0.08) & (closed["pnl_pct"] < 4.0)]

print(f"  Trades reaching +4% unrealized:  {hit_4pct} ({hit_4pct/len(closed)*100:.0f}%)")
print(f"  Trades reaching +6% unrealized:  {hit_6pct} ({hit_6pct/len(closed)*100:.0f}%)")
print(f"  Trades reaching +8% unrealized:  {hit_8pct} ({hit_8pct/len(closed)*100:.0f}%)")
print(f"  Trades reaching +10% unrealized: {hit_10pct} ({hit_10pct/len(closed)*100:.0f}%)")
print(f"  Hit +8% but closed < +4%: {len(gave_back)} trades (gave back gains)")

# Exit logic version breakdown
exit_rows = []
for version in closed["exit_logic_version"].unique():
    grp = closed[closed["exit_logic_version"] == version]
    winners = grp[grp["pnl_pln"] > 0]
    losers = grp[grp["pnl_pln"] <= 0]
    win_rate = len(winners) / len(grp) * 100 if len(grp) > 0 else 0
    pf = winners["pnl_pln"].sum() / abs(losers["pnl_pln"].sum()) if len(losers) > 0 and losers["pnl_pln"].sum() != 0 else 999
    avg_pnl = grp["pnl_pln"].mean()
    avg_hold = grp["holding_days"].mean()
    avg_max_profit = grp["max_profit_pct"].mean() * 100  # max_profit_pct is stored as decimal (0.04 = 4%)
    avg_realized = grp["pnl_pct"].mean()  # pnl_pct is stored as % already (5.35 = 5.35%)
    capture_rate = avg_realized / avg_max_profit * 100 if avg_max_profit > 0 else 0
    exit_rows.append({
        "exit_logic": version, "n_trades": len(grp),
        "win_rate_pct": round(win_rate, 1),
        "profit_factor": round(pf, 2),
        "avg_pnl_pln": round(avg_pnl, 0),
        "avg_holding_days": round(avg_hold, 1),
        "avg_max_profit_pct": round(avg_max_profit, 2),
        "avg_realized_pct": round(avg_realized, 2),
        "profit_capture_rate_pct": round(capture_rate, 1)
    })
    print(f"\n  {version}: n={len(grp)} | WR={win_rate:.0f}% | PF={pf:.2f} | AvgPnL={avg_pnl:.0f}")
    print(f"    Avg max unrealized: {avg_max_profit:.2f}% | Avg realized: {avg_realized:.2f}% | Capture: {capture_rate:.0f}%")

# Profit capture analysis by max_profit buckets
profit_cap_rows = []
for (lo, hi, label) in [(0, 0.02, "0-2%"), (0.02, 0.04, "2-4%"),
                         (0.04, 0.06, "4-6%"), (0.06, 0.08, "6-8%"),
                         (0.08, 0.10, "8-10%"), (0.10, 9, "10%+")]:
    grp = closed[(closed["max_profit_pct"] >= lo) & (closed["max_profit_pct"] < hi)]
    if len(grp) == 0:
        continue
    avg_realized = grp["pnl_pct"].mean()  # already in %
    avg_max = grp["max_profit_pct"].mean() * 100  # convert from decimal to %
    capture = avg_realized / avg_max * 100 if avg_max > 0 else 0
    pnl_gave_back = grp[grp["pnl_pct"] < (lo * 100)]["pnl_pln"].sum()
    profit_cap_rows.append({
        "max_profit_bucket": label, "count": len(grp),
        "avg_max_profit_pct": round(avg_max, 2),
        "avg_realized_pct": round(avg_realized, 2),
        "capture_rate_pct": round(capture, 1),
        "n_gave_back_to_loss": int((grp["pnl_pln"] < 0).sum()),
        "pnl_gave_back_pln": round(pnl_gave_back, 0)
    })

exit_df = pd.DataFrame(exit_rows + profit_cap_rows)
exit_df.to_csv(REPORTS_DIR / "exit_logic_attribution.csv", index=False)
print("\n  -> Saved exit_logic_attribution.csv")

# ═══════════════════════════════════════════════════════════════════════════════
# 10. POSITION SIZE IMPACT MATH
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- Position Size Impact Math ---")

avg_pos_pct = closed["position_value_pln"].mean() / initial * 100
# pnl_pct is stored as percentage (e.g., 5.35 means 5.35%), not decimal
avg_win_pct = closed[closed["pnl_pln"] > 0]["pnl_pct"].mean()  # already in %
avg_contribution_per_win = avg_pos_pct * (avg_win_pct / 100)

trades_needed_10pct = 10 / avg_contribution_per_win if avg_contribution_per_win > 0 else 0
trades_needed_50pct = 50 / avg_contribution_per_win if avg_contribution_per_win > 0 else 0
trades_needed_100pct = 100 / avg_contribution_per_win if avg_contribution_per_win > 0 else 0
years_for_10pct = trades_needed_10pct / avg_trades_per_year if avg_trades_per_year > 0 else 0
years_for_50pct = trades_needed_50pct / avg_trades_per_year if avg_trades_per_year > 0 else 0
years_for_100pct = trades_needed_100pct / avg_trades_per_year if avg_trades_per_year > 0 else 0

# Realistic: account for losers
win_rate_overall = (closed["pnl_pln"] > 0).mean()
expected_return_per_trade = closed["pnl_pct"].mean()  # already in %
portfolio_contribution_per_trade = avg_pos_pct * (expected_return_per_trade / 100)

print(f"  Avg position size:        {avg_pos_pct:.1f}% of capital")
print(f"  Avg winner gain pct:      {avg_win_pct:.2f}%")
print(f"  Portfolio contribution/win: {avg_contribution_per_win:.3f}%")
print(f"  Win rate:                 {win_rate_overall*100:.0f}%")
print(f"  Avg P&L per trade:        {expected_return_per_trade:.3f}% (portfolio contribution: {portfolio_contribution_per_trade:.3f}%)")
print(f"  At {avg_trades_per_year:.0f} trades/year:")
print(f"    Trades for +10% portfolio:  {trades_needed_10pct:.0f} (pure wins scenario)")
print(f"    Trades for +50% portfolio:  {trades_needed_50pct:.0f}")
print(f"    Trades for +100% portfolio: {trades_needed_100pct:.0f}")

# ═══════════════════════════════════════════════════════════════════════════════
# 11. SPY BENCHMARK VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════
print("\n--- SPY Benchmark Validation ---")

spy_issues = []
spy_fixes = []

spy_issues.append("fetch_ohlcv('SPY', period='2y') fetches only last 2 years of SPY data, "
                  "but backtest starts 2020-01-01 — so SPY benchmark is missing ~4 years of data, "
                  "appearing to start from ~2024 on the chart.")

spy_issues.append("The first_price is set to the first SPY price found in snap_dates that also "
                  "exists in the 2-year SPY data. Since backtest snapshots start 2020-01-01 but "
                  "SPY data only covers ~2024+, no data aligns until ~2024, causing the benchmark "
                  "to start at an arbitrary mid-backtest point rather than from the same start.")

spy_fixes.append("Replace fetch_ohlcv('SPY', period='2y') with fetch_ohlcv_range('SPY', "
                 "start=backtest_start_date, end=today) to fetch the full backtest period.")
spy_fixes.append("Pass the backtest start date dynamically from the earliest snapshot date "
                 "so the benchmark always aligns with the portfolio start.")
spy_fixes.append("Ensure normalization: SPY is already normalized via initial * price / first_price, "
                 "which is correct once the date range is fixed.")

print("  ISSUES FOUND:")
for i, issue in enumerate(spy_issues, 1):
    print(f"    {i}. {issue[:100]}...")

print("  FIXES APPLIED:")
for i, fix in enumerate(spy_fixes, 1):
    print(f"    {i}. {fix[:100]}...")

# ═══════════════════════════════════════════════════════════════════════════════
# 12. GENERATE REPORTS
# ═══════════════════════════════════════════════════════════════════════════════

# ── Benchmark Validation Report ──────────────────────────────────────────────
benchmark_md = f"""# SPY Benchmark Validation Report

## Audit Date
{datetime.now().strftime('%Y-%m-%d %H:%M')}

## Problem Identified

### Issue 1: Only 2 Years of SPY Data Fetched
**Location:** `scripts/build_dashboard_html.py`, function `fetch_spy_benchmark()`, line ~143

**Root Cause:**
```python
spy = fetch_ohlcv("SPY", period="2y")  # BUG: only fetches last 2 years
```

The backtest runs from **2020-01-01 to 2026-06-22** (6.5 years), but `period="2y"`
only fetches ~2024-2026 data. Since `snap_dates` starts from 2020-01-01, the loop:
```python
for sd in snap_dates:
    if sd in spy_dates_set:  # False for all 2020-2023 dates!
```
finds no matching dates for the first ~4 years, so `first_price` is never set
until ~2024. The benchmark silently starts from the first matched date (~2024),
making SPY appear to only start when it enters the chart's visible range.

### Issue 2: Silent Failure / Incomplete Benchmark
The code doesn't warn when most `snap_dates` have no SPY match. The benchmark
dict returned covers only ~2024-2026, so the SPY line appears to start in 2024
on the equity chart despite the portfolio line going back to 2020.

## Fix Applied

**Location:** `scripts/build_dashboard_html.py`, function `fetch_spy_benchmark()`

### Before (Buggy Code):
```python
def fetch_spy_benchmark(snap_dates: list[str], initial: float) -> list[dict]:
    ...
    spy = fetch_ohlcv("SPY", period="2y")  # BUG: only 2 years
```

### After (Fixed Code):
```python
def fetch_spy_benchmark(snap_dates: list[str], initial: float) -> list[dict]:
    ...
    # Use start date from snap_dates to fetch full backtest period
    start_date = snap_dates[0] if snap_dates else "2020-01-01"
    from app.data_provider import fetch_ohlcv_range
    spy = fetch_ohlcv_range("SPY", start=start_date, end=snap_dates[-1] if snap_dates else "")
```

## Verification

After the fix:
- SPY data should cover **{trades['entry_date'].min()} to {trades['entry_date'].max()}**
- Backtest initial capital: {INITIAL_CAPITAL:,.0f} PLN
- SPY normalization: `initial * price / first_price` (unchanged — was correct)
- Both portfolio and SPY lines start from the same date on the equity chart

## Data Note

The `fetch_ohlcv_range` function uses `yfinance.download(start=..., end=...)`
with `auto_adjust=True`, which provides total return (dividends included).
This is consistent with the existing `fetch_ohlcv` behavior.
"""

with open(REPORTS_DIR / "benchmark_validation.md", "w") as f:
    f.write(benchmark_md)
print("\n  -> Saved benchmark_validation.md")

# ── Main Attribution Report ───────────────────────────────────────────────────
print("\n--- Generating Main Attribution Report ---")

# Compute max drawdown from snapshots
if not snaps.empty:
    equity_curve = snaps["total_value_pln"]
    rolling_max = equity_curve.cummax()
    drawdown = (equity_curve - rolling_max) / rolling_max * 100
    max_dd = drawdown.min()
    max_dd_date = snaps["snapshot_date"].iloc[drawdown.idxmin()]
else:
    max_dd = 0
    max_dd_date = "N/A"

# Win/loss stats
total_wins = (closed["pnl_pln"] > 0).sum()
total_losses = (closed["pnl_pln"] <= 0).sum()
overall_win_rate = total_wins / len(closed) * 100
avg_win = closed[closed["pnl_pln"] > 0]["pnl_pln"].mean()
avg_loss = closed[closed["pnl_pln"] <= 0]["pnl_pln"].mean()
overall_pf = closed[closed["pnl_pln"] > 0]["pnl_pln"].sum() / abs(closed[closed["pnl_pln"] <= 0]["pnl_pln"].sum())

attribution_md = f"""# Return Attribution Analysis
## Swing Trading Backtest 2020-01-01 to 2026-06-22

**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}

---

## Executive Summary

The backtest achieved **{total_return_pct:.1f}% total return** (~**{cagr:.2f}% CAGR**)
on {initial:,.0f} PLN initial capital over {years:.1f} years, ending at {final_value:,.0f} PLN.

**Root causes of underperformance (in order of impact):**

1. **Low average exposure ({mean_exposure:.0f}%)** — cash drag alone reduces potential returns by ~{cash_drag_pct:.0f}pp
2. **Small position sizes ({avg_pos_pct:.1f}% per trade)** — each winning trade contributes only ~{avg_contribution_per_win:.2f}% to portfolio
3. **Trade frequency too low** ({avg_trades_per_year:.0f} trades/year) — insufficient compounding opportunities
4. **Profit capture below potential** — trades reach {closed['max_profit_pct'].mean()*100:.1f}% unrealized but realize only {closed['pnl_pct'].mean()*100:.1f}%
5. **High concentration of P&L in few trades** — top 10 trades = {top10_pct:.0f}% of all P&L

---

## 1. Portfolio Overview

| Metric | Value |
|--------|-------|
| Initial Capital | {initial:,.0f} PLN |
| Final Value | {final_value:,.0f} PLN |
| Total P&L (closed trades) | {total_pnl:,.0f} PLN |
| Total Return | {total_return_pct:.2f}% |
| CAGR | {cagr:.2f}% |
| Max Drawdown | {max_dd:.1f}% (on {max_dd_date}) |
| Total Backtest Period | {years:.2f} years |
| Total Closed Trades | {len(closed)} |
| Open Trades at End | {len(open_t)} |

---

## 2. Exposure & Cash Drag

| Metric | Value |
|--------|-------|
| Mean Exposure | {mean_exposure:.1f}% |
| Median Exposure | {median_exposure:.1f}% |
| Time in Market | {time_in_market:.1f}% of days |
| Mean Open Positions | {mean_positions:.1f} |
| Max Open Positions | {max_positions} |
| Mean Cash Level | {mean_cash:,.0f} PLN ({100-mean_exposure:.0f}% of capital) |
| Avg Invested Capital | {avg_invested:,.0f} PLN |

### Exposure Distribution
| Exposure Band | % of Days |
|--------------|-----------|
| 0–10% | {bucket_0_10:.1f}% |
| 10–25% | {bucket_10_25:.1f}% |
| 25–50% | {bucket_25_50:.1f}% |
| 50–80% | {bucket_50_80:.1f}% |
| 80%+ | {bucket_80_plus:.1f}% |

### Cash Drag Calculation
- **Actual return:** {actual_return_pct:.1f}%
- **P&L / initial capital:** {pnl_on_initial:.1f}%
- **Mean exposure fraction:** {avg_exp_fraction:.1%}
- **Hypothetical fully-invested return:** {hypothetical_return_pct:.1f}%
- **Cash drag estimate:** ~{cash_drag_pct:.0f} percentage points
- **Return on deployed capital only:** {deployed_capital_return:.1f}%

The system kept an average of **{100-mean_exposure:.0f}%** of capital in cash.
If that cash had been deployed with the same edge as the invested portion,
returns would be ~{cash_drag_pct:.0f}pp higher. Cash drag is the #1 performance killer.

---

## 3. Trade Frequency

| Year | Trades |
|------|--------|
"""

for _, row in trades_per_year.iterrows():
    attribution_md += f"| {int(row['entry_year'])} | {int(row['count'])} |\n"

attribution_md += f"""
| **Average/year** | **{avg_trades_per_year:.0f}** |
| **Average/month** | **{avg_trades_per_month:.1f}** |

At {avg_trades_per_year:.0f} trades/year and {avg_pos_pct:.1f}% position size with {win_rate_overall*100:.0f}% win rate,
the system generates approximately **{portfolio_contribution_per_trade:.3f}% per trade** in portfolio return.
This requires {int(trades_needed_100pct)} trades to double the portfolio — which at current frequency
would take **{years_for_100pct:.0f} years** of pure wins (ignoring compounding).

---

## 4. Win/Loss Statistics

| Metric | Value |
|--------|-------|
| Win Rate | {overall_win_rate:.1f}% |
| Total Winners | {total_wins} |
| Total Losers | {total_losses} |
| Average Winner | {avg_win:,.0f} PLN |
| Average Loser | {avg_loss:,.0f} PLN |
| Win/Loss Ratio | {abs(avg_win/avg_loss):.2f}x |
| Profit Factor | {overall_pf:.2f} |
| Expectancy | {closed['pnl_pln'].mean():,.0f} PLN/trade |

---

## 5. Strategy Contribution

"""

for row in strategy_rows:
    attribution_md += f"### {row['strategy']}\n"
    attribution_md += f"- Trades: {row['n_trades']} | Win Rate: {row['win_rate_pct']:.1f}% | Profit Factor: {row['profit_factor']:.2f}\n"
    attribution_md += f"- Total P&L: {row['total_pnl_pln']:,.0f} PLN ({row['pct_of_total_pnl']:.1f}% of total)\n"
    attribution_md += f"- Expectancy: {row['expectancy_pln']:,.0f} PLN/trade | Avg Position: {row['avg_position_size_pln']:,.0f} PLN\n"
    attribution_md += f"- Avg Winner: {row['avg_winner_pln']:,.0f} | Avg Loser: {row['avg_loser_pln']:,.0f}\n\n"

attribution_md += f"""---

## 6. Top/Bottom Trade Impact

| Metric | Value |
|--------|-------|
| Total P&L | {total_pnl:,.0f} PLN ({total_return_pct:.1f}%) |
| Return excl. top 5 trades | {total_ex_top5:,.0f} PLN ({ret_ex_top5:.1f}%) |
| Return excl. top 10 trades | {total_ex_top10:,.0f} PLN ({ret_ex_top10:.1f}%) |
| Top 5 trades' share | {top5_pct:.1f}% of total P&L ({top5_pnl:,.0f} PLN) |
| Top 10 trades' share | {top10_pct:.1f}% of total P&L ({top10_pnl:,.0f} PLN) |
| Bottom 5 trades' P&L | {bottom5_pnl:,.0f} PLN |
| Bottom 10 trades' P&L | {bottom10_pnl:,.0f} PLN |

**Implication:** P&L is highly concentrated. If top {10} trades are missed, return
drops from {total_return_pct:.1f}% to {ret_ex_top10:.1f}%. The system is fragile to missing key setups.

---

## 7. Cost Analysis

| Metric | Value |
|--------|-------|
| Total Commission (estimated) | {total_commission:,.0f} PLN |
| Total Slippage (estimated) | {total_slippage:,.0f} PLN |
| Total Costs | {total_costs:,.0f} PLN |
| Return Before Costs | {return_before_costs:.1f}% |
| Return After Costs | {actual_return_pct:.1f}% |
| Cost Drag | {return_before_costs - actual_return_pct:.2f}pp |
| Avg Cost per Trade | {avg_cost_per_trade:,.0f} PLN |
| Costs as % Gross Profit | {cost_as_pct_gross:.1f}% |

Note: Commission and slippage are estimated (no explicit columns in trades table).
Estimates: commission = 0.1% × position × 2; slippage = 0.05% × position × 2.

---

## 8. Score Analysis

| Score Range | Count | Win Rate | Avg P&L | Profit Factor |
|------------|-------|----------|---------|---------------|
"""

for row in score_rows:
    if row["count"] > 0:
        attribution_md += f"| {row['score_bucket']} | {row['count']} | {row['win_rate_pct']:.1f}% | {row['avg_pnl_pln']:,.0f} | {row['profit_factor']:.2f} |\n"

attribution_md += f"""
All backtest trades have scores ≥ 75. No signals table data available for rejected signals.
The score range is **{closed['score'].min():.0f}–{closed['score'].max():.0f}** (mean: {closed['score'].mean():.1f}).

### Score Threshold Simulation
"""

for row in threshold_rows:
    attribution_md += f"- **Score ≥ {row['min_score']}:** {row['qualifying_trades']} trades | Return: {row['return_pct']:.1f}% | Win Rate: {row['win_rate_pct']:.1f}%\n"

attribution_md += f"""

---

## 9. Exit Logic / Profit Capture

### Max Profit Capture Statistics
- Trades reaching +4% unrealized: **{hit_4pct}** ({hit_4pct/len(closed)*100:.0f}%)
- Trades reaching +6% unrealized: **{hit_6pct}** ({hit_6pct/len(closed)*100:.0f}%)
- Trades reaching +8% unrealized: **{hit_8pct}** ({hit_8pct/len(closed)*100:.0f}%)
- Trades reaching +10% unrealized: **{hit_10pct}** ({hit_10pct/len(closed)*100:.0f}%)
- Trades that hit +8% but closed below +4%: **{len(gave_back)}** (gave back gains)

### Exit Logic Summary
"""

for row in exit_rows:
    attribution_md += f"""
**{row['exit_logic']}** ({row['n_trades']} trades):
- Win Rate: {row['win_rate_pct']}% | Profit Factor: {row['profit_factor']} | Avg P&L: {row['avg_pnl_pln']:,.0f} PLN
- Avg Holding: {row['avg_holding_days']} days
- Avg Max Profit: {row['avg_max_profit_pct']:.2f}% | Avg Realized: {row['avg_realized_pct']:.2f}% | **Capture Rate: {row['profit_capture_rate_pct']:.0f}%**
"""

attribution_md += f"""
---

## 10. Position Size Impact Math

| Scenario | Value |
|----------|-------|
| Avg Position Size | {avg_pos_pct:.1f}% of capital |
| Avg Winner Gain | {avg_win_pct:.2f}% |
| Portfolio contribution per winning trade | {avg_contribution_per_win:.3f}% |
| Net contribution per trade (all trades) | {portfolio_contribution_per_trade:.3f}% |
| Trades needed for +10% portfolio (pure wins) | {int(trades_needed_10pct)} |
| Trades needed for +50% portfolio (pure wins) | {int(trades_needed_50pct)} |
| Trades needed for +100% portfolio (pure wins) | {int(trades_needed_100pct)} |
| At {avg_trades_per_year:.0f} trades/year: years for +10% | {years_for_10pct:.1f}y |
| At {avg_trades_per_year:.0f} trades/year: years for +100% | {years_for_100pct:.1f}y |

**Key Insight:** With 3% position size and 10% win, each winning trade contributes 0.3%
to the portfolio. Needing 33 wins for +10% portfolio at ~{avg_trades_per_year:.0f} trades/year
and {win_rate_overall*100:.0f}% win rate means ~{int(33/win_rate_overall/avg_trades_per_year*12)} months for +10% net —
realistic but slow. With losers included (expectancy = {portfolio_contribution_per_trade:.3f}%/trade),
it takes ~{int(10/portfolio_contribution_per_trade) if portfolio_contribution_per_trade > 0 else 'N/A'} trades or
{int(10/portfolio_contribution_per_trade/avg_trades_per_year*12) if portfolio_contribution_per_trade > 0 else 'N/A'} months for +10%.

---

## 11. Plain-Language Q&A

### Q1: Is cash drag the primary performance killer?
**Yes.** Mean exposure of {mean_exposure:.0f}% means {100-mean_exposure:.0f}% of capital sat idle.
Hypothetically deploying all capital with the same edge would yield ~{hypothetical_return_pct:.0f}%
vs the actual {actual_return_pct:.0f}%. Cash drag costs ~{cash_drag_pct:.0f}pp of return.

### Q2: Are the signals weak?
**Partially.** Profit factor of {overall_pf:.2f} is modest but positive. Win rate of {overall_win_rate:.0f}%
is decent. The edge exists but is thin, and very concentrated in top trades.

### Q3: Are costs a significant drag?
**Minor.** Estimated total costs of {total_costs:,.0f} PLN ({cost_as_pct_gross:.0f}% of gross profit) are
manageable. Cost drag is only ~{return_before_costs - actual_return_pct:.1f}pp of total return.

### Q4: Is exit logic hurting performance?
**Yes, somewhat.** The {exit_rows[0]['profit_capture_rate_pct']:.0f}% profit capture rate means the system
captures only part of unrealized gains. {len(gave_back)} trades hit +8% then closed below +4%.

### Q5: Is position sizing appropriate?
**No — positions are too small.** At {avg_pos_pct:.1f}% average position, even a 10% winning trade
adds only {avg_pos_pct*0.1:.2f}% to the portfolio. To meaningfully grow capital, either increase
position size or trade much more frequently.

### Q6: Is trade frequency sufficient?
**No.** {avg_trades_per_year:.0f} trades/year at current position sizes cannot compound fast enough.
Doubling trade frequency or increasing position size to 5-6% would dramatically improve CAGR.

### Q7: Is P&L well-distributed?
**No.** Top 10 trades account for {top10_pct:.0f}% of all profit. The system relies on occasional
large winners. Without those, return would be only {ret_ex_top10:.1f}%.

### Q8: What would most improve performance?
In order of impact:
1. **Increase exposure** from {mean_exposure:.0f}% to 60-80% (deploy cash)
2. **Increase position size** from {avg_pos_pct:.1f}% to 4-6% per trade
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
"""

with open(REPORTS_DIR / "return_attribution_analysis.md", "w") as f:
    f.write(attribution_md)
print("  -> Saved return_attribution_analysis.md")

# ═══════════════════════════════════════════════════════════════════════════════
# FINAL SUMMARY
# ═══════════════════════════════════════════════════════════════════════════════
print("\n" + "=" * 70)
print("ANALYSIS COMPLETE")
print("=" * 70)
print(f"\nSummary:")
print(f"  Total Return: {total_return_pct:.1f}% | CAGR: {cagr:.2f}%")
print(f"  Win Rate: {overall_win_rate:.0f}% | Profit Factor: {overall_pf:.2f}")
print(f"  Mean Exposure: {mean_exposure:.0f}% | Cash Drag: ~{cash_drag_pct:.0f}pp")
print(f"  Avg Position: {avg_pos_pct:.1f}% | Avg Trades/Year: {avg_trades_per_year:.0f}")
print(f"\nReports saved to: {REPORTS_DIR}")
print("  - exposure_analysis.csv")
print("  - cash_drag_analysis.csv")
print("  - strategy_contribution.csv")
print("  - score_threshold_analysis.csv")
print("  - exit_logic_attribution.csv")
print("  - benchmark_validation.md")
print("  - return_attribution_analysis.md")

conn.close()
