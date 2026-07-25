"""Shared throttling helpers for Telegram Bot API file fetches."""
from __future__ import annotations

import asyncio
import logging
import os
import random
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
_telegram_file_fetch_semaphore = asyncio.Semaphore(TELEGRAM_FILE_FETCH_CONCURRENCY)


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

    async with _telegram_file_fetch_semaphore:
        last_exc: Exception | None = None
        for attempt in range(attempts + 1):
            try:
                return await asyncio.wait_for(bot.get_file(file_id), timeout=timeout)
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
