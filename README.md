# 📈 Polymarket Trading Bot

A local, read-only tool that scans Polymarket prediction markets, finds high-probability short-term opportunities, and displays them in a clean dashboard.

> **No real money is involved. This is paper-trading only.**

---

## What it does

- Pulls live market data from Polymarket (or uses realistic sample data if offline)
- Finds markets where YES prices sit between **0.87–0.95** and close within **24 hours**
- Scores each market using 8 weighted factors (liquidity, spread, time, objectivity, etc.)
- Ranks them **Tier A / B / C** with plain-English explanations
- Shows everything in a clean dark-mode dashboard

---

## Requirements

- **Python 3.11 or newer** — [Download here](https://www.python.org/downloads/)
- A terminal (Terminal on Mac, Command Prompt on Windows)
- Internet connection (optional — falls back to sample data)

---

## Quick Start (3 steps)

### Step 1 — Install (one time only)

Open Terminal, navigate to this folder, and run:

```bash
./setup.sh
```

This takes about 60 seconds and installs everything automatically.

---

### Step 2 — Open the Dashboard

```bash
./run.sh
```

Your browser will open at **http://localhost:8501** automatically.

---

### Step 3 — Scan for Opportunities

Inside the dashboard, click the **"🔍 Scan Markets"** button in the left sidebar.

Results appear instantly. If the Polymarket API is unavailable, sample data is used automatically — no errors, no crashes.

---

## Dashboard Pages

### 📊 Overview
The at-a-glance command centre. Shows two rows of live summary statistics, your top 5 opportunities, your top 5 watchlist markets, a breakdown of why markets were rejected, and score/price/timing distribution charts. Start here every time you open the dashboard.

### 🏆 Top Opportunities
The primary decision page. Shows only markets that passed every filter and scored above 55/100, sorted by final score. Click any row to open a full detail panel with score breakdown, penalty breakdown, and a plain-English verdict. Use the sidebar filters to narrow by score, tier, price range, timing, and keyword.

### 👀 Watchlist
Two sections:
- **My Watchlist** — markets you've manually bookmarked. Each shows exactly what needs to improve before it qualifies.
- **Near Misses** — rejected markets that came closest to passing. Worth monitoring daily.

### ❌ Rejected Markets
Every market fetched but not accepted, grouped by the primary rejection reason (price, liquidity, spread, timing, depth, ambiguity, score). Includes a donut chart of the rejection breakdown so you can see where the current market is weakest.

---

## Understanding Actions, Confidence, and Tiers

### Action Labels

| Label | What it means |
|-------|--------------|
| **Strong Candidate** | Tier A market — all key signals align. Highest priority. |
| **Watch Closely** | Tier B — solid setup with one or two minor reservations. Monitor frequently. |
| **Lower Quality** | Tier C — passes minimum filters but has notable weaknesses. Proceed with caution. |
| **Do Not Trade** | Failed one or more hard filters. Not recommended under any circumstances. |

### Confidence Levels

| Level | What it means |
|-------|--------------|
| **High** | Score ≥ 72, low penalties, strong liquidity and spread. All signals agree. |
| **Medium** | Score ≥ 58, moderate penalties. Worth watching but not a slam dunk. |
| **Low** | Barely above threshold. One adverse move could flip the outcome. |

### Tiers

| Tier | Score Range | Meaning |
|------|-------------|---------|
| 🥇 A | 75–100 | Top-quality setup. All scoring factors are strong. |
| 🥈 B | 60–74  | Good setup. One or two factors below ideal. |
| 🥉 C | 55–59  | Marginal. Meets the bar but not by much. |

### Watchlist Reasons

The "What Needs to Improve" column tells you the one or two specific things blocking a market from qualifying:

| Message | What to do |
|---------|-----------|
| Price must rise above 0.87 | Wait — the market probability needs to strengthen |
| Price must fall below 0.95 | Wait — too little upside at current probability |
| Liquidity must reach $10K+ | Wait for more participants to enter |
| Spread must tighten to ≤0.015 | Wait for market makers to compete more aggressively |
| Event is Xh away | Re-check when it enters the 24h window |
| More depth needed | Volume is low — price may not reflect true conviction |
| Ambiguity too high | Question wording is unclear — outcome could be disputed |

---

## Using the Terminal Scanner (optional)

You can also scan from the terminal without opening a browser:

```bash
./scan.sh
```

For more details (including rejected markets):

```bash
./scan.sh --verbose
```

See last scan summary:

```bash
source .venv/bin/activate
python cli.py status
```

---

## Understanding Scores

| Score | Tier | Meaning |
|-------|------|---------|
| 75–100 | 🥇 A | Strong opportunity — all signals align |
| 55–74  | 🥈 B | Worth watching — mostly positive signals |
| 0–54   | 🥉 C | Not recommended — one or more weaknesses |

### Score factors

| Factor | Weight | What it measures |
|--------|--------|-----------------|
| Liquidity | 22% | How much money is in the market |
| Spread | 18% | Gap between buy/sell price (smaller = better) |
| Objectivity | 18% | How clear and binary the question is |
| Time to end | 14% | Sweet spot is 4–16 hours to close |
| Price band | 10% | How centered in the 0.87–0.95 range |
| Depth | 8% | How actively the market is being traded |
| Stability | 6% | Price consistency proxy |
| Momentum | 4% | Recent trading volume |

Penalties can reduce scores for: market fragility (closing very soon), ambiguous questions, and extreme prices.

---

## Filter Criteria

A market must pass **all** of these to appear in Top Opportunities:

- YES price between **0.87 and 0.95**
- Event closes within **24 hours**
- Liquidity above **$5,000**
- 24h volume above **$1,000** (depth proxy)
- Spread below **0.020**

---

## Frequently Asked Questions

**Q: Does this trade real money?**
No. This tool is read-only. It never connects to any wallet or places any orders.

**Q: What if the internet is down?**
It automatically uses built-in sample data. Everything still works.

**Q: How often should I scan?**
Markets change quickly. Scanning every 30–60 minutes during active hours gives the best results.

**Q: What is a "spread"?**
The spread is the gap between what buyers are willing to pay and what sellers are asking. A tight spread (< 0.01) means the market is liquid and efficient.

**Q: Why does Tier A matter?**
Tier A markets have the best combination of liquidity, clear outcome, good timing, and tight pricing — they carry the least uncertainty.

---

## Auto Paper Trading — Bankroll Simulator

The bankroll simulator automatically takes paper positions in the top opportunities identified at each scan, tracks them through resolution, and compares three sizing models (2%, 3%, 5%) on identical entries.

> No real money. No wallet. No execution. Paper-trading only.

---

### How Auto Paper Entries Work

Every time you click **"Run Auto Paper Entries"** (or run `python cli.py run-auto-paper`), the system:

1. Fetches current live market data
2. Scores and filters all markets through the existing strategy engine
3. Selects the top accepted opportunities (ranked by score)
4. Simulates entering a position in each qualifying market
5. Simultaneously settles any previously open positions that have resolved

**Entry filters applied:**
- Market must be `accepted = true` (passed all scoring filters)
- No duplicate open position on the same market already exists
- Market must have at least **30 minutes** remaining before expiry
- Strict or Broad mode filter must pass (see below)

---

### How Bankroll Sizing Works

Three parallel sizing models run on every entry using the exact same markets:

| Model | Position Size | Risk Level |
|-------|--------------|------------|
| Conservative | 2% of current balance per trade | Lower risk, lower returns |
| Balanced | 3% of current balance per trade | Moderate |
| Aggressive | 5% of current balance per trade | Higher risk, higher upside |

Position size is calculated on the **current balance** at the time of entry (not starting balance), so sizing compounds over time with your performance.

---

### Entry Modes

| Mode | Which markets qualify |
|------|-----------------------|
| **Strict** | Tier A always + Tier B only if score ≥ 80. Default top limit: 5 |
| **Broad** | Tier A or Tier B with score ≥ 75. Default top limit: 10 |

Strict mode produces fewer, higher-quality trades. Broad mode generates more trades with slightly lower average quality. The training analytics page tells you which is performing better.

---

### What Live / Win / Loss / Unresolved Mean

| Status | What it means |
|--------|--------------|
| **live** | Position entered. Market not yet resolved. |
| **win** | Market resolved YES (price ≥ 0.99). P&L = notional × (1 − price) / price |
| **loss** | Market resolved NO (price ≤ 0.01). P&L = −notional |
| **unresolved** | Market expired but outcome unclear. P&L recorded as 0. |
| **skipped** | Entry attempted but blocked (duplicate, too close to expiry, etc.) |

---

### Quick Start — Auto Paper Trading

```bash
source .venv/bin/activate

# Run a scan and auto-enter positions
python cli.py run-auto-paper

# Check your bankroll across all 3 models
python cli.py show-bankroll

# See all open positions
python cli.py show-live-positions

# Settle resolved trades against latest prices
python cli.py settle-paper-trades

# Export training analytics report
python cli.py export-training-report
```

Or use the dashboard:
1. Open **💰 Bankroll Simulator** → click **"Run Auto Paper Entries"**
2. Open **📍 Live Positions** to see open and resolved trades
3. Open **🎓 What Winners Look Like** to see training analytics after trades settle

---

### How to Interpret Training Analytics

The **🎓 What Winners Look Like** page analyses all resolved trades and surfaces:

- **Win rate by tier** — which tier (A/B/C) is actually winning
- **Win rate by score bucket** — whether higher scores predict wins
- **Win rate by price band** — which price range (0.89–0.91, 0.92–0.94, etc.) performs best
- **Win rate by timing** — which hours-to-expiry window produces the best results
- **Win rate by confidence** — whether your confidence model is calibrated
- **Strict vs Broad** — which entry mode is producing better outcomes

The system generates a plain-English paragraph summarising the key patterns. Example output:

> "Across 24 resolved trades, the overall win rate is 71%. Winners average a score of 82 vs 71 for losers — higher-scoring markets are outperforming. The best-performing score range is 85–100 (83% win rate). Best performance comes from the 0.89–0.91 price band (80% win rate). Most losers come from later-timing setups (avg 14.2h to end). Strict mode (75% win rate) is outperforming broad mode (62%) — quality over quantity is paying off."

---

## How to Run the 48-Hour Validation Test

The validation mode runs a **48-hour paper-trading simulation** on real market data. It scans automatically every 15 minutes, enters positions, settles them when outcomes resolve, and tracks P&L across three position sizes (1%, 2%, 5%).

> No real money is involved. This is a paper simulation for strategy research only.

---

### Step 1 — Start the test

```bash
source .venv/bin/activate
python cli.py start-48h-test
```

Optional flags:
```bash
python cli.py start-48h-test --balance 5000 --position-pct 2 --max-positions 5
```

The test runs for 48 hours from the moment you start it. It survives restarts — state is saved to your local database automatically.

---

### Step 2 — Monitor live (recommended: dashboard)

Open the dashboard and navigate to the **🧪 48h Validation** page:

```bash
./run.sh
```

The page shows:
- Live P&L for all three sizing models (1%, 2%, 5%)
- Equity curves as positions settle
- Full trade log with entry/exit prices
- Win rates by tier, score bucket, price band, and timing
- A plain-English conclusion about your strategy's performance

The page auto-triggers a scan cycle if 15 minutes have elapsed since the last one — just keep it open or refresh occasionally.

---

### Step 3 — Monitor from the terminal (optional)

Check status and trigger a scan cycle from the command line:

```bash
python cli.py test-status
```

This shows a summary table across all three models and triggers a scan if one is due.

To check without scanning:
```bash
python cli.py test-status --no-scan
```

---

### Step 4 — Export results

At any point (or after the 48 hours are up), export a full report:

```bash
python cli.py export-48h-report
```

This creates in the `data/` folder:
- `trades_all_TIMESTAMP.csv` — every paper trade
- `trades_resolved_TIMESTAMP.csv` — won/lost trades only
- `trades_unresolved_TIMESTAMP.csv` — expired but unresolved
- `trades_open_TIMESTAMP.csv` — still open positions
- `validation_report_48h_TIMESTAMP.md` — full markdown report with all metrics

To export a specific run:
```bash
python cli.py export-48h-report --run-id 3
```

---

### Step 5 — Stop early (optional)

```bash
python cli.py stop-48h-test
```

The test also stops automatically after 48 hours.

---

### Monitoring checklist

| When | What to do |
|------|-----------|
| First 2 hours | Check that positions are being entered (trade log should fill up) |
| Every few hours | Glance at the equity curves — all three models should show activity |
| At 24h mark | Note win rate and whether P&L is positive or negative |
| At 48h | Click **Export Results** on the dashboard or run `export-48h-report` |

---

## Project Structure (for the curious)

```
├── app/
│   ├── dashboard.py        ← Streamlit UI (7 pages)
│   ├── scanner.py          ← Fetches data from Polymarket API
│   ├── strategy.py         ← Dual-band scoring and filtering
│   ├── simulation.py       ← Bankroll P&L simulation
│   ├── validation.py       ← 48-hour paper-trading engine
│   ├── validation_report.py← CSV + markdown report export
│   ├── database.py         ← Local SQLite persistence
│   ├── labels.py           ← Action labels, confidence, tags
│   ├── main.py             ← Module entry point (python -m app.main)
│   └── sample_data.py      ← Fallback data when API is unavailable
├── data/
│   └── markets.db          ← Created automatically on first scan
├── cli.py                  ← Terminal commands
├── setup.sh                ← One-time installer
├── run.sh                  ← Launches dashboard
└── scan.sh                 ← Terminal scan
```

---

## Troubleshooting

**"Permission denied" when running ./setup.sh**
```bash
chmod +x setup.sh run.sh scan.sh
./setup.sh
```

**"Python not found"**
Install Python 3.11 from https://python.org and try again.

**Dashboard won't open**
Make sure you ran `./setup.sh` first. Then try:
```bash
source .venv/bin/activate
streamlit run app/dashboard.py
```

**Markets show as sample data**
This is normal when the Polymarket API is temporarily unavailable. The data is realistic and the bot functions identically.

---

*This tool is for educational and research purposes only. Not financial advice.*
