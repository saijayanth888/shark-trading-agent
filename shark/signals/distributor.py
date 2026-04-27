"""
Signal Distribution — sends HTML emails via Gmail SMTP or Resend HTTP API.

Transport priority (first available wins):
  1. Resend HTTP API  — set RESEND_API_KEY + RESEND_FROM_EMAIL (works in all cloud envs)
  2. Gmail SMTP       — set GMAIL_APP_PASSWORD + NOTIFY_FROM_EMAIL (blocked in some sandboxes)
  3. SIGNAL-LOG.md    — always available fallback; committed to git with each phase

Environment variables:
  NOTIFY_FROM_EMAIL   — sender address (Gmail for SMTP, verified domain for Resend)
  NOTIFY_EMAIL        — recipient address
  GMAIL_APP_PASSWORD  — 16-char Gmail App Password (spaces stripped automatically)
  RESEND_API_KEY      — optional; if set, Resend is tried before SMTP
  RESEND_FROM_EMAIL   — optional; sender for Resend (must be verified in Resend dashboard)
"""

import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger(__name__)

_GMAIL_SMTP_HOST = "smtp.gmail.com"
_GMAIL_SMTP_PORT = 587
_SMTP_TIMEOUT = 10  # fail fast in sandboxes that block TCP sockets
_FALLBACK_LOG = Path(__file__).resolve().parents[2] / "memory" / "SIGNAL-LOG.md"


def send_email_digest(subject: str, body_html: str) -> bool:
    """
    Send an HTML email. Tries Resend → Gmail SMTP → SIGNAL-LOG.md fallback.
    Returns True only if a real email was delivered.
    """
    to_email = os.environ.get("NOTIFY_EMAIL", "")
    if not to_email:
        logger.warning("Email skipped — NOTIFY_EMAIL not set")
        _write_fallback(subject, body_html)
        return False

    if _try_resend(subject, body_html, to_email):
        return True

    if _try_smtp(subject, body_html, to_email):
        return True

    logger.warning("All email transports failed — writing to SIGNAL-LOG.md")
    _write_fallback(subject, body_html)
    return False


# ---------------------------------------------------------------------------
# Resend HTTP transport (works in all cloud envs — uses HTTPS port 443)
# ---------------------------------------------------------------------------

def _try_resend(subject: str, body_html: str, to_email: str) -> bool:
    api_key = os.environ.get("RESEND_API_KEY", "")
    from_email = os.environ.get("RESEND_FROM_EMAIL", os.environ.get("NOTIFY_FROM_EMAIL", ""))

    if not api_key:
        return False

    import json as _json
    import time
    import urllib.request

    payload = _json.dumps({
        "from": f"Shark Trading Agent <{from_email}>",
        "to": [to_email],
        "subject": subject,
        "html": body_html,
    }).encode()

    for attempt in range(1, 4):  # 3 attempts
        try:
            req = urllib.request.Request(
                "https://api.resend.com/emails",
                data=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                if resp.status in (200, 201):
                    logger.info("Email sent via Resend — subject=%r to=%s", subject, to_email)
                    return True
                logger.warning("Resend returned HTTP %s", resp.status)
        except Exception as exc:
            logger.warning("Resend attempt %d/3 failed: %s", attempt, exc)
            if attempt < 3:
                time.sleep(1.5 * attempt)

    return False


# ---------------------------------------------------------------------------
# Gmail SMTP transport
# ---------------------------------------------------------------------------

def _try_smtp(subject: str, body_html: str, to_email: str) -> bool:
    from_email = os.environ.get("NOTIFY_FROM_EMAIL", "")
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "")

    if not from_email or not app_password:
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Shark Trading Agent <{from_email}>"
    msg["To"] = to_email
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(_GMAIL_SMTP_HOST, _GMAIL_SMTP_PORT, timeout=_SMTP_TIMEOUT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(from_email, app_password)
            smtp.sendmail(from_email, to_email, msg.as_string())
        logger.info("Email sent via Gmail SMTP — subject=%r to=%s", subject, to_email)
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error(
            "Gmail auth failed for %s — verify GMAIL_APP_PASSWORD at myaccount.google.com/apppasswords",
            from_email,
        )
    except OSError as exc:
        logger.warning("Gmail SMTP socket blocked (sandbox): %s", exc)
    except Exception as exc:
        logger.warning("Gmail SMTP failed: %s", exc)

    return False


# ---------------------------------------------------------------------------
# File fallback
# ---------------------------------------------------------------------------

def _write_fallback(subject: str, body_html: str) -> None:
    """Append signal to SIGNAL-LOG.md when all email transports fail."""
    try:
        _FALLBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _FALLBACK_LOG.open("a", encoding="utf-8") as f:
            f.write(f"\n## {subject}\n{body_html}\n")
        logger.info("Signal written to fallback: %s", _FALLBACK_LOG.name)
    except Exception as exc:
        logger.error("Fallback log write failed: %s", exc)
