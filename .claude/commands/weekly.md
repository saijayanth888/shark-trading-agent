---
description: "Weekly pipeline: review + backtest + KB refresh (matches cloud weekly routine)"
---
Run the consolidated Friday weekly pipeline locally. This matches the cloud `weekly.md` routine.

## Step 1 — Weekly Review

```bash
python shark/run.py weekly-review
```

Grades the week (A-F), computes alpha vs SPY, analyzes trade patterns.

## Step 2 — Backtest

```bash
python shark/run.py backtest
```

12-month simulation of current strategy. Generates BACKTEST-REPORT.md.

## Step 3 — KB Refresh

```bash
python shark/run.py kb-refresh
```

Full pattern recompute from bar data. Incremental pulls for fresh tickers, full for new/stale. ~3-5 min steady-state.

## Dry Run

```bash
python shark/run.py weekly-review --dry-run && python shark/run.py backtest --dry-run && python shark/run.py kb-refresh --dry-run
```

## On Error

```bash
tail -30 memory/error.log
```
