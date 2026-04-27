# /pre-market — Pre-Market Research

Runs the 6am pre-market research phase locally. Scans watchlist, detects regime + macro context, ranks by RS and sentiment, writes top candidates to DAILY-HANDOFF.md, sends research email.

## Run

```bash
python shark/run.py pre-market
```

Python handles everything: git pull → context briefing → Perplexity research → regime detection → RS ranking → RESEARCH-LOG.md → DAILY-HANDOFF.md → email → git commit + push.

## Dry Run (preview without side effects)

```bash
python shark/run.py pre-market --dry-run
```

## On Error

```bash
tail -30 memory/error.log
```

Read the error, diagnose the root cause, fix if possible, then re-run. Do not re-run blindly.
