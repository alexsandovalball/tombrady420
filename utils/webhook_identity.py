"""Stable per-slot identity helpers for Telegram webhook/runtime isolation."""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import dataclass
from typing import Optional


GLOBAL_SALT = os.getenv("WEBHOOK_IDENTITY_SALT", "bradycadia")
WEBHOOK_SECRET_KEY = os.getenv("WEBHOOK_SECRET_KEY", GLOBAL_SALT)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_token_hash(telegram_token: str) -> str:
    return _sha256(telegram_token)


def build_identity_hash(slot_id: int, telegram_token: str, identity_version: int = 1, global_salt: str = GLOBAL_SALT) -> str:
    return _sha256(f"{slot_id}:{telegram_token}:{identity_version}:{global_salt}")


def resolve_bot_identity(
    telegram_token: str,
    *,
    telegram_bot_id: Optional[int] = None,
    global_salt: str = GLOBAL_SALT,
) -> str:
    """Return the canonical bot identity used for token-order-independent namespaces."""
    if telegram_bot_id:
        return _sha256(f"telegram_bot_id:{telegram_bot_id}:{global_salt}")
    return _sha256(f"token:{telegram_token}:{global_salt}")


def build_webhook_secret(slot_id: int, telegram_token: str, identity_version: int = 1, global_salt: str = GLOBAL_SALT) -> str:
    """Derive Telegram webhook secret from the token only, never slot position."""
    return hmac.new(
        WEBHOOK_SECRET_KEY.encode("utf-8"),
        telegram_token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def build_registry_webhook_secret(telegram_bot_id: int) -> str:
    """Derive the canonical stored webhook secret from immutable Telegram bot ID."""
    return hmac.new(
        WEBHOOK_SECRET_KEY.encode("utf-8"),
        f"telegram_bot_id:{telegram_bot_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


@dataclass(frozen=True)
class BotSlot:
    slot_id: int
    slot_name: str
    telegram_token: str
    telegram_username: Optional[str]
    telegram_bot_id: Optional[int]
    token_hash: str
    identity_version: int
    identity_hash: str
    webhook_secret: str


def build_bot_slot(
    slot_id: int,
    telegram_token: str,
    *,
    telegram_username: Optional[str] = None,
    telegram_bot_id: Optional[int] = None,
    identity_version: int = 1,
) -> BotSlot:
    slot_name = f"bot_token_{slot_id}"
    token_hash = build_token_hash(telegram_token)
    identity_hash = resolve_bot_identity(telegram_token, telegram_bot_id=telegram_bot_id)
    return BotSlot(
        slot_id=slot_id,
        slot_name=slot_name,
        telegram_token=telegram_token,
        telegram_username=telegram_username,
        telegram_bot_id=telegram_bot_id,
        token_hash=token_hash,
        identity_version=identity_version,
        identity_hash=identity_hash,
        webhook_secret=(
            build_registry_webhook_secret(telegram_bot_id)
            if telegram_bot_id
            else build_webhook_secret(slot_id, telegram_token, identity_version)
        ),
    )
