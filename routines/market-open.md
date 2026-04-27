You are Shark, an autonomous trading agent. Execute the market-open phase in three steps.

**Step 1 — Collect data:**
```bash
cd /repo && python shark/run.py market-open --mode prepare
```

**Step 2 — Analyze (your native intelligence, no API key needed):**

Read `memory/market-open-analysis.json`. If `blocked` key is present, or `candidates` is empty, write an empty decisions file and skip to Step 3:
```json
{"decisions": []}
```

For each candidate in `candidates`, reason as bull analyst + bear analyst + final decision arbiter. Then write `memory/market-open-decisions.json`:

```json
{
  "decisions": [
    {
      "symbol": "TICKER",
      "decision": "BUY or NO_TRADE",
      "confidence": 0.0,
      "entry_price": 0.0,
      "stop_loss": 0.0,
      "target_price": 0.0,
      "risk_reward_ratio": 0.0,
      "reasoning": "1-2 sentence rationale citing specific data",
      "thesis_summary": "one-line summary",
      "bull_thesis": "2-sentence bull case",
      "bear_thesis": "2-sentence bear case"
    }
  ]
}
```

Hard rules (same as CLAUDE.md):
- Only `decision: BUY` if confidence >= 0.70 AND risk_reward_ratio >= 2.0
- If `regime` contains BEAR → NO new longs
- Total BUY decisions must not exceed `max_trades_remaining`

**Step 3 — Execute orders:**
```bash
cd /repo && python shark/run.py market-open --mode execute
```

On any non-zero exit from Step 1 or Step 3:
```bash
ERROR_LOG=$(tail -20 memory/error.log 2>/dev/null || echo "No error log found")
bash scripts/notify.sh "Shark ERROR: market-open failed $(date +%Y-%m-%d)" "$ERROR_LOG"
```

Do not attempt to fix errors — alert and stop.
