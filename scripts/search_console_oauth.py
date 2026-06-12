"""Create a Search Console OAuth refresh token file.

Run this once from a machine with a browser:

    python scripts/search_console_oauth.py --client-json path/to/oauth-client.json

The script starts a temporary localhost callback, opens Google's consent page,
then writes a token file that Render can reuse for background Search Console
imports.
"""

from __future__ import annotations

import argparse
import json
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import requests

SEARCH_CONSOLE_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


def _client_info(payload: dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload.get("installed"), dict):
        return payload["installed"]
    if isinstance(payload.get("web"), dict):
        return payload["web"]
    return payload


def _load_client(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    info = _client_info(payload)
    if not info.get("client_id") or not info.get("client_secret"):
        raise SystemExit("OAuth client JSON must contain client_id and client_secret")
    return info


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    server: "_OAuthServer"

    def log_message(self, fmt: str, *args) -> None:
        return

    def do_GET(self) -> None:  # noqa: N802 - stdlib API name
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        self.server.auth_code = (params.get("code") or [""])[0]
        self.server.auth_error = (params.get("error") or [""])[0]
        self.server.auth_state = (params.get("state") or [""])[0]

        if self.server.auth_error:
            body = "Authorization failed. You can close this tab."
            status = 400
        else:
            body = "Authorization complete. You can close this tab and return to Codex/PowerShell."
            status = 200

        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))
        threading.Thread(target=self.server.shutdown, daemon=True).start()


class _OAuthServer(HTTPServer):
    auth_code = ""
    auth_error = ""
    auth_state = ""


def _exchange_code(client: dict[str, Any], code: str, redirect_uri: str) -> dict[str, Any]:
    response = requests.post(
        client.get("token_uri") or "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    if response.status_code >= 400:
        raise SystemExit(f"Token exchange failed: {response.status_code} {response.text}")
    payload = response.json()
    if not payload.get("refresh_token"):
        raise SystemExit(
            "Google did not return a refresh_token. Delete old app permissions at "
            "https://myaccount.google.com/permissions and rerun this script."
        )
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a Search Console OAuth token file.")
    parser.add_argument("--client-json", required=True, help="Path to the OAuth client ID JSON you downloaded.")
    parser.add_argument(
        "--output",
        default="search-console-oauth-token.json",
        help="Where to write the refresh token JSON.",
    )
    args = parser.parse_args()

    client_path = Path(args.client_json).expanduser().resolve()
    output_path = Path(args.output).expanduser().resolve()
    client = _load_client(client_path)

    server = _OAuthServer(("127.0.0.1", 0), _OAuthCallbackHandler)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/"
    state = secrets.token_urlsafe(18)

    auth_uri = client.get("auth_uri") or "https://accounts.google.com/o/oauth2/v2/auth"
    auth_url = auth_uri + "?" + urllib.parse.urlencode({
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SEARCH_CONSOLE_SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    })

    print("\nOpening Google sign-in in your browser...")
    print("\nIf it does not open, paste this URL into your browser:\n")
    print(auth_url)
    print("\nWaiting for Google callback...")
    webbrowser.open(auth_url)

    server.serve_forever()
    server.server_close()

    if server.auth_error:
        raise SystemExit(f"Authorization failed: {server.auth_error}")
    if server.auth_state != state:
        raise SystemExit("Authorization failed: state mismatch")
    if not server.auth_code:
        raise SystemExit("Authorization failed: no code received")

    token = _exchange_code(client, server.auth_code, redirect_uri)
    output_payload = {
        "refresh_token": token["refresh_token"],
        "token_uri": client.get("token_uri") or "https://oauth2.googleapis.com/token",
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "scope": SEARCH_CONSOLE_SCOPE,
    }
    output_path.write_text(json.dumps(output_payload, indent=2), encoding="utf-8")
    print(f"\nToken written to: {output_path}")
    print("\nOn Render, place this file at /data/search-console-oauth-token.json")
    print("Also place your OAuth client JSON at /data/search-console-oauth-client.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
