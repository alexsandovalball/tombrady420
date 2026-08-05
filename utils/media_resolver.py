"""Centralized bot-specific Telegram media resolution."""
from __future__ import annotations

import asyncio
import json
import logging
import time as _time
import weakref
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Optional

from telegram import Bot
from telegram.error import BadRequest, RetryAfter, TelegramError

from database import Database
from utils.helpers import (
    get_bot_specific_file_id,
    is_invalid_telegram_file_id_error,
    is_transient_telegram_error,
)
from utils.telegram_fetch import fetch_telegram_file

logger = logging.getLogger(__name__)

_resolve_semaphore = asyncio.Semaphore(4)
_VALIDATED_FILE_ID_TTL_SECONDS = 60 * 60
_VALIDATED_FILE_ID_MAX = 2000
_VALIDATION_TIMEOUT_SECONDS = 2.0
_validated_file_ids: OrderedDict[tuple[str, str], tuple[float, Optional[str]]] = OrderedDict()
_background_validation_tasks: set[asyncio.Task] = set()
_background_validation_keys: set[tuple[str, str]] = set()
_BACKGROUND_VALIDATION_MAX = 128
_resolve_locks: weakref.WeakValueDictionary[
    tuple[str, int, int, int, str], asyncio.Lock
] = weakref.WeakValueDictionary()


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
    file_unique_id: Optional[str] = None


def _invalid_file_id_error(exc: Exception) -> bool:
    return is_invalid_telegram_file_id_error(exc)


