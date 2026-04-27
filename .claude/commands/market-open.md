# /market-open — Market Open Execution

Runs the 10am trade execution phase locally. Validates candidates from pre-execute, applies all gates (regime, macro, RS, ATR sizing, guardrails), places bracket orders, logs trades, sends trade alert email.

## Run

```bash
python shark/run.py market-open
```

Python handles everything: git pull → context briefing → position sizing → regime/macro/RS gates → guardrail checks → bracket orders → TRADE-LOG.md → email → git commit + push.

## Dry Run (preview without placing real orders)

```bash
python shark/run.py market-open --dry-run
```

## On Error

```bash
tail -30 memory/error.log
```

Read the error. If trades were partially placed, check Alpaca positions before re-running.
