# /pre-execute — Pre-Execute Validation

Runs the 9:45am validation phase locally. Takes candidates from pre-market DAILY-HANDOFF.md, validates with first 30 minutes of live data (volume, price action, news), writes validated symbols back to DAILY-HANDOFF.md for market-open.

## Run

```bash
python shark/run.py pre-execute
```

Python handles everything: git pull → context briefing → live quote validation → volume confirmation → DAILY-HANDOFF.md update → git commit + push.

## Dry Run (preview without writing handoff)

```bash
python shark/run.py pre-execute --dry-run
```

## On Error

```bash
tail -30 memory/error.log
```

If pre-execute fails, market-open will fall back to pre-market candidates directly.
