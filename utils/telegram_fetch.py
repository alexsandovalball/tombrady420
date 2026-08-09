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
TELEGRAM_FILE_FETCH_FAILURE_COOLDOWN_SECONDS = float(
    _bounded_int_env("TELEGRAM_FILE_FETCH_FAILURE_COOLDOWN_SECONDS", 8, 1, 30)
)
_telegram_file_fetch_semaphore = asyncio.Semaphore(TELEGRAM_FILE_FETCH_CONCURRENCY)
_telegram_file_fetch_cache: OrderedDict[tuple[int, str], tuple[float, Any]] = OrderedDict()
_telegram_file_fetch_failures: OrderedDict[tuple[int, str], tuple[float, str, str, float]] = OrderedDict()
_telegram_file_fetch_inflight: dict[tuple[int, str], asyncio.Task] = {}
_telegram_file_fetch_lock = asyncio.Lock()
_telegram_file_fetch_active = 0
_telegram_file_fetch_started = 0
_telegram_file_fetch_completed = 0
_telegram_file_fetch_failed = 0


def _bot_fetch_key(bot: Bot, file_id: str) -> tuple[int, str]:
    return (id(bot), file_id)


async def _remove_inflight_fetch(key: tuple[int, str], task: asyncio.Task) -> None:
    async with _telegram_file_fetch_lock:
        if _telegram_file_fetch_inflight.get(key) is task:
            _telegram_file_fetch_inflight.pop(key, None)


def _finish_inflight_fetch(task: asyncio.Task, key: tuple[int, str]) -> None:
    # Retrieve the exception so a timed-out caller cannot create an unhandled
    # task warning. The original awaiters still receive the same exception.
    try:
        task.exception()
    except BaseException:
        pass
    try:
        asyncio.get_running_loop().create_task(_remove_inflight_fetch(key, task))
    except RuntimeError:
        pass


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
        _telegram_file_fetch_failures.pop(key, None)
        while len(_telegram_file_fetch_cache) > TELEGRAM_FILE_FETCH_CACHE_MAX:
            _telegram_file_fetch_cache.popitem(last=False)


async def _get_recent_fetch_failure(key: tuple[int, str]) -> tuple[str, str, float] | None:
    async with _telegram_file_fetch_lock:
        cached = _telegram_file_fetch_failures.get(key)
        if cached is None:
            return None
        expires_at, failure_kind, message, retry_after = cached
        if time.monotonic() >= expires_at:
            _telegram_file_fetch_failures.pop(key, None)
            return None
        _telegram_file_fetch_failures.move_to_end(key)
        return failure_kind, message, retry_after


async def _remember_fetch_failure(
    key: tuple[int, str],
    *,
    failure_kind: str,
    message: str,
    retry_after: float = 0.0,
    ttl_seconds: Optional[float] = None,
) -> None:
    cooldown = max(1.0, float(ttl_seconds or TELEGRAM_FILE_FETCH_FAILURE_COOLDOWN_SECONDS))
    async with _telegram_file_fetch_lock:
        _telegram_file_fetch_failures[key] = (
            time.monotonic() + cooldown,
            failure_kind,
            message,
            max(0.0, float(retry_after or 0.0)),
        )
        _telegram_file_fetch_failures.move_to_end(key)
        while len(_telegram_file_fetch_failures) > TELEGRAM_FILE_FETCH_CACHE_MAX:
            _telegram_file_fetch_failures.popitem(last=False)


def _raise_recent_fetch_failure(failure_kind: str, message: str, retry_after: float) -> None:
    if failure_kind == "retry_after":
        raise RetryAfter(retry_after or 1)
    raise TimedOut(message or "Recent Telegram file fetch timed out")


async def _run_fetch(
    bot: Bot,
    file_id: str,
    *,
    context: str,
    attempts: int,
    timeout: float,
) -> Any:
    global _telegram_file_fetch_active, _telegram_file_fetch_started
    global _telegram_file_fetch_failed, _telegram_file_fetch_completed
    async with _telegram_file_fetch_semaphore:
        _telegram_file_fetch_active += 1
        _telegram_file_fetch_started += 1
        try:
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
                        await _remember_fetch_failure(
                            _bot_fetch_key(bot, file_id),
                            failure_kind="retry_after",
                            message=str(exc),
                            retry_after=delay,
                            ttl_seconds=min(max(delay, 1.0), TELEGRAM_FILE_FETCH_FAILURE_COOLDOWN_SECONDS),
                        )
                        raise
                    await asyncio.sleep(delay)
                except (asyncio.TimeoutError, TimedOut) as exc:
                    last_exc = exc
                    if attempt >= attempts:
                        await _remember_fetch_failure(
                            _bot_fetch_key(bot, file_id),
                            failure_kind="timeout",
                            message=str(exc),
                        )
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
        except BaseException:
            _telegram_file_fetch_failed += 1
            raise
        finally:
            _telegram_file_fetch_active = max(0, _telegram_file_fetch_active - 1)
            _telegram_file_fetch_completed += 1


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

    recent_failure = await _get_recent_fetch_failure(key)
    if recent_failure is not None:
        failure_kind, message, retry_after = recent_failure
        logger.debug("[telegram_fetch] failure_cooldown_hit context=%s kind=%s", context, failure_kind)
        _raise_recent_fetch_failure(failure_kind, message, retry_after)

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
            inflight.add_done_callback(lambda task, fetch_key=key: _finish_inflight_fetch(task, fetch_key))

    # One browser request timing out must not cancel the shared fetch for other
    # callers, nor interrupt its eventual cache/update cleanup.
    return await asyncio.shield(inflight)


def get_telegram_fetch_snapshot() -> dict[str, int]:
    """Return bounded fetch counters for the admin/runtime monitor.

    These counters are intentionally process-local and diagnostic only. They
    avoid retaining request objects, exceptions, or media data in memory.
    """
    return {
        "active": _telegram_file_fetch_active,
        "inflight": len(_telegram_file_fetch_inflight),
        "started": _telegram_file_fetch_started,
        "completed": _telegram_file_fetch_completed,
        "failed": _telegram_file_fetch_failed,
        "result_cache_entries": len(_telegram_file_fetch_cache),
        "failure_cache_entries": len(_telegram_file_fetch_failures),
        "concurrency_limit": TELEGRAM_FILE_FETCH_CONCURRENCY,
    }
