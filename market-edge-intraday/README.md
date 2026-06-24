# Intraday Paper Trading — market-edge-intraday

A research tool that **pretends** to trade US stocks during the day, using
**virtual money only**, so you can study whether a set of trading ideas would
have made or lost money — without ever risking a single real dollar.

> **PAPER TRADING ONLY.** This system never connects to a real brokerage
> account and never sends a real order. Nothing here can buy or sell anything
> with real money.

---

## 1. What this system is

It is an automated "trading simulator + lab notebook". It:

- watches the US stock market during the day (15‑minute snapshots),
- looks for a handful of well‑known intraday patterns,
- scores each opportunity from 0 to 100,
- "buys" and "sells" the good ones with **make‑believe money**,
- records every decision so you can review the results later in a dashboard.

You can run it in two ways:

- **Backtest** — replay past market data to see how the ideas would have done.
- **Live paper** — follow today's market in real time, still with fake money.

## 2. What this system does NOT do

- ❌ It does **not** place real orders or touch a real broker account.
- ❌ It does **not** use leverage (no borrowing to trade bigger).
- ❌ It does **not** trade options, futures, CFDs, forex or crypto.
- ❌ It does **not** trade overnight — every position is closed before the
  market closes the same day.
- ❌ It is **not** financial advice and does **not** guarantee profit.

It only trades regular US stocks and ETFs, long‑only (it buys, then sells), with
imaginary capital.

## 3. How one trade works (plain English)

1. During the day the system looks at each stock in its watchlist.
2. When a stock matches a pattern (for example, it breaks above its opening
   range on strong volume), the system creates a **signal** and scores it.
3. If the score is high enough and the risk rules allow it, the system "buys"
   a position on the **next** bar's opening price (never the same bar — see the
   no‑look‑ahead rule).
4. It immediately sets a **stop‑loss** (where it gives up if wrong) and a
   **target** (where it takes profit).
5. The position is watched bar by bar. It closes when the stop or target is hit,
   when the idea is invalidated, or at the **end of the day** at the latest.
6. The result (profit or loss, in PLN) is saved.

## 4. What "paper trading" means

Paper trading = practising with fake money on real prices. The prices are real
and current, but the buys and sells are simulated inside this program. It is the
safe way to test a strategy before ever considering real money. **This project
only ever paper trades.**

## 5. How to get an API key (free)

The system needs permission to **read** market prices. It uses Alpaca's free
data:

1. Go to **https://alpaca.markets** and **sign up** for a free account.
2. Open your **dashboard**.
3. Go to **Paper Trading → API Keys** and click **Generate**.
4. You will see two values: an **API Key ID** and a **Secret Key**. Copy both.

The free "IEX" data feed is enough for this research system.

## 6. Where to paste the API key

In the project folder there is a file called `.env.example`. Make a copy named
`.env` and paste your two keys into these lines:

```
ALPACA_API_KEY=your_key_id_here
ALPACA_SECRET_KEY=your_secret_key_here
```

Save the file. That's it — the keys never leave your computer.

## 7. How to install Docker

Docker runs the whole system for you so you don't have to install Python, a
database, etc.

- **Windows / Mac:** download **Docker Desktop** from
  https://www.docker.com/products/docker-desktop and install it like any app.
- **Linux:** follow https://docs.docker.com/engine/install/ for your distro.

After installing, open Docker Desktop once so it is running.

## 8. How to run the project

Open a terminal in the project folder and run:

```bash
cp .env.example .env      # then edit .env to add your API keys (step 6)
docker compose up -d      # starts the database, worker, API and dashboard
```

The first run downloads and builds things, so give it a few minutes.

## 9. How to run a backtest

```bash
docker compose run --rm worker python scripts/run_backtest.py \
    --start 2026-01-01 --end 2026-03-31 \
    --symbols AAPL,MSFT,NVDA --cost-scenario BASE
```

This replays history and writes results into the `reports/` folder. If you have
no API key and no downloaded data, it stops and tells you — it will **never make
up fake results**.

## 10. How to run live paper trading

```bash
docker compose up -d worker
```

The worker follows the live market during US trading hours and paper‑trades
automatically. (It is also the default service, so plain `docker compose up -d`
already starts it.)

## 11. How to open the dashboard

Open your web browser at:

```
http://localhost:8501
```

You'll see equity, open positions, signals, trades, risk and system health. Use
the **RUN MODE** switch at the top left to flip between **Backtest** and
**Live Paper** views.

## 12. How to stop the system

```bash
docker compose down
```

This stops everything. Your data and reports are kept.

## 13. How to reset the (paper) account

To wipe the live‑paper history and start fresh:

```bash
docker compose run --rm worker python scripts/reset_paper_account.py --yes
```

Add `--all` if you also want to clear backtest data.

## 14. How to check for errors

- In the dashboard open the **System Health** page — it shows database status,
  data freshness, the worker heartbeat, and recent errors/warnings.
