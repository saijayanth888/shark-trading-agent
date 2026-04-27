# /midday — Midday Portfolio Scan

Runs the 1pm position management phase locally. Evaluates all open positions for exits: hard stops (-7%), partial profits, time decay, thesis breaks, volatility expansion, regime shifts. Reviews closed trades via AI grader. Sends alert if action taken.

## Run

```bash
python shark/run.py midday
```

Python handles everything: git pull → context briefing → exit manager → hard stops → stop tightening → thesis checks → trade reviewer → TRADE-LOG.md → email if action → git commit + push.

## Dry Run (preview exit decisions without closing positions)

```bash
python shark/run.py midday --dry-run
```

## On Error

```bash
tail -30 memory/error.log
```

Check Alpaca positions manually before re-running to avoid duplicate closes.
