# /kb-update — Knowledge Base Daily Increment

Runs the lightweight daily KB update locally. Appends today's bar to each ticker file in `kb/historical_bars/` and commits/pushes. Patterns are NOT recomputed (that runs Sundays during kb-refresh).

> Equivalent to the Mon-Fri 5:30 PM ET cloud routine. Runtime: 1–2 minutes.

## Run

```bash
python shark/run.py kb-update
```

Python handles everything: git pull → fetch latest 5 bars per ticker → merge with existing → trim to ~2 years → git commit + push.

## Dry Run (skip git push)

```bash
python shark/run.py kb-update --dry-run
```

## Prerequisites

The KB must be seeded first. If `kb/historical_bars/` is empty, the update will exit with an error message instructing you to run `/kb-refresh` first.

## On Error

```bash
tail -30 memory/error.log
```

Common issues:
- **"KB has no tickers — run kb-refresh first"** → bootstrap with `/kb-refresh` or `python scripts/seed_kb.py --commit`.
- **Git push rejected** → another routine pushed first; re-run will rebase.
