#!/usr/bin/env python3
"""
One-time Gmail OAuth2 setup — obtains a refresh token for the Gmail REST API.

Run this ONCE on your local machine (not in a sandbox):
    python scripts/gmail_oauth_setup.py

Prerequisites:
  1. Go to https://console.cloud.google.com/
  2. Create a project (or use existing)
  3. Enable the Gmail API:
       APIs & Services → Library → search "Gmail API" → Enable
  4. Create OAuth credentials:
       APIs & Services → Credentials → Create Credentials → OAuth client ID
       Application type: "Desktop app"
       Download the JSON → save as `gcp-oauth.keys.json` in this folder
  5. Configure OAuth consent screen:
       APIs & Services → OAuth consent screen
       Add your Gmail address as a test user
       Scopes needed: https://www.googleapis.com/auth/gmail.send

After running this script, you'll get a GMAIL_OAUTH_REFRESH_TOKEN.
Add it (along with client_id and client_secret) to your .env file
and to your Claude routine environment variables.
"""

import http.server
import json
import os
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

_REDIRECT_PORT = 9004
_REDIRECT_URI = f"http://localhost:{_REDIRECT_PORT}"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_SCOPES = "https://www.googleapis.com/auth/gmail.send"

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_KEYS_FILE = os.path.join(_SCRIPT_DIR, "gcp-oauth.keys.json")


def _load_client_credentials() -> tuple[str, str]:
    """Load client_id and client_secret from gcp-oauth.keys.json."""
    if not os.path.exists(_KEYS_FILE):
        print(f"\n❌ File not found: {_KEYS_FILE}")
        print("   Download your OAuth credentials JSON from Google Cloud Console")
        print("   and save it as: scripts/gcp-oauth.keys.json")
        sys.exit(1)

    with open(_KEYS_FILE) as f:
        data = json.load(f)

    # Google exports credentials in different formats
    if "installed" in data:
        creds = data["installed"]
    elif "web" in data:
        creds = data["web"]
    else:
        creds = data

    client_id = creds.get("client_id", "")
    client_secret = creds.get("client_secret", "")

    if not client_id or not client_secret:
        print("❌ Could not find client_id/client_secret in gcp-oauth.keys.json")
        sys.exit(1)

    return client_id, client_secret


def _exchange_code_for_tokens(
    code: str, client_id: str, client_secret: str,
) -> dict:
    """Exchange authorization code for access + refresh tokens."""
    data = urllib.parse.urlencode({
        "code": code,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": _REDIRECT_URI,
        "grant_type": "authorization_code",
    }).encode()

    req = urllib.request.Request(_TOKEN_URL, data=data, method="POST")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())


def main() -> None:
    print("=" * 60)
    print("  Shark Trading Agent — Gmail OAuth2 Setup")
    print("=" * 60)

    client_id, client_secret = _load_client_credentials()
    print(f"\n✓ Loaded credentials — client_id: {client_id[:20]}...")

    # Build authorization URL
    params = urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": _REDIRECT_URI,
        "response_type": "code",
        "scope": _SCOPES,
        "access_type": "offline",
        "prompt": "consent",  # Force consent to always get refresh_token
    })
    auth_url = f"{_AUTH_URL}?{params}"

    # Start local server to capture the redirect
    auth_code: list[str] = []
    server_error: list[str] = []

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = urllib.parse.urlparse(self.path).query
            qs = urllib.parse.parse_qs(query)

            if "code" in qs:
                auth_code.append(qs["code"][0])
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><h2>&#10004; Authorization successful!</h2>"
                    b"<p>You can close this tab and return to the terminal.</p>"
                    b"</body></html>"
                )
            elif "error" in qs:
                server_error.append(qs["error"][0])
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    f"<html><body><h2>Error: {qs['error'][0]}</h2></body></html>".encode()
                )
            else:
                self.send_response(400)
                self.end_headers()

        def log_message(self, *args):  # noqa: ANN002
            pass  # Suppress server logs

    server = http.server.HTTPServer(("127.0.0.1", _REDIRECT_PORT), CallbackHandler)
    server_thread = threading.Thread(target=server.handle_request, daemon=True)
    server_thread.start()

    print(f"\n→ Opening browser for Google authorization...")
    print(f"  If the browser doesn't open, visit:\n  {auth_url}\n")
    webbrowser.open(auth_url)

    # Wait for the callback
    server_thread.join(timeout=120)
    server.server_close()

    if server_error:
        print(f"\n❌ Authorization failed: {server_error[0]}")
        sys.exit(1)

    if not auth_code:
        print("\n❌ Timed out waiting for authorization (120s)")
        sys.exit(1)

    print("✓ Authorization code received — exchanging for tokens...")

    tokens = _exchange_code_for_tokens(auth_code[0], client_id, client_secret)
    refresh_token = tokens.get("refresh_token", "")
    access_token = tokens.get("access_token", "")

    if not refresh_token:
        print("\n❌ No refresh_token in response — try revoking access at")
        print("   https://myaccount.google.com/permissions")
        print("   and run this script again.")
        sys.exit(1)

    # Verify the token works by getting user profile
    print("✓ Tokens received — verifying...")
    try:
        req = urllib.request.Request(
            "https://gmail.googleapis.com/gmail/v1/users/me/profile",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            profile = json.loads(resp.read())
            email = profile.get("emailAddress", "unknown")
            print(f"✓ Verified — sending as: {email}")
    except Exception as exc:
        print(f"⚠ Could not verify token (non-fatal): {exc}")

    print("\n" + "=" * 60)
    print("  ✅ SUCCESS — Add these to your .env and Claude routine env vars:")
    print("=" * 60)
    print(f"\nGMAIL_OAUTH_CLIENT_ID={client_id}")
    print(f"GMAIL_OAUTH_CLIENT_SECRET={client_secret}")
    print(f"GMAIL_OAUTH_REFRESH_TOKEN={refresh_token}")
    print(f"NOTIFY_FROM_EMAIL={email if 'email' in dir() else 'your_gmail@gmail.com'}")
    print(f"NOTIFY_EMAIL={email if 'email' in dir() else 'your_gmail@gmail.com'}")
    print("\n" + "=" * 60)
    print("  The refresh token does NOT expire unless you revoke it.")
    print("  Keep it secret — treat it like a password.")
    print("=" * 60)


if __name__ == "__main__":
    main()
