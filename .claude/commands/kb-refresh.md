# /kb-refresh — Knowledge Base Weekly Rebuild

Runs the heavy weekly KB rebuild locally. Pulls 2 years of daily bars for the entire S&P 500 + sector ETFs + indices, recomputes statistical patterns (calendar effects, sector rotation, regime outcomes, anti-patterns), and commits/pushes the kb/ folder.

> Equivalent to the Sunday 8 AM ET cloud routine. Runtime: 10–15 minutes.

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
