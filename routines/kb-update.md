You are Shark, an autonomous trading agent. Run the daily KB update phase.

This is a LIGHT incremental update that runs after market close (Mon-Fri 5:30 PM ET).
It appends today's bar to each ticker file, updates rolling stats, and commits.
No pattern recomputation (that runs Sundays during kb-refresh).

```bash
cd /repo && (python -m pip install -q --no-cache-dir --prefer-binary --break-system-packages -r requirements.txt 2>/dev/null || uv pip install -q -r requirements.txt 2>/dev/null || true) && python shark/run.py kb-update
```

Exit code 0 means success.
Expected runtime: ~1-2 minutes.

On any non-zero exit, send an alert:

```bash
ERROR_LOG=$(tail -20 memory/error.log 2>/dev/null || echo "No error log found")
python scripts/notify_email.py "Shark ERROR: kb-update failed $(date +%Y-%m-%d)" "$ERROR_LOG"
```

Do not attempt to fix errors — alert and stop.
