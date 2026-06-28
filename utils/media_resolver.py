"""Centralized bot-specific Telegram media resolution."""
from __future__ import annotations

import asyncio
import json
import logging
import time as _time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional

from telegram import Bot
from telegram.error import BadRequest, RetryAfter, TelegramError

from database import Database
from utils.helpers import get_bot_specific_file_id

logger = logging.getLogger(__name__)

_resolve_semaphore = asyncio.Semaphore(4)
_VALIDATED_FILE_ID_TTL_SECONDS = 60 * 60
_VALIDATED_FILE_ID_MAX = 5000
_VALIDATION_TIMEOUT_SECONDS = 3.0
_validated_file_ids: OrderedDict[tuple[str, str], float] = OrderedDict()
_resolve_locks: dict[tuple[str, int, int, int, str], asyncio.Lock] = {}


def _normalize_media_type(file_type: Any, fallback: str = "photo") -> str:
    value = str(file_type or fallback or "photo").strip().lower()
    return value or "photo"


def _parse_additional_media_entry(entry: Any, fallback_type: str) -> Optional[tuple[str, str]]:
    if isinstance(entry, str):
        file_id = entry.strip()
        return (file_id, fallback_type) if file_id else None

    if isinstance(entry, dict):
        file_id = (
            entry.get("file_id")
            or entry.get("id")
            or entry.get("media")
            or entry.get("media_id")
        )
        file_type = (
            entry.get("file_type")
            or entry.get("type")
            or entry.get("media_type")
            or fallback_type
        )
        return (str(file_id).strip(), _normalize_media_type(file_type, fallback_type)) if file_id else None

    if isinstance(entry, (list, tuple)):
        if not entry:
            return None
        file_id = entry[0]
        if not file_id:
            return None
        file_type = entry[1] if len(entry) >= 2 else fallback_type
        return (str(file_id).strip(), _normalize_media_type(file_type, fallback_type))

    return None


def collect_product_media(product: dict[str, Any]) -> list[tuple[str, str]]:
    """Return all product media as (file_id, file_type), accepting historical JSON shapes."""
    fallback_type = _normalize_media_type(product.get("file_type"), "photo")
    media: list[tuple[str, str]] = []

    primary_file_id = product.get("file_id")
    if primary_file_id:
        media.append((str(primary_file_id).strip(), fallback_type))

    raw = product.get("additional_file_ids")
    if not raw:
        return media

    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        logger.warning("Malformed additional_file_ids", extra={"product_id": product.get("id")})
        return media

    if isinstance(data, dict):
        if isinstance(data.get("media"), list):
            data = data["media"]
        elif isinstance(data.get("items"), list):
            data = data["items"]
        elif isinstance(data.get("file_ids"), list):
            file_ids = data.get("file_ids") or []
            file_types = data.get("file_types") or []
            data = [
                (file_id, file_types[idx] if idx < len(file_types) else fallback_type)
                for idx, file_id in enumerate(file_ids)
            ]
        else:
            parsed = _parse_additional_media_entry(data, fallback_type)
            return media + ([parsed] if parsed else [])

    if not isinstance(data, list):
        logger.warning("Unexpected additional_file_ids payload", extra={"product_id": product.get("id")})
        return media

    for entry in data:
        parsed = _parse_additional_media_entry(entry, fallback_type)
        if parsed:
            media.append(parsed)

    return [(file_id, file_type) for file_id, file_type in media if file_id]


@dataclass
class MediaResolutionResult:
    success: bool
    file_id: Optional[str]
    source: str
    error: Optional[str] = None


def _invalid_file_id_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "file_id is invalid" in message
        or "wrong file identifier" in message
        or "file identifier" in message
    )


