#!/usr/bin/env python3
"""Audit and optionally delete Telegram webhooks for known bot tokens.

Use this on Render when a stale Telegram bot is still delivering to an old
legacy URL such as /webhook/9. Telegram only lets the holder of that bot token
change or delete the webhook, so this script checks every token you provide and
can delete the matching stale webhook with drop_pending_updates=true.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Iterable


API_BASE = "https://api.telegram.org/bot"


def _token_label(token: str) -> str:
    token = token.strip()
    if len(token) <= 12:
        return "***"
    return f"{token[:6]}...{token[-6:]}"


def _read_tokens_from_env() -> list[str]:
    tokens: list[str] = []
    if os.getenv("BOT_TOKENS"):
        tokens.extend(t.strip() for t in os.environ["BOT_TOKENS"].split(","))

    for name, value in sorted(os.environ.items()):
        if name == "BOT_TOKEN" or name.startswith("BOT_TOKEN_"):
            tokens.append(value.strip())
    return tokens


def _read_tokens_from_file(path: str | None) -> list[str]:
    if not path:
        return []
    token_file = Path(path)
    if not token_file.exists():
        raise SystemExit(f"token file not found: {token_file}")
    tokens: list[str] = []
    for line in token_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        tokens.append(line)
    return tokens


def _unique_tokens(tokens: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for token in tokens:
        token = token.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        unique.append(token)
    return unique


def _telegram_get(token: str, method: str, params: dict[str, object] | None = None) -> dict:
    query = f"?{urllib.parse.urlencode(params or {})}" if params else ""
    url = f"{API_BASE}{token}/{method}{query}"
    with urllib.request.urlopen(url, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _telegram_post(token: str, method: str, params: dict[str, object]) -> dict:
    data = urllib.parse.urlencode(params).encode("utf-8")
    url = f"{API_BASE}{token}/{method}"
    request = urllib.request.Request(url, data=data, method="POST")
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find Telegram bot tokens whose webhook still points at a stale URL."
    )
    parser.add_argument("tokens", nargs="*", help="Bot token(s) to audit.")
    parser.add_argument("--file", help="File containing one bot token per line.")
    parser.add_argument(
        "--include-env",
        action="store_true",
        help="Also read BOT_TOKENS, BOT_TOKEN, and BOT_TOKEN_* from the environment.",
    )
    parser.add_argument(
        "--url-contains",
        default="/webhook/9",
        help="Only delete webhooks whose URL contains this text. Default: /webhook/9",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Delete matching webhooks and drop pending updates.",
    )
    parser.add_argument(
        "--delete-all",
        action="store_true",
        help="Delete every provided token's webhook, regardless of URL. Use with care.",
    )
    args = parser.parse_args()

    tokens = list(args.tokens)
    tokens.extend(_read_tokens_from_file(args.file))
    if args.include_env:
        tokens.extend(_read_tokens_from_env())
    tokens = _unique_tokens(tokens)

    if not tokens:
        print("No tokens supplied. Pass tokens, --file old_tokens.txt, or --include-env.", file=sys.stderr)
        return 2

    matched = 0
    deleted = 0
    for token in tokens:
        label = _token_label(token)
        try:
            info_response = _telegram_get(token, "getWebhookInfo")
        except Exception as exc:
            print(f"{label} getWebhookInfo failed: {exc}")
            continue

        if not info_response.get("ok"):
            print(f"{label} getWebhookInfo not ok: {info_response}")
            continue

        info = info_response.get("result") or {}
        url = info.get("url") or ""
        pending = info.get("pending_update_count", 0)
        last_error = info.get("last_error_message") or ""
        should_delete = args.delete_all or (args.url_contains and args.url_contains in url)

        status = "MATCH" if should_delete else "skip"
        if should_delete:
            matched += 1
        print(f"{label} {status} pending={pending} url={url!r} last_error={last_error!r}")

        if should_delete and args.delete:
            try:
                delete_response = _telegram_post(
                    token,
                    "deleteWebhook",
                    {"drop_pending_updates": "true"},
                )
            except Exception as exc:
                print(f"{label} deleteWebhook failed: {exc}")
                continue
            print(f"{label} deleteWebhook response: {delete_response}")
            if delete_response.get("ok"):
                deleted += 1

    if args.delete:
        print(f"Done. matched={matched} deleted={deleted}")
    else:
        print(f"Done. matched={matched}. Re-run with --delete to delete matching webhooks.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
