"""
Signal Distribution — sends trade alert emails via Gmail SMTP.

Requires a Gmail App Password (not your main Gmail password).
Setup: Google Account → Security → 2-Step Verification → App Passwords → create one.

Environment variables:
    NOTIFY_FROM_EMAIL  — the Gmail address to send FROM (e.g. sharkwaveai@gmail.com)
    GMAIL_APP_PASSWORD — 16-character App Password from Google Account settings
    NOTIFY_EMAIL       — the address to send alerts TO (can be same as FROM)
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
_FALLBACK_LOG = Path(__file__).resolve().parents[2] / "memory" / "SIGNAL-LOG.md"


def send_email_digest(subject: str, body_html: str) -> bool:
    """Send an HTML email via Gmail SMTP.

    Returns True on success, False on failure (also writes fallback to SIGNAL-LOG.md).
    """
    from_email = os.environ.get("NOTIFY_FROM_EMAIL", "")
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "")
    to_email = os.environ.get("NOTIFY_EMAIL", "")

    if not from_email or not app_password or not to_email:
        missing = [k for k, v in {
            "NOTIFY_FROM_EMAIL": from_email,
            "GMAIL_APP_PASSWORD": app_password,
            "NOTIFY_EMAIL": to_email,
        }.items() if not v]
        logger.warning("Email skipped — missing env vars: %s", missing)
        _write_fallback(subject, body_html)
        return False

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_email
    msg["To"] = to_email
    msg.attach(MIMEText(body_html, "html"))

    try:
        with smtplib.SMTP(_GMAIL_SMTP_HOST, _GMAIL_SMTP_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(from_email, app_password)
            smtp.sendmail(from_email, to_email, msg.as_string())
        logger.info("Email sent — subject=%r to=%s", subject, to_email)
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error(
            "Gmail auth failed for %s — check GMAIL_APP_PASSWORD is a valid App Password "
            "(not your Gmail login password). Generate one at: "
            "myaccount.google.com/apppasswords", from_email
        )
        _write_fallback(subject, body_html)
        return False
    except Exception as exc:
        logger.error("Email send failed: %s", exc)
        _write_fallback(subject, body_html)
        return False


def _write_fallback(subject: str, body_html: str) -> None:
    """Append the signal to SIGNAL-LOG.md when email is unavailable."""
    try:
        _FALLBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
        with _FALLBACK_LOG.open("a", encoding="utf-8") as f:
            f.write(f"\n## {subject}\n{body_html}\n")
        logger.info("Signal written to fallback log: %s", _FALLBACK_LOG)
    except Exception as exc:
        logger.error("Fallback log write failed: %s", exc)
