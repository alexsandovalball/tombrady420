"""Shared throttling helpers for Telegram Bot API file fetches."""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from collections import OrderedDict
from typing import Any, Optional

from telegram import Bot
from telegram.error import RetryAfter, TelegramError, TimedOut

logger = logging.getLogger(__name__)


def _bounded_int_env(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.getenv(name)
    try:
        value = int(raw) if raw is not None else default
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


TELEGRAM_FILE_FETCH_CONCURRENCY = _bounded_int_env("TELEGRAM_FILE_FETCH_CONCURRENCY", 3, 1, 8)
TELEGRAM_FILE_FETCH_TIMEOUT_SECONDS = float(_bounded_int_env("TELEGRAM_FILE_FETCH_TIMEOUT_SECONDS", 8, 2, 30))
TELEGRAM_FILE_FETCH_CACHE_TTL_SECONDS = float(_bounded_int_env("TELEGRAM_FILE_FETCH_CACHE_TTL_SECONDS", 90, 5, 600))
TELEGRAM_FILE_FETCH_CACHE_MAX = _bounded_int_env("TELEGRAM_FILE_FETCH_CACHE_MAX", 512, 64, 4096)
_telegram_file_fetch_semaphore = asyncio.Semaphore(TELEGRAM_FILE_FETCH_CONCURRENCY)
_telegram_file_fetch_cache: OrderedDict[tuple[int, str], tuple[float, Any]] = OrderedDict()
_telegram_file_fetch_inflight: dict[tuple[int, str], asyncio.Task] = {}
_telegram_file_fetch_lock = asyncio.Lock()


def _bot_fetch_key(bot: Bot, file_id: str) -> tuple[int, str]:
    return (id(bot), file_id)


async def _get_cached_fetch_result(key: tuple[int, str]) -> Any | None:
    async with _telegram_file_fetch_lock:
        cached = _telegram_file_fetch_cache.get(key)
        if cached is None:
            return None
        created_at, result = cached
        if time.monotonic() - created_at >= TELEGRAM_FILE_FETCH_CACHE_TTL_SECONDS:
            _telegram_file_fetch_cache.pop(key, None)
            return None
        _telegram_file_fetch_cache.move_to_end(key)
        return result


async def _remember_fetch_result(key: tuple[int, str], result: Any) -> None:
    async with _telegram_file_fetch_lock:
        _telegram_file_fetch_cache[key] = (time.monotonic(), result)
        _telegram_file_fetch_cache.move_to_end(key)
        while len(_telegram_file_fetch_cache) > TELEGRAM_FILE_FETCH_CACHE_MAX:
            _telegram_file_fetch_cache.popitem(last=False)


async def _run_fetch(
    bot: Bot,
    file_id: str,
    *,
    context: str,
    attempts: int,
    timeout: float,
) -> Any:
    async with _telegram_file_fetch_semaphore:
        last_exc: Exception | None = None
        for attempt in range(attempts + 1):
            try:
                result = await asyncio.wait_for(bot.get_file(file_id), timeout=timeout)
                await _remember_fetch_result(_bot_fetch_key(bot, file_id), result)
                return result
            except RetryAfter as exc:
                last_exc = exc
                delay = max(0.5, float(getattr(exc, "retry_after", 1) or 1))
                logger.warning(
                    "[telegram_fetch] retry_after context=%s attempt=%d delay=%.2f",
                    context,
                    attempt + 1,
                    delay,
                )
                if attempt >= attempts:
                    raise
                await asyncio.sleep(delay)
            except (asyncio.TimeoutError, TimedOut) as exc:
                last_exc = exc
                if attempt >= attempts:
                    raise
                delay = 0.25 * (attempt + 1) + random.uniform(0.0, 0.25)
                logger.warning(
                    "[telegram_fetch] timeout retry context=%s attempt=%d delay=%.2f error=%s",
                    context,
                    attempt + 1,
                    delay,
                    exc,
                )
                await asyncio.sleep(delay)
            except TelegramError:
                raise
        if last_exc is not None:
            raise last_exc
        raise RuntimeError(f"Telegram file fetch failed unexpectedly for {context}")


async def fetch_telegram_file(
    bot: Bot,
    file_id: str,
    *,
    context: str = "telegram_file_fetch",
    retries: int = 1,
    timeout_seconds: Optional[float] = None,
) -> Any:
    """Fetch a Telegram file with a small global concurrency gate and one transient retry.

    The gate keeps bursts of browser media requests from exhausting the bot's HTTP pool.
    """
    timeout = float(timeout_seconds or TELEGRAM_FILE_FETCH_TIMEOUT_SECONDS)
    attempts = max(0, int(retries))
    key = _bot_fetch_key(bot, file_id)

    cached = await _get_cached_fetch_result(key)
    if cached is not None:
        logger.debug("[telegram_fetch] cache_hit context=%s", context)
        return cached

    async with _telegram_file_fetch_lock:
        inflight = _telegram_file_fetch_inflight.get(key)
        if inflight is None:
            inflight = asyncio.create_task(
                _run_fetch(
                    bot,
                    file_id,
                    context=context,
                    attempts=attempts,
                    timeout=timeout,
                ),
                name=f"telegram_file_fetch:{file_id[:24]}",
            )
            _telegram_file_fetch_inflight[key] = inflight

    try:
        return await inflight
    finally:
        async with _telegram_file_fetch_lock:
            current = _telegram_file_fetch_inflight.get(key)
            if current is inflight:
                _telegram_file_fetch_inflight.pop(key, None)
