"""
Signal Distribution — sends HTML emails via Gmail REST API, Resend, or SMTP.

Transport priority (first available wins):
  1. Gmail REST API   — set GMAIL_OAUTH_REFRESH_TOKEN + GMAIL_OAUTH_CLIENT_ID +
                         GMAIL_OAUTH_CLIENT_SECRET  (HTTPS port 443, works everywhere)
  2. Resend HTTP API  — set RESEND_API_KEY + RESEND_FROM_EMAIL (HTTPS, works everywhere)
  3. Gmail SMTP       — set GMAIL_APP_PASSWORD + NOTIFY_FROM_EMAIL (port 587, blocked in sandboxes)
  4. SIGNAL-LOG.md    — always available fallback; committed to git with each phase

Environment variables:
  NOTIFY_FROM_EMAIL          — sender Gmail address (used by API + SMTP)
  NOTIFY_EMAIL               — recipient address
  GMAIL_OAUTH_CLIENT_ID      — Google Cloud OAuth2 client ID
  GMAIL_OAUTH_CLIENT_SECRET  — Google Cloud OAuth2 client secret
  GMAIL_OAUTH_REFRESH_TOKEN  — long-lived OAuth2 refresh token (obtained once via browser)
  GMAIL_APP_PASSWORD         — 16-char Gmail App Password (SMTP fallback only)
  RESEND_API_KEY             — optional; Resend transport
  RESEND_FROM_EMAIL          — optional; sender for Resend
"""

import logging
import os
import socket
import smtplib
import urllib.error
import urllib.request
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
    Send an HTML email.
    Tries: Gmail REST API → Resend → Gmail SMTP → SIGNAL-LOG.md fallback.
    Returns True only if a real email was delivered.
    """
    to_email = os.environ.get("NOTIFY_EMAIL", "")
    if not to_email:
        logger.warning("Email skipped — NOTIFY_EMAIL not set")
        _write_fallback(subject, body_html)
        return False

    if _try_gmail_api(subject, body_html, to_email):
        return True

    if _try_resend(subject, body_html, to_email):
        return True

    if _try_smtp(subject, body_html, to_email):
        return True

    logger.warning("All email transports failed — writing to SIGNAL-LOG.md")
    _write_fallback(subject, body_html)
    return False


# ---------------------------------------------------------------------------
# Gmail REST API transport (HTTPS port 443 — works in all environments)
# ---------------------------------------------------------------------------

_GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"


def _ipv4_urlopen(req, **kwargs):
    """urlopen wrapper that forces IPv4 — some cloud sandboxes block IPv6."""
    import http.client
    import ssl

    class _IPv4HTTPSConnection(http.client.HTTPSConnection):
        def connect(self):
            self.sock = socket.create_connection(
                (self.host, self.port),
                timeout=self.timeout,
                source_address=self.source_address,
            )
            ctx = self._context or ssl.create_default_context()
            self.sock = ctx.wrap_socket(self.sock, server_hostname=self.host)

    class _IPv4HTTPSHandler(urllib.request.HTTPSHandler):
        def https_open(self, req):
            return self.do_open(_IPv4HTTPSConnection, req)

    opener = urllib.request.build_opener(_IPv4HTTPSHandler)
    return opener.open(req, **kwargs)


def _try_gmail_api(subject: str, body_html: str, to_email: str) -> bool:
    """Send email via Gmail REST API using OAuth2 refresh token.

    This uses HTTPS (port 443) so it works in cloud sandboxes where
    SMTP (port 587) is blocked.
    """
    client_id = os.environ.get("GMAIL_OAUTH_CLIENT_ID", "")
    client_secret = os.environ.get("GMAIL_OAUTH_CLIENT_SECRET", "")
    refresh_token = os.environ.get("GMAIL_OAUTH_REFRESH_TOKEN", "")
    from_email = os.environ.get("NOTIFY_FROM_EMAIL", "")

    if not all([client_id, client_secret, refresh_token, from_email]):
        return False

    import base64
    import json as _json
    import time

    # Step 1: Exchange refresh token for access token
    access_token = _get_gmail_access_token(client_id, client_secret, refresh_token)
    if not access_token:
        return False

    # Step 2: Build RFC 2822 MIME message
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"Shark Trading Agent <{from_email}>"
    msg["To"] = to_email
    msg.attach(MIMEText(body_html, "html"))

    raw_msg = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

    # Step 3: Send via Gmail API with retry
    payload = _json.dumps({"raw": raw_msg}).encode()

    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(
                _GMAIL_SEND_URL,
                data=payload,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with _ipv4_urlopen(req, timeout=15) as resp:
                if resp.status in (200, 201):
                    logger.info("Email sent via Gmail API — subject=%r to=%s", subject, to_email)
                    return True
                logger.warning("Gmail API returned HTTP %s", resp.status)
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode("utf-8", errors="replace")[:200]
            logger.warning(
                "Gmail API attempt %d/3 failed (HTTP %s): %s",
                attempt, exc.code, body_text,
            )
            # 401 = token expired, don't retry with same token
            if exc.code == 401:
                access_token = _get_gmail_access_token(client_id, client_secret, refresh_token)
                if not access_token:
                    return False
        except Exception as exc:
            logger.warning("Gmail API attempt %d/3 failed: %s", attempt, exc)

        if attempt < 3:
            time.sleep(1.5 * attempt)

    return False


def _get_gmail_access_token(
    client_id: str, client_secret: str, refresh_token: str,
) -> str:
    """Exchange a refresh token for a short-lived access token."""
    import json as _json
    import urllib.parse

    data = urllib.parse.urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }).encode()

    try:
        req = urllib.request.Request(
            _GMAIL_TOKEN_URL, data=data, method="POST",
        )
        with _ipv4_urlopen(req, timeout=10) as resp:
            result = _json.loads(resp.read())
            token = result.get("access_token", "")
            if token:
                return token
            logger.error("Gmail OAuth token response missing access_token")
    except Exception as exc:
        logger.error("Gmail OAuth token exchange failed: %s", exc)

    return ""


# ---------------------------------------------------------------------------
# Resend HTTP transport (HTTPS port 443)
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
            with _ipv4_urlopen(req, timeout=15) as resp:
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
