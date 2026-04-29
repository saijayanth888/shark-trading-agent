---
description: "End-of-day pipeline: daily summary + KB update (matches cloud eod routine)"
---
Run the consolidated end-of-day pipeline locally. This matches the cloud `eod.md` routine.

## Step 1 — Daily Summary

```bash
python shark/run.py daily-summary
```

EOD snapshot, circuit breaker check, outcome resolution for closed trades, dashboard generation, email digest.

## Step 2 — KB Daily Update

```bash
python shark/run.py kb-update
```

Appends today's bars to each ticker file. ~1-2 min.

## Dry Run

```bash
python shark/run.py daily-summary --dry-run && python shark/run.py kb-update --dry-run
```

## On Error

```bash
tail -30 memory/error.log
```

Daily summary failure is critical — EOD snapshot may not be saved. Fix and re-run before end of day.