class MediaResolver:
    """Resolve media file IDs that are valid for the currently serving bot."""

    def __init__(self, bot: Bot, db: Optional[Database] = None) -> None:
        self.bot = bot
        self.db = db or Database()

    async def resolve_media_for_bot(
        self,
        product: dict[str, Any],
        current_bot_username: str,
        media_index: int = 0,
        allow_rehydrate: bool = True,
    ) -> MediaResolutionResult:
        product_id = product.get("id")
        original_bot = (product.get("bot_username") or "").lower()
        current_bot = (current_bot_username or "").lower()
        file_type = self._media_type(product, media_index)
        original_file_id = self._media_file_id(product, media_index)
        source_chat_id = product.get("chat_id")
        source_message_id = self._source_message_id(product, media_index)

        same_bot = original_bot and current_bot and original_bot == current_bot
        log_extra = {
            "product_id": product_id,
            "current_bot": current_bot,
            "original_bot": original_bot,
            "media_index": media_index,
            "file_type": file_type,
        }

        if not source_chat_id or not source_message_id or not file_type:
            if same_bot and original_file_id:
                validation = await self._validate_file_id(original_file_id)
                if validation.success:
                    logger.info(
                        "Media resolution succeeded",
                        extra={**log_extra, "resolution_source": "original_no_source", "fallback_used": False},
                    )
                    return MediaResolutionResult(True, original_file_id, "original_no_source")
            error = "missing source chat/message metadata"
            logger.warning(
                "Bot-specific file_id resolution failed",
                extra={**log_extra, "reason": error, "fallback_used": True},
            )
            return MediaResolutionResult(False, None, "metadata", error)

        if same_bot and original_file_id:
            validation = await self._validate_file_id(original_file_id)
            if validation.success:
                logger.info(
                    "Media resolution succeeded",
                    extra={**log_extra, "resolution_source": "original", "fallback_used": False},
                )
                return MediaResolutionResult(True, original_file_id, "original")
            logger.warning(
                "Original bot file_id validation failed",
                extra={**log_extra, "reason": validation.error},
            )

        cached = await self.db.get_bot_file_id(
            source_chat_id,
            source_message_id,
            media_index,
            current_bot,
            file_type=file_type,
        )
        if cached:
            validation = await self._validate_file_id(cached)
            if validation.success:
                logger.info(
                    "Media resolution cache hit",
                    extra={**log_extra, "resolution_source": "cache", "cache_hit": True},
                )
                return MediaResolutionResult(True, cached, "cache")

            await self.db.delete_bot_file_id_cache_entry(
                source_chat_id, source_message_id, media_index, current_bot
            )
            logger.warning(
                "Invalid cached file_id evicted",
                extra={**log_extra, "reason": validation.error, "cache_hit": True},
            )
        else:
            logger.info("Media resolution cache miss", extra={**log_extra, "cache_hit": False})

        if not allow_rehydrate:
            error = "cache miss; rehydrate skipped"
            logger.info(
                "Media resolution skipped rehydrate",
                extra={**log_extra, "resolution_source": "cache", "reason": error, "fallback_used": True},
            )
            return MediaResolutionResult(False, None, "cache", error)

        resolved = await self._singleflight_rehydrate(
            product,
            source_chat_id,
            source_message_id,
            file_type,
            media_index,
            current_bot,
            log_extra,
        )

        if not resolved:
            error = "forward/rehydrate failed"
            logger.warning(
                "Bot-specific file_id resolution failed",
                extra={**log_extra, "resolution_source": "forward", "reason": error, "fallback_used": True},
            )
            return MediaResolutionResult(False, None, "forward", error)

        self._remember_validated_file_id(resolved)

        logger.info(
            "Media resolution succeeded",
            extra={**log_extra, "resolution_source": "forward", "fallback_used": False},
        )
        return MediaResolutionResult(True, resolved, "forward")

    async def _singleflight_rehydrate(
        self,
        product: dict[str, Any],
        source_chat_id: int,
        source_message_id: int,
        file_type: str,
        media_index: int,
        current_bot: str,
        log_extra: dict[str, Any],
    ) -> Optional[str]:
        lock_key = (current_bot, int(source_chat_id), int(source_message_id), int(media_index), file_type)
        lock = _resolve_locks.get(lock_key)
        if lock is None:
            lock = asyncio.Lock()
            _resolve_locks[lock_key] = lock

        async with lock:
            cached = await self.db.get_bot_file_id(
                source_chat_id,
                source_message_id,
                media_index,
                current_bot,
                file_type=file_type,
            )
            if cached:
                validation = await self._validate_file_id(cached)
                if validation.success:
                    logger.info(
                        "Media resolution cache hit after wait",
                        extra={**log_extra, "resolution_source": "cache_after_wait", "cache_hit": True},
                    )
                    return cached

            async with _resolve_semaphore:
                return await self._rehydrate_with_retry(
                    product,
                    source_chat_id,
                    source_message_id,
                    file_type,
                    media_index,
                    current_bot,
                    log_extra,
                )

    async def _validate_file_id(self, file_id: str) -> MediaResolutionResult:
        bot_username = (getattr(self.bot, "username", None) or "").lower()
        cache_key = (bot_username, file_id)
        now = _time.monotonic()
        cached_at = _validated_file_ids.get(cache_key)
        if cached_at is not None and now - cached_at < _VALIDATED_FILE_ID_TTL_SECONDS:
            _validated_file_ids.move_to_end(cache_key)
            return MediaResolutionResult(True, file_id, "validated_cache")

        try:
            await asyncio.wait_for(
                self.bot.get_file(file_id),
                timeout=_VALIDATION_TIMEOUT_SECONDS,
            )
            self._remember_validated_file_id(file_id)
            return MediaResolutionResult(True, file_id, "validated")
        except asyncio.TimeoutError:
            _validated_file_ids.pop(cache_key, None)
            return MediaResolutionResult(False, None, "validation", "validation timeout")
        except TelegramError as exc:
            _validated_file_ids.pop(cache_key, None)
            return MediaResolutionResult(False, None, "validation", str(exc))

    def _remember_validated_file_id(self, file_id: str) -> None:
        bot_username = (getattr(self.bot, "username", None) or "").lower()
        cache_key = (bot_username, file_id)
        _validated_file_ids[cache_key] = _time.monotonic()
        _validated_file_ids.move_to_end(cache_key)
        while len(_validated_file_ids) > _VALIDATED_FILE_ID_MAX:
            _validated_file_ids.popitem(last=False)

    async def _rehydrate_with_retry(
        self,
        product: dict[str, Any],
        source_chat_id: int,
        source_message_id: int,
        file_type: str,
        media_index: int,
        current_bot: str,
        log_extra: dict[str, Any],
    ) -> Optional[str]:
        for attempt in range(3):
            try:
                return await get_bot_specific_file_id(
                    self.bot,
                    source_chat_id,
                    source_message_id,
                    file_type,
                    file_index=media_index,
                )
            except RetryAfter as exc:
                delay = min(max(float(exc.retry_after), 1.0) * (2 ** attempt), 30.0)
                logger.warning(
                    "Media rehydrate rate limited",
                    extra={**log_extra, "attempt": attempt + 1, "delay": delay},
                )
                await asyncio.sleep(delay)
            except BadRequest as exc:
                if _invalid_file_id_error(exc):
                    await self.db.delete_bot_file_id_cache_entry(
                        source_chat_id, source_message_id, media_index, current_bot
                    )
                logger.warning(
                    "Media rehydrate BadRequest",
                    extra={**log_extra, "attempt": attempt + 1, "reason": str(exc)},
                )
                return None
            except Exception as exc:
                logger.warning(
                    "Media rehydrate failed",
                    extra={**log_extra, "attempt": attempt + 1, "reason": str(exc)},
                    exc_info=True,
                )
                await asyncio.sleep(0.5 * (2 ** attempt))
        return None

    def _media_file_id(self, product: dict[str, Any], media_index: int) -> Optional[str]:
        media = collect_product_media(product)
        if 0 <= media_index < len(media):
            return media[media_index][0]
        return None

    def _media_type(self, product: dict[str, Any], media_index: int) -> Optional[str]:
        media = collect_product_media(product)
        if 0 <= media_index < len(media):
            return media[media_index][1]
        return None

    def _source_message_id(self, product: dict[str, Any], media_index: int) -> Optional[int]:
        if media_index == 0:
            return product.get("message_id")
        ids = self._additional_message_ids(product)
        if media_index - 1 < len(ids) and ids[media_index - 1] is not None:
            return ids[media_index - 1]
        if product.get("media_group_id") and product.get("message_id") is not None:
            try:
                return int(product.get("message_id")) + media_index
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _additional_media(product: dict[str, Any]) -> list[tuple[str, str]]:
        return collect_product_media(product)[1:]

    @staticmethod
    def _additional_message_ids(product: dict[str, Any]) -> list[Optional[int]]:
        raw = product.get("additional_message_ids")
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("Malformed additional_message_ids", extra={"product_id": product.get("id")})
            return []
        if not isinstance(data, list):
            return []
        result: list[Optional[int]] = []
        for item in data:
            try:
                result.append(int(item))
            except (TypeError, ValueError):
                result.append(None)
                logger.warning(
                    "Invalid additional message id",
                    extra={"product_id": product.get("id"), "message_id": item},
                )
        return result
