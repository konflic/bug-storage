"""One-time OAuth2 authorization-code flow for the issue tracker.

Run this script once to authorize bugdb with the tracker. It will:

1. Open a browser to the OAuth2 authorization URL.
2. Start a tiny local HTTP server to receive the callback.
3. Exchange the authorization code for access + refresh tokens.
4. Print the tokens so you can add them to your ``.env`` file.

Usage::

    python -m app.tracker_auth

Requires TRACKER_CLIENT_ID, TRACKER_CLIENT_SECRET, TRACKER_AUTHORIZE_URL,
and TRACKER_TOKEN_URL to be set in ``.env`` or as environment variables.
"""

from __future__ import annotations

import http.server
import secrets
import sys
import threading
import urllib.parse
import webbrowser

import httpx

from .config import settings

# Local callback server
_CALLBACK_PORT = 8777
_CALLBACK_PATH = "/callback"
_REDIRECT_URI = f"http://localhost:{_CALLBACK_PORT}{_CALLBACK_PATH}"


def _authorize() -> None:
    """Run the full authorization flow."""
    if not settings.tracker_client_id:
        print("ERROR: TRACKER_CLIENT_ID is not set.", file=sys.stderr)
        sys.exit(1)
    if not settings.tracker_client_secret:
        print("ERROR: TRACKER_CLIENT_SECRET is not set.", file=sys.stderr)
        sys.exit(1)
    if not settings.tracker_authorize_url:
        print("ERROR: TRACKER_AUTHORIZE_URL is not set.", file=sys.stderr)
        sys.exit(1)
    if not settings.tracker_token_url:
        print("ERROR: TRACKER_TOKEN_URL is not set.", file=sys.stderr)
        sys.exit(1)

    state = secrets.token_urlsafe(16)
    auth_code: str | None = None
    error_msg: str | None = None
    done = threading.Event()

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            nonlocal auth_code, error_msg
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)

            if parsed.path != _CALLBACK_PATH:
                self.send_response(404)
                self.end_headers()
                return

            if "error" in params:
                error_msg = params["error"][0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(
                    f"<h1>Authorization failed</h1><p>{error_msg}</p>"
                    "<p>You can close this tab.</p>".encode()
                )
                done.set()
                return

            received_state = params.get("state", [None])[0]
            if received_state != state:
                error_msg = f"State mismatch: expected {state}, got {received_state}"
                self.send_response(400)
                self.end_headers()
                done.set()
                return

            auth_code = params.get("code", [None])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(
                b"<h1>Authorization successful!</h1>"
                b"<p>You can close this tab and return to the terminal.</p>"
            )
            done.set()

        def log_message(self, format, *args):
            pass  # Suppress request logs

    # Build the authorization URL
    auth_params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id": settings.tracker_client_id,
        "redirect_uri": _REDIRECT_URI,
        "state": state,
        "scope": settings.tracker_scopes,
    })
    auth_url = f"{settings.tracker_authorize_url}?{auth_params}"

    print(f"Opening browser for authorization...")
    print(f"If the browser doesn't open, visit this URL manually:\n")
    print(f"  {auth_url}\n")

    # Start the callback server in a thread
    server = http.server.HTTPServer(("localhost", _CALLBACK_PORT), CallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    # Open the browser
    webbrowser.open(auth_url)

    # Wait for the callback
    print(f"Waiting for callback on http://localhost:{_CALLBACK_PORT}{_CALLBACK_PATH} ...")
    done.wait(timeout=300)
    server.shutdown()

    if error_msg:
        print(f"\nERROR: Authorization failed: {error_msg}", file=sys.stderr)
        sys.exit(1)
    if not auth_code:
        print("\nERROR: No authorization code received (timeout?).", file=sys.stderr)
        sys.exit(1)

    print(f"\nAuthorization code received. Exchanging for tokens...")

    # Exchange the code for tokens
    resp = httpx.post(
        settings.tracker_token_url,
        data={
            "grant_type": "authorization_code",
            "code": auth_code,
            "client_id": settings.tracker_client_id,
            "client_secret": settings.tracker_client_secret,
            "redirect_uri": _REDIRECT_URI,
        },
        timeout=15.0,
    )
    if resp.status_code != 200:
        print(f"\nERROR: Token exchange failed ({resp.status_code}): {resp.text}",
              file=sys.stderr)
        sys.exit(1)

    body = resp.json()
    access_token = body.get("access_token", "")
    refresh_token = body.get("refresh_token", "")
    expires_in = body.get("expires_in", "?")

    print("\n" + "=" * 60)
    print("SUCCESS! Add these to your .env file:")
    print("=" * 60)
    print(f"\nTRACKER_OAUTH_TOKEN={access_token}")
    if refresh_token:
        print(f"TRACKER_REFRESH_TOKEN={refresh_token}")
    print(f"\n# Access token expires in {expires_in}s.")
    if refresh_token:
        print("# With the refresh token set, bugdb will auto-renew.")
        print("# You can remove TRACKER_OAUTH_TOKEN once TRACKER_REFRESH_TOKEN is set.")
    else:
        print("# No refresh token was returned. You'll need to re-authorize")
        print("# when the access token expires.")
    print("=" * 60)


def main() -> None:
    _authorize()


if __name__ == "__main__":
    main()
