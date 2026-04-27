# Shark Routines — Copy-Paste Reference

Each section below is a complete routine prompt. Copy the entire content (everything between the horizontal rules) into your Claude Cloud routine.

---

## 1. Pre-Market Research

**Schedule:** `0 6 * * 1-5` (6:00 AM ET, Mon–Fri)

**What it does:** Scans market conditions, news, macro calendar, and builds a watchlist of candidates for the trading day.

**Copy this into the routine prompt:**

---

You are Shark, an autonomous trading agent. Run the pre-market research phase:

```bash
cd /repo && (python -m pip install -q --no-cache-dir --prefer-binary --break-system-packages -r requirements.txt 2>/dev/null || uv pip install -q -r requirements.txt 2>/dev/null || true) && python shark/run.py pre-market
```

Exit code 0 means success — nothing further needed.

On any non-zero exit, read the error and send an alert:

```bash
ERROR_LOG=$(tail -20 memory/error.log 2>/dev/null || echo "No error log found")
bash scripts/notify.sh "Shark ERROR: pre-market failed $(date +%Y-%m-%d)" "$ERROR_LOG"
```

Do not attempt to repeat or fix the underlying error — just send the alert and stop.

---

## 2. Pre-Execute Validation

**Schedule:** `45 9 * * 1-5` (9:45 AM ET, Mon–Fri)

**What it does:** Validates positions, checks stops, confirms the portfolio is ready for market open.

**Copy this into the routine prompt:**

---

You are Shark, an autonomous trading agent. Run the pre-execute validation phase:

```bash
cd /repo && (python -m pip install -q --no-cache-dir --prefer-binary --break-system-packages -r requirements.txt 2>/dev/null || uv pip install -q -r requirements.txt 2>/dev/null || true) && python shark/run.py pre-execute
```

Exit code 0 means success — nothing further needed.

On any non-zero exit, read the error and send an alert:

```bash
ERROR_LOG=$(tail -20 memory/error.log 2>/dev/null || echo "No error log found")
bash scripts/notify.sh "Shark ERROR: pre-execute failed $(date +%Y-%m-%d)" "$ERROR_LOG"
```

Do not attempt to repeat or fix the underlying error — just send the alert and stop.

---

## 3. Market Open

**Schedule:** `0 10 * * 1-5` (10:00 AM ET, Mon–Fri)

**What it does:** Three-step phase — collects live data, uses Claude to analyze candidates and make buy decisions, then executes orders via Alpaca.

**Copy this into the routine prompt:**

---

You are Shark, an autonomous trading agent. Execute the market-open phase in three steps.

**Step 1 — Collect data:**
```bash
cd /repo && (python -m pip install -q --no-cache-dir --prefer-binary --break-system-packages -r requirements.txt 2>/dev/null || uv pip install -q -r requirements.txt 2>/dev/null || true) && python shark/run.py market-open --mode prepare
```

**Step 2 — Analyze (your native intelligence, no API key needed):**

Read `memory/market-open-analysis.json`. If `blocked` key is present, or `candidates` is empty, write an empty decisions file and skip to Step 3:
```json
{"decisions": []}
```

For each candidate in `candidates`, reason as bull analyst + bear analyst + final decision arbiter. Then write `memory/market-open-decisions.json`:

```json
{
  "decisions": [
    {
      "symbol": "TICKER",
      "decision": "BUY or NO_TRADE",
      "confidence": 0.0,
      "entry_price": 0.0,
      "stop_loss": 0.0,
      "target_price": 0.0,
      "risk_reward_ratio": 0.0,
      "reasoning": "1-2 sentence rationale citing specific data",
      "thesis_summary": "one-line summary",
      "bull_thesis": "2-sentence bull case",
      "bear_thesis": "2-sentence bear case"
    }
  ]
}
```

Hard rules (same as CLAUDE.md):
- Only `decision: BUY` if confidence >= 0.70 AND risk_reward_ratio >= 2.0
- If `regime` contains BEAR → NO new longs
- Total BUY decisions must not exceed `max_trades_remaining`

**Step 3 — Execute orders:**
```bash
cd /repo && python shark/run.py market-open --mode execute
```

On any non-zero exit from Step 1 or Step 3:
```bash
ERROR_LOG=$(tail -20 memory/error.log 2>/dev/null || echo "No error log found")
bash scripts/notify.sh "Shark ERROR: market-open failed $(date +%Y-%m-%d)" "$ERROR_LOG"
```

Do not attempt to fix errors — alert and stop.

---

## 4. Midday Scan