def _transient_validation_error(error: Optional[str]) -> bool:
    return is_transient_telegram_error(error or "")


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
        force_rehydrate: bool = False,
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
                logger.info(
                    "Media resolution succeeded",
                    extra={**log_extra, "resolution_source": "original_no_source", "fallback_used": False},
                )
                self._schedule_background_validation(original_file_id)
                return MediaResolutionResult(True, original_file_id, "original_no_source")
            error = "missing source chat/message metadata"
            logger.warning(
                "Bot-specific file_id resolution failed",
                extra={**log_extra, "reason": error, "fallback_used": True},
            )
            return MediaResolutionResult(False, None, "metadata", error)

        if same_bot and original_file_id and not force_rehydrate:
            logger.info(
                "Media resolution succeeded",
                extra={**log_extra, "resolution_source": "original", "fallback_used": False},
            )
            self._schedule_background_validation(original_file_id)
            return MediaResolutionResult(True, original_file_id, "original")

        cached = None
        if not force_rehydrate:
            cached = await self.db.get_bot_file_id(
                source_chat_id,
                source_message_id,
                media_index,
                current_bot,
                file_type=file_type,
            )
        if cached:
            logger.info(
                "Media resolution cache hit",
                extra={**log_extra, "resolution_source": "cache_unvalidated_fast_path", "cache_hit": True},
            )
            self._schedule_background_validation(
                cached,
                source_chat_id=source_chat_id,
                source_message_id=source_message_id,
                media_index=media_index,
                current_bot=current_bot,
                file_type=file_type,
                log_extra=log_extra,
            )
            return MediaResolutionResult(True, cached, "cache")
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
            force_rehydrate=force_rehydrate,
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
        force_rehydrate: bool = False,
    ) -> Optional[str]:
        lock_key = (current_bot, int(source_chat_id), int(source_message_id), int(media_index), file_type)
        lock = _resolve_locks.get(lock_key)
        if lock is None:
            lock = asyncio.Lock()
            _resolve_locks[lock_key] = lock

        async with lock:
            cached = None
            if not force_rehydrate:
                cached = await self.db.get_bot_file_id(
                    source_chat_id,
                    source_message_id,
                    media_index,
                    current_bot,
                    file_type=file_type,
                )
            if cached:
                logger.info(
                    "Media resolution cache hit after wait",
                    extra={**log_extra, "resolution_source": "cache_after_wait_unvalidated_fast_path", "cache_hit": True},
                )
                self._schedule_background_validation(
                    cached,
                    source_chat_id=source_chat_id,
                    source_message_id=source_message_id,
                    media_index=media_index,
                    current_bot=current_bot,
                    file_type=file_type,
                    log_extra=log_extra,
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
                    force_refresh=force_rehydrate,
                )

    async def _validate_file_id(self, file_id: str) -> MediaResolutionResult:
        bot_username = (getattr(self.bot, "username", None) or "").lower()
        cache_key = (bot_username, file_id)
        now = _time.monotonic()
        cached_at = _validated_file_ids.get(cache_key)
        cached_unique_id = None
        if cached_at is not None:
            if isinstance(cached_at, tuple):
                cached_at, cached_unique_id = cached_at
            else:
                cached_unique_id = None
        if cached_at is not None and now - cached_at < _VALIDATED_FILE_ID_TTL_SECONDS:
            _validated_file_ids.move_to_end(cache_key)
            return MediaResolutionResult(True, file_id, "validated_cache", file_unique_id=cached_unique_id)

        try:
            tg_file = await fetch_telegram_file(
                self.bot,
                file_id,
                context="media_resolver_validate",
                retries=1,
                timeout_seconds=_VALIDATION_TIMEOUT_SECONDS,
            )
            file_unique_id = getattr(tg_file, "file_unique_id", None)
            self._remember_validated_file_id(file_id, file_unique_id)
            return MediaResolutionResult(
                True,
                file_id,
                "validated",
                file_unique_id=file_unique_id,
            )
        except asyncio.TimeoutError:
            _validated_file_ids.pop(cache_key, None)
            return MediaResolutionResult(False, None, "validation", "validation timeout")
        except TelegramError as exc:
            _validated_file_ids.pop(cache_key, None)
            return MediaResolutionResult(False, None, "validation", str(exc))

    def _remember_validated_file_id(self, file_id: str, file_unique_id: Optional[str] = None) -> None:
        bot_username = (getattr(self.bot, "username", None) or "").lower()
        cache_key = (bot_username, file_id)
        _validated_file_ids[cache_key] = (_time.monotonic(), file_unique_id)
        _validated_file_ids.move_to_end(cache_key)
        while len(_validated_file_ids) > _VALIDATED_FILE_ID_MAX:
            _validated_file_ids.popitem(last=False)

    def _schedule_background_validation(
        self,
        file_id: str,
        *,
        source_chat_id: Optional[int] = None,
        source_message_id: Optional[int] = None,
        media_index: int = 0,
        current_bot: str = "",
        file_type: str = "",
        log_extra: Optional[dict[str, Any]] = None,
    ) -> None:
        """Validate a cached file ID after responding to the user."""
        if not file_id:
            return

        bot_username = (getattr(self.bot, "username", None) or "").lower()
        cache_key = (bot_username, file_id)
        if cache_key in _background_validation_keys:
            return
        if len(_background_validation_tasks) >= _BACKGROUND_VALIDATION_MAX:
            logger.debug("Skipping background media validation; queue is full")
            return
        cached_at = _validated_file_ids.get(cache_key)
        if cached_at is not None:
            timestamp = cached_at[0] if isinstance(cached_at, tuple) else cached_at
            if _time.monotonic() - timestamp < _VALIDATED_FILE_ID_TTL_SECONDS:
                return

        async def _runner() -> None:
            try:
                validation = await self._validate_file_id(file_id)
                if validation.success:
                    if source_chat_id and source_message_id and file_type and current_bot:
                        await self._validated_file_matches_origin(
                            validation,
                            source_chat_id,
                            source_message_id,
                            media_index,
                            current_bot,
                            file_type,
                            log_extra or {},
                        )
                    return
                if _transient_validation_error(validation.error):
                    return
                reason = validation.error
            except Exception as exc:
                reason = str(exc)

            if reason:
                logger.warning(
                    "Background media validation failed",
                    extra={**(log_extra or {}), "reason": reason},
                )

        _background_validation_keys.add(cache_key)
        task = asyncio.create_task(_runner(), name="media_file_id_background_validate")
        _background_validation_tasks.add(task)

        def _cleanup(completed_task: asyncio.Task) -> None:
            _background_validation_tasks.discard(completed_task)
            _background_validation_keys.discard(cache_key)

        task.add_done_callback(_cleanup)

    async def _validated_file_matches_origin(
        self,
        validation: MediaResolutionResult,
        source_chat_id: int,
        source_message_id: int,
        media_index: int,
        current_bot: str,
        file_type: str,
        log_extra: dict[str, Any],
    ) -> bool:
        if not validation.file_unique_id:
            return True
        get_canonical = getattr(self.db, "get_canonical_media_by_origin", None)
        if get_canonical is None:
            return True
        canonical = await get_canonical(source_chat_id, source_message_id, file_type)
        if not canonical:
            return True
        expected_unique_id = canonical.get("file_unique_id")
        if not expected_unique_id or expected_unique_id == validation.file_unique_id:
            return True
        await self.db.delete_bot_file_id_cache_entry(
            source_chat_id, source_message_id, media_index, current_bot
        )
        logger.warning(
            "Cached file_id unique_id mismatch; evicting stale media cache",
            extra={
                **log_extra,
                "expected_file_unique_id": expected_unique_id,
                "actual_file_unique_id": validation.file_unique_id,
                "cache_hit": True,
            },
        )
        return False

    async def _rehydrate_with_retry(
        self,
        product: dict[str, Any],
        source_chat_id: int,
        source_message_id: int,
        file_type: str,
        media_index: int,
        current_bot: str,
        log_extra: dict[str, Any],
        force_refresh: bool = False,
    ) -> Optional[str]:
        for attempt in range(3):
            try:
                return await get_bot_specific_file_id(
                    self.bot,
                    source_chat_id,
                    source_message_id,
                    file_type,
                    file_index=media_index,
                    force_refresh=force_refresh,
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
