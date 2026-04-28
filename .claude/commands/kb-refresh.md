# /kb-refresh — Knowledge Base Weekly Rebuild

Runs the weekly KB rebuild locally. Incremental by default — full pulls only for new/stale tickers, deltas for fresh ones. Bars use `Adjustment.ALL` (split + dividend adjusted). Auto-detects legacy unadjusted KBs and triggers a one-time full re-pull.

Recomputes statistical patterns:

- `kb/patterns/calendar_effects.json` — day-of-week, FOMC drift
- `kb/patterns/sector_rotation.json` — 6-month sector momentum + top_3 / bottom_3 (used by pre-market scoring)
- `kb/patterns/regime_outcomes.json` — per-ticker stats by SPY regime
- `kb/patterns/ticker_base_rates.json` — per-ticker setup win rates
- `kb/patterns/anti_patterns.json` — auto-reject combos

Also prunes stale PEAD setup files (`kb/earnings/*.json` >90 days, no recorded outcomes).

> Equivalent to the Sunday 8 AM ET cloud routine. Steady-state runtime ~3-5 min; first-time / legacy upgrade ~10-15 min.

## Run

```bash
python shark/run.py kb-refresh
```

Python handles everything: git pull → S&P 500 list refresh → batch bar fetch → pattern extraction → git commit + push.

## Dry Run (skip git push)

```bash
python shark/run.py kb-refresh --dry-run
```

Local files are still written to `kb/`, just not pushed.

## First-Time Bootstrap

If this is the FIRST time and you want fine-grained control:

```bash
python scripts/seed_kb.py --commit          # full S&P 500
python scripts/seed_kb.py --max-tickers 50  # quick smoke test
python scripts/seed_kb.py --skip-patterns   # bars only, no patterns
```

## On Error

```bash
tail -30 memory/error.log
```

Common issues:
- **SSL cert errors** when fetching S&P 500 list → corporate proxy interference; cloud routines are unaffected.
- **Empty bars batches** → check `ALPACA_DATA_FEED` (free tier = `iex`, paid = `sip`).
- **Git push rejected** → another routine pushed first; re-run will rebase.
