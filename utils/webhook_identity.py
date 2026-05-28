"""Stable per-slot identity helpers for Telegram webhook/runtime isolation."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Optional


GLOBAL_SALT = os.getenv("WEBHOOK_IDENTITY_SALT", "bradycadia")


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def build_token_hash(telegram_token: str) -> str:
    return _sha256(telegram_token)


def build_identity_hash(slot_id: int, telegram_token: str, identity_version: int = 1, global_salt: str = GLOBAL_SALT) -> str:
    return _sha256(f"{slot_id}:{telegram_token}:{identity_version}:{global_salt}")


def build_webhook_secret(slot_id: int, telegram_token: str, identity_version: int = 1, global_salt: str = GLOBAL_SALT) -> str:
    return build_identity_hash(slot_id, telegram_token, identity_version, global_salt)


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
    identity_hash = build_identity_hash(slot_id, telegram_token, identity_version)
    return BotSlot(
        slot_id=slot_id,
        slot_name=slot_name,
        telegram_token=telegram_token,
        telegram_username=telegram_username,
        telegram_bot_id=telegram_bot_id,
        token_hash=token_hash,
        identity_version=identity_version,
        identity_hash=identity_hash,
        webhook_secret=build_webhook_secret(slot_id, telegram_token, identity_version),
    )
