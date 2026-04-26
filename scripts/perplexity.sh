#!/usr/bin/env bash
# Shark Trading Agent — Perplexity research wrapper
# Usage: bash scripts/perplexity.sh "<query>"
# Exits with code 3 if PERPLEXITY_API_KEY not set — callers can fall back.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

query="${1:-}"
if [[ -z "$query" ]]; then
  echo "Usage: bash scripts/perplexity.sh \"<query>\"" >&2
  exit 1
fi

if [[ -z "${PERPLEXITY_API_KEY:-}" ]]; then
  echo "WARNING: PERPLEXITY_API_KEY not set. Caller should fall back to WebSearch." >&2
  exit 3
fi

MODEL="${PERPLEXITY_MODEL:-sonar-pro}"

# Build JSON payload safely using python
payload="$(python3 -c "
import json, sys
print(json.dumps({
  'model': sys.argv[1],
  'messages': [
    {'role': 'system', 'content': 'You are a precise financial research assistant. Cite every claim with sources. Be concise and factual.'},
    {'role': 'user', 'content': sys.argv[2]},
  ],
  'return_citations': True,
  'search_recency_filter': 'day',
}))
" "$MODEL" "$query")"

curl -fsS https://api.perplexity.ai/chat/completions \
  -H "Authorization: Bearer $PERPLEXITY_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$payload"

echo
