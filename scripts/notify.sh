#!/usr/bin/env bash
# Shark Trading Agent — SendGrid email notification wrapper
# Usage: bash scripts/notify.sh "<subject>" "<body_text>"
# Falls back to local file if SENDGRID_API_KEY not set.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"
FALLBACK="$ROOT/memory/NOTIFICATIONS.md"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

subject="${1:-Shark Agent Notification}"
body="${2:-$(cat 2>/dev/null || echo 'No body provided')}"
stamp="$(date '+%Y-%m-%d %H:%M %Z')"

# Fallback: append to local file if SendGrid not configured
if [[ -z "${SENDGRID_API_KEY:-}" || -z "${NOTIFY_EMAIL:-}" ]]; then
  printf "\n---\n## %s — %s (fallback — SendGrid not configured)\n%s\n" \
    "$stamp" "$subject" "$body" >> "$FALLBACK"
  echo "[notify fallback] appended to memory/NOTIFICATIONS.md"
  exit 0
fi

FROM_EMAIL="${NOTIFY_FROM_EMAIL:-shark@trading.bot}"

payload="$(python3 -c "
import json, sys
subject = sys.argv[1]
body = sys.argv[2]
to_email = sys.argv[3]
from_email = sys.argv[4]
print(json.dumps({
  'personalizations': [{'to': [{'email': to_email}]}],
  'from': {'email': from_email, 'name': 'Shark Trading Agent'},
  'subject': subject,
  'content': [
    {'type': 'text/plain', 'value': body},
    {'type': 'text/html', 'value': '<pre style=\"font-family:monospace\">' + body.replace('\n','<br>') + '</pre>'},
  ]
}))
" "$subject" "$body" "$NOTIFY_EMAIL" "$FROM_EMAIL")"

curl -fsS -X POST https://api.sendgrid.com/v3/mail/send \
  -H "Authorization: Bearer $SENDGRID_API_KEY" \
  -H "Content-Type: application/json" \
  -d "$payload"

echo "[notify] Email sent to $NOTIFY_EMAIL: $subject"
