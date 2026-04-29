---
description: "Full trading pipeline: pre-execute validation → market-open execution (matches cloud trading routine)"
---
Run the consolidated trading pipeline locally. This matches the cloud `trading.md` routine.

## Step 1 — Pre-Execute Validation

```bash
python shark/run.py pre-execute
```

If it fails, check `memory/error.log`. Market-open will fall back to pre-market candidates.

## Step 2 — Market Open (full local path with AI analysis)

```bash
python shark/run.py market-open
```

This runs the full pipeline: guardrails → combined analyst (or debate if `SHARK_DEBATE_ROUNDS > 0`) → optional LLM risk debate (if `SHARK_LLM_RISK_REVIEW=true`) → bracket orders → outcome tracking → email.

## Dry Run (preview without placing real orders)

```bash
python shark/run.py pre-execute --dry-run && python shark/run.py market-open --dry-run
```

## Multi-Agent Config (optional env vars)

- `SHARK_DEBATE_ROUNDS=2` — enable N-round bull↔bear adversarial debate (0 = single-call legacy)
- `SHARK_LLM_RISK_REVIEW=true` — enable 3-way LLM risk debate after analyst decision
- `SHARK_RISK_DEBATE_ROUNDS=1` — risk debate rounds
- `SHARK_LLM_PROVIDER=anthropic` — LLM provider (anthropic/openai/google)
