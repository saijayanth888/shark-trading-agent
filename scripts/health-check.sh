#!/usr/bin/env bash
# Shark Trading Agent — API health check
# Usage: bash scripts/health-check.sh
# Exit code 0 = all APIs reachable and authenticated.
# Exit code 1 = one or more APIs failed.

set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

PASS=0
FAIL=0

ok()   { echo "[OK]   $1"; ((PASS++)); }
fail() { echo "[FAIL] $1"; ((FAIL++)); }

echo "=== Shark API Health Check ==="
echo ""

# ── Alpaca ────────────────────────────────────────────────────────────────
if [[ -z "${ALPACA_API_KEY:-}" || -z "${ALPACA_SECRET_KEY:-}" ]]; then
  fail "Alpaca — ALPACA_API_KEY or ALPACA_SECRET_KEY not set"
else
  BASE="${ALPACA_BASE_URL:-https://paper-api.alpaca.markets}"
  HTTP=$(curl -o /dev/null -s -w "%{http_code}" \
    -H "APCA-API-KEY-ID: $ALPACA_API_KEY" \
    -H "APCA-API-SECRET-KEY: $ALPACA_SECRET_KEY" \
    "$BASE/v2/account")
  if [[ "$HTTP" == "200" ]]; then
    ok "Alpaca ($BASE) — HTTP $HTTP"
  else
    fail "Alpaca ($BASE) — HTTP $HTTP (expected 200)"
  fi
fi

# ── Perplexity ───────────────────────────────────────────────────────────
if [[ -z "${PERPLEXITY_API_KEY:-}" ]]; then
  fail "Perplexity — PERPLEXITY_API_KEY not set"
else
  PAYLOAD='{"model":"sonar","messages":[{"role":"user","content":"ping"}],"max_tokens":1}'
  HTTP=$(curl -o /dev/null -s -w "%{http_code}" \
    -X POST https://api.perplexity.ai/chat/completions \
    -H "Authorization: Bearer $PERPLEXITY_API_KEY" \
    -H "Content-Type: application/json" \
    -d "$PAYLOAD")
  if [[ "$HTTP" == "200" ]]; then
    ok "Perplexity — HTTP $HTTP"
  else
    fail "Perplexity — HTTP $HTTP (expected 200)"
  fi
fi

# ── Anthropic ────────────────────────────────────────────────────────────
if [[ -z "${ANTHROPIC_API_KEY:-}" ]]; then
  fail "Anthropic — ANTHROPIC_API_KEY not set"
else
  HTTP=$(curl -o /dev/null -s -w "%{http_code}" \
    -H "x-api-key: $ANTHROPIC_API_KEY" \
    -H "anthropic-version: 2023-06-01" \
    "https://api.anthropic.com/v1/models")
  if [[ "$HTTP" == "200" ]]; then
    ok "Anthropic — HTTP $HTTP"
  else
    fail "Anthropic — HTTP $HTTP (expected 200)"
  fi
fi

# ── Gmail SMTP (config check only — no live connection) ──────────────────
if [[ -z "${GMAIL_APP_PASSWORD:-}" || -z "${NOTIFY_EMAIL:-}" || -z "${NOTIFY_FROM_EMAIL:-}" ]]; then
  fail "Gmail SMTP — one or more of GMAIL_APP_PASSWORD / NOTIFY_EMAIL / NOTIFY_FROM_EMAIL not set (notify will fall back to file)"
else
  ok "Gmail SMTP — credentials present (not tested live)"
fi

echo ""
echo "=== Results: $PASS passed, $FAIL failed ==="

[[ "$FAIL" -eq 0 ]]
