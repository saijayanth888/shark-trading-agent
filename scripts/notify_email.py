#!/usr/bin/env python3
"""
Shark Trading Agent — CLI email notification using Gmail REST API.

Usage (from routines):
    python scripts/notify_email.py "Subject line" "Body text"

This replaces scripts/notify.sh for cloud sandbox environments where
Gmail SMTP (port 587) is blocked. Uses the same send_email_digest()
pipeline as all other Shark phases (Gmail API → Resend → SMTP → file).

Environment variables required:
    GMAIL_OAUTH_CLIENT_ID, GMAIL_OAUTH_CLIENT_SECRET, GMAIL_OAUTH_REFRESH_TOKEN,
    NOTIFY_FROM_EMAIL, NOTIFY_EMAIL
"""

import os
import sys
from pathlib import Path

# Ensure repo root is on sys.path so `shark` package resolves
_REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO_ROOT))

# Load .env for local dev (cloud injects env vars directly)
_env_path = _REPO_ROOT / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())

_FALLBACK_LOG = _REPO_ROOT / "memory" / "NOTIFICATIONS.md"


def main() -> None:
    subject = sys.argv[1] if len(sys.argv) > 1 else "Shark Agent Notification"
    body = sys.argv[2] if len(sys.argv) > 2 else "No body provided"

    from shark.signals.distributor import send_email_digest
    from shark.signals.templates import alert_html

    html = alert_html(title=subject, message=body, severity="danger")
    sent = send_email_digest(subject=subject, body_html=html)

    if sent:
        print(f"[notify_email] Email sent: {subject}")
    else:
        # Always write to fallback file so the alert isn't lost
        stamp = __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M %Z")
        _FALLBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _FALLBACK_LOG.open("a", encoding="utf-8") as f:
            f.write(f"\n---\n## {stamp} — {subject}\n{body}\n")
        print(f"[notify_email] Fallback written to memory/NOTIFICATIONS.md: {subject}")


if __name__ == "__main__":
    main()
