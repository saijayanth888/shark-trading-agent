# /daily-summary — End-of-Day Portfolio Snapshot

Runs the 4:15pm EOD snapshot phase locally. Calculates daily P&L, updates peak equity, checks circuit breaker, writes EOD entry to TRADE-LOG.md, sends portfolio digest email.

## Run

```bash
python shark/run.py daily-summary
```

Python handles everything: git pull → context briefing → account snapshot → circuit breaker check → peak equity update → TRADE-LOG.md → email → git commit + push.

## Dry Run (preview without writing memory or sending email)

```bash
python shark/run.py daily-summary --dry-run
```

## On Error

```bash
tail -30 memory/error.log
```

Daily summary failure means the EOD snapshot was not saved. Fix the error and re-run before end of day.
