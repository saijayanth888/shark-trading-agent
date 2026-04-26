#!/usr/bin/env bash
# Shark Trading Agent — Gmail SMTP email notification wrapper
# Usage: bash scripts/notify.sh "<subject>" "<body_text>"
# Falls back to local file if GMAIL_APP_PASSWORD not set.

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

# Fallback: append to local file if Gmail not configured
if [[ -z "${GMAIL_APP_PASSWORD:-}" || -z "${NOTIFY_EMAIL:-}" || -z "${NOTIFY_FROM_EMAIL:-}" ]]; then
  printf "\n---\n## %s — %s (fallback — Gmail not configured)\n%s\n" \
    "$stamp" "$subject" "$body" >> "$FALLBACK"
  echo "[notify fallback] appended to memory/NOTIFICATIONS.md"
  exit 0
fi

python3 - "$subject" "$body" "$NOTIFY_EMAIL" "$NOTIFY_FROM_EMAIL" "$GMAIL_APP_PASSWORD" <<'PYEOF'
import smtplib, sys
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

subject, body, to_email, from_email, app_password = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5]

msg = MIMEMultipart("alternative")
msg["Subject"] = subject
msg["From"]    = f"Shark Trading Agent <{from_email}>"
msg["To"]      = to_email
msg.attach(MIMEText(body, "plain"))
msg.attach(MIMEText(f'<pre style="font-family:monospace">{body}</pre>', "html"))

with smtplib.SMTP("smtp.gmail.com", 587) as smtp:
    smtp.ehlo()
    smtp.starttls()
    smtp.login(from_email, app_password)
    smtp.sendmail(from_email, to_email, msg.as_string())

print(f"[notify] Email sent to {to_email}: {subject}")
PYEOF