- From the terminal: `docker compose logs -f worker` shows live logs.
- A quick health check: `docker compose run --rm worker python scripts/health_check.py`.

## 15. How to read the metrics

- **Win rate** — share of trades that made money. 55% means 55 of every 100
  trades were winners. (A high win rate alone does not mean profit.)
- **Profit factor (PF)** — total profit divided by total loss. Above **1.0**
  means more money won than lost; **1.5+** is encouraging, **2.0+** is strong.
- **Expectancy** — the average result per trade, often shown in "R" (multiples
  of the amount risked). +0.2R means each trade on average earns 0.2× what it
  risked.
- **Max drawdown** — the worst peak‑to‑valley drop in the account. Smaller is
  better; it tells you how painful the bad stretches were.
- **Sharpe ratio** — return compared to how bumpy the ride was. Higher is
  better; roughly, above 1 is decent, above 2 is very good.

## 16. Why one month of results is not enough

A single month can be lucky or unlucky. A strategy can look brilliant in a calm,
rising market and fall apart in a choppy one. To trust a result you want **many
months across different market conditions**, plus **out‑of‑sample** and
**walk‑forward** checks (testing on data the strategy was not tuned on). Short
samples fool everyone.

## 17. Why this system does NOT guarantee profit

Markets change. Past patterns can stop working. Costs, slippage and bad luck eat
into results. This is a **research tool** to study ideas honestly — including
finding out that an idea does **not** work. It makes no promise of profit, and
because it is paper‑only, it cannot lose (or make) real money.

---

## Quick start (cheat sheet)

```bash
cp .env.example .env          # add ALPACA_API_KEY + ALPACA_SECRET_KEY
docker compose up -d          # start everything
open http://localhost:8501    # dashboard
docker compose down           # stop everything
```

---

## Architecture overview

```
                +-------------------+
   market data  |  Data provider    |  (Alpaca / Massive, READ-ONLY)
   (real prices)|  app/data         |
                +---------+---------+
                          |
                          v
   +----------------------------------------------+
   |  Universe builder (app/universe)             |  picks ~500 liquid stocks
   +----------------------------------------------+
                          |
                          v
   +----------------------------------------------+
   |  Strategies (app/strategies) -> signals      |  patterns + 0-100 score
   +----------------------------------------------+
                          |
                          v
   +----------------------------------------------+
   |  Risk manager (app/risk) -> sizing & limits  |  daily loss, exposure, etc.
   +----------------------------------------------+
                          |
                          v
   +----------------------------------------------+
   |  Execution / paper broker (app/execution)    |  simulated fills + costs
   +----------------------------------------------+
                          |
        +-----------------+------------------+
        v                                    v
+---------------------+            +-------------------------+
| Backtest engine     |            | Live paper engine       |
| app/backtest        |            | app/engine (APScheduler)|
+----------+----------+            +-----------+-------------+
           |                                   |
           +----------------+------------------+
                            v
                 +---------------------+
                 |  PostgreSQL (UTC)   |  trades, signals, positions,
                 |  app/models         |  snapshots, events, heartbeats
                 +----------+----------+
                            |
            +---------------+----------------+
            v                                v
   +-----------------+              +-------------------+
   | FastAPI (app/api)|             | Streamlit dashboard|
   | /health, data    |             | dashboard/         |
   +-----------------+              +-------------------+
```

The **same** strategy, risk and broker code is used by both the backtest and the
live‑paper engine, so what you test is what you run.

---

## How signals are scored (0–100)

Each candidate signal is scored from **0 to 100**. Every dimension produces a
value between 0 and 1, is multiplied by its weight (the weights add up to 100),
and the results are summed — so 100 is the maximum. A weak dimension simply
contributes 0 for its share, which is how points are effectively "subtracted".
The exact logic lives in `app/strategies/scoring.py`.

| Dimension | Max points | Rewards |
|---|:---:|---|
| Liquidity | 12 | high dollar volume / easy to enter & exit |
| Spread | 8 | tight (estimated) bid/ask gap |
| Relative volume | 14 | current bar volume well above its 20-bar average |
| SPY trend alignment | 12 | broad market agrees with the trade direction |
| Relative strength | 12 | stock outperforming SPY and its sector |
| Candle quality | 10 | clean bar, strong close location |
| Distance from VWAP | 8 | sensible distance from VWAP (mean reversion rewards being far *below* VWAP; momentum rewards being above but not over-extended) |
| Reward / risk | 12 | higher reward versus risk |
| Time of day | 6 | earlier in the allowed entry window |
| Current volatility | 6 | enough range to move, not chaotic |
| **Total** | **100** | |

**Default threshold:** `MIN_SIGNAL_SCORE = 75`. Only signals scoring **75 or
above** are considered for entry. Backtests **sweep** the threshold across
**65 / 70 / 75 / 80 / 85** to see how sensitive results are to it.
