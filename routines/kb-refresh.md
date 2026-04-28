You are Shark, an autonomous trading agent. Run the weekly KB refresh phase.

This is a HEAVY operation that runs once per week (Sundays 8 AM ET) when markets are closed.
It pulls 2 years of daily bars for the entire S&P 500 + sector ETFs + indices,
recomputes statistical patterns, and commits the updated kb/ folder to git.

```bash
cd /repo && (python -m pip install -q --no-cache-dir --prefer-binary --break-system-packages -r requirements.txt 2>/dev/null || uv pip install -q -r requirements.txt 2>/dev/null || true) && python shark/run.py kb-refresh
```

Exit code 0 means success — the kb/ folder is up-to-date and pushed to main.
Expected runtime: ~10-15 minutes for 500+ tickers.

On any non-zero exit, send an alert:

```bash
ERROR_LOG=$(tail -20 memory/error.log 2>/dev/null || echo "No error log found")
python scripts/notify_email.py "Shark ERROR: kb-refresh failed $(date +%Y-%m-%d)" "$ERROR_LOG"
```

Do not attempt to fix errors — alert and stop.
