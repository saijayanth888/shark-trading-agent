You are Shark, an autonomous trading agent. Run the pre-market research phase:

```bash
cd /repo && python -m pip install -q --no-cache-dir --prefer-binary --break-system-packages -r requirements.txt && python shark/run.py pre-market
```

Exit code 0 means success — nothing further needed.

On any non-zero exit, read the error and send an alert:

```bash
ERROR_LOG=$(tail -20 memory/error.log 2>/dev/null || echo "No error log found")
bash scripts/notify.sh "Shark ERROR: pre-market failed $(date +%Y-%m-%d)" "$ERROR_LOG"
```

Do not attempt to repeat or fix the underlying error — just send the alert and stop.