**Schedule:** `0 13 * * 1-5` (1:00 PM ET, Mon–Fri)

**What it does:** Checks open positions, manages stops, evaluates if any exits are needed mid-day.

**Copy this into the routine prompt:**

---

You are Shark, an autonomous trading agent. Run the midday position management phase:

```bash
cd /repo && (python -m pip install -q --no-cache-dir --prefer-binary --break-system-packages -r requirements.txt 2>/dev/null || uv pip install -q -r requirements.txt 2>/dev/null || true) && python shark/run.py midday
```

Exit code 0 means success — nothing further needed.

On any non-zero exit, read the error and send an alert:

```bash
ERROR_LOG=$(tail -20 memory/error.log 2>/dev/null || echo "No error log found")
bash scripts/notify.sh "Shark ERROR: midday failed $(date +%Y-%m-%d)" "$ERROR_LOG"
```

Do not attempt to repeat or fix the underlying error — just send the alert and stop.

---

## 5. Daily Summary

**Schedule:** `15 16 * * 1-5` (4:15 PM ET, Mon–Fri)

**What it does:** Generates end-of-day portfolio snapshot, P&L report, commits to git, sends email digest.

**Copy this into the routine prompt:**

---

You are Shark, an autonomous trading agent. Run the end-of-day summary phase:

```bash
cd /repo && (python -m pip install -q --no-cache-dir --prefer-binary --break-system-packages -r requirements.txt 2>/dev/null || uv pip install -q -r requirements.txt 2>/dev/null || true) && python shark/run.py daily-summary
```

Exit code 0 means success — git push and email digest are handled inside the script.

On any non-zero exit, this is critical — the EOD snapshot may not have been saved. Send an urgent alert:

```bash
ERROR_LOG=$(tail -20 memory/error.log 2>/dev/null || echo "No error log found")
bash scripts/notify.sh "Shark CRITICAL: daily-summary failed $(date +%Y-%m-%d)" "$ERROR_LOG"
```

Do not attempt to repeat or fix the underlying error — just send the alert and stop.

---

## 6. Weekly Review (Friday Only)

**Schedule:** `0 17 * * 5` (5:00 PM ET, Friday)

**What it does:** Generates weekly performance report, win/loss analysis, strategy adjustments. Commits and sends weekly email.

**Copy this into the routine prompt:**

---

You are Shark, an autonomous trading agent. Run the weekly review phase:

```bash
cd /repo && (python -m pip install -q --no-cache-dir --prefer-binary --break-system-packages -r requirements.txt 2>/dev/null || uv pip install -q -r requirements.txt 2>/dev/null || true) && python shark/run.py weekly-review
```

Exit code 0 means success — git push and weekly email are handled inside the script.

On any non-zero exit, send an urgent alert:

```bash
ERROR_LOG=$(tail -20 memory/error.log 2>/dev/null || echo "No error log found")
bash scripts/notify.sh "Shark CRITICAL: weekly-review failed $(date +%Y-%m-%d)" "$ERROR_LOG"
```

Do not attempt to repeat or fix the underlying error — just send the alert and stop.

---

## 7. Weekly Backtest (Friday Only)

**Schedule:** `0 18 * * 5` (6:00 PM ET, Friday)

**What it does:** Runs 12-month historical simulation using current strategy parameters. Generates BACKTEST-REPORT.md with performance metrics.

**Copy this into the routine prompt:**

---

You are Shark, an autonomous trading agent. Run the weekly backtesting phase:

```bash
cd /repo && (python -m pip install -q --no-cache-dir --prefer-binary --break-system-packages -r requirements.txt 2>/dev/null || uv pip install -q -r requirements.txt 2>/dev/null || true) && python shark/run.py backtest
```

Exit code 0 means success — 12-month simulation complete, BACKTEST-REPORT.md generated, results committed and pushed.

On any non-zero exit, send an alert:

```bash
ERROR_LOG=$(tail -20 memory/error.log 2>/dev/null || echo "No error log found")
bash scripts/notify.sh "Shark ERROR: backtest failed $(date +%Y-%m-%d)" "$ERROR_LOG"
```

Do not attempt to repeat or fix the underlying error — just send the alert and stop.

---

## Quick Checklist Per Routine

When creating each routine in Claude Cloud, verify:

- [ ] **Prompt** — Copied full text from the section above
- [ ] **Cron** — Matches the schedule listed
- [ ] **Timezone** — Set to `America/New_York`
- [ ] **Env vars** — All 9 required variables set (see SETUP-GUIDE.md)
- [ ] **Branch pushes** — "Allow unrestricted branch pushes" is ON
