# /weekly-review — Weekly Performance Review

Runs the Friday 5pm weekly review phase locally. Grades the week (A-F), computes alpha vs SPY, analyzes trade patterns, optionally adjusts strategy parameters, sends weekly digest email.

## Run

```bash
python shark/run.py weekly-review
```

Python handles everything: git pull → context briefing → P&L calculation → alpha vs SPY → pattern stats → WEEKLY-REVIEW.md → TRADING-STRATEGY.md update if mutation warranted → email → git commit + push.

## Dry Run (preview without writing memory or sending email)

```bash
python shark/run.py weekly-review --dry-run
```

## On Error

```bash
tail -30 memory/error.log
```

Review error log, fix root cause, re-run. Check that WEEKLY-REVIEW.md was written before next Monday.
