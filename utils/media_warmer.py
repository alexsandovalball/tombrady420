"""Background media cache warm-up for product deep links."""
from __future__ import annotations

import asyncio
import logging
from typing import Iterable, Optional

from telegram.ext import Application

from database import Database
from utils.media_resolver import MediaResolver, collect_product_media

logger = logging.getLogger(__name__)

_warm_locks: dict[tuple[int, str], asyncio.Lock] = {}
_warm_semaphore = asyncio.Semaphore(2)


def _bot_username(bot_app: Application) -> str:
    bot = getattr(bot_app, "bot", None)
    return (getattr(bot, "username", None) or "").lower().lstrip("@")


async def warm_product_media_for_bot(
    product_id: int,
    bot_app: Application,
    *,
    db: Optional[Database] = None,
    max_media: int = 4,
) -> dict[str, int]:
    """Resolve and cache product media IDs for one bot without blocking handlers."""
    bot_username = _bot_username(bot_app)
    if not bot_username:
        return {"warmed": 0, "failed": 0}

    lock_key = (int(product_id), bot_username)
    lock = _warm_locks.get(lock_key)
    if lock is None:
        lock = asyncio.Lock()
        _warm_locks[lock_key] = lock

    async with lock:
        async with _warm_semaphore:
            database = db or Database()
            product = await database.get_product(product_id)
            if not product:
                return {"warmed": 0, "failed": 0}

            media = collect_product_media(product)[:max_media]
            if not media:
                return {"warmed": 0, "failed": 0}

            resolver = MediaResolver(bot_app.bot, database)
            results = await asyncio.gather(
                *[
                    resolver.resolve_media_for_bot(product, bot_username, idx)
                    for idx, _ in enumerate(media)
                ],
                return_exceptions=True,
            )

            warmed = 0
            failed = 0
            for result in results:
                if isinstance(result, Exception):
                    failed += 1
                elif result.success and result.file_id:
                    warmed += 1
                else:
                    failed += 1

            logger.info(
                "[media_warm] product=%s bot=@%s warmed=%s failed=%s",
                product_id,
                bot_username,
                warmed,
                failed,
            )
            return {"warmed": warmed, "failed": failed}


async def warm_product_media_for_bots(
    product_id: int,
    bot_apps: Iterable[Application],
    *,
    db: Optional[Database] = None,
    max_bots: int = 4,
    max_media: int = 4,
) -> dict[str, int]:
    """Warm product media for a small set of active bot applications."""
    unique_apps: list[Application] = []
    seen: set[str] = set()
    for bot_app in bot_apps or []:
        username = _bot_username(bot_app)
        if not username or username in seen:
            continue
        seen.add(username)
        unique_apps.append(bot_app)
        if len(unique_apps) >= max_bots:
            break

    if not unique_apps:
        return {"bots": 0, "warmed": 0, "failed": 0}

    results = await asyncio.gather(
        *[
            warm_product_media_for_bot(product_id, bot_app, db=db, max_media=max_media)
            for bot_app in unique_apps
        ],
        return_exceptions=True,
    )

    warmed = 0
    failed = 0
    for result in results:
        if isinstance(result, Exception):
            failed += 1
            logger.warning("[media_warm] product=%s error=%s", product_id, result)
        else:
            warmed += int(result.get("warmed", 0))
            failed += int(result.get("failed", 0))

    return {"bots": len(unique_apps), "warmed": warmed, "failed": failed}


def schedule_product_media_warm(
    product_id: int,
    bot_apps: Iterable[Application],
    *,
    db: Optional[Database] = None,
    max_bots: int = 4,
    max_media: int = 4,
) -> Optional[asyncio.Task]:
    """Start best-effort media warm-up and never raise into the caller."""
    apps = list(bot_apps or [])
    if not apps:
        return None

    async def _runner() -> None:
        try:
            stats = await warm_product_media_for_bots(
                product_id,
                apps,
                db=db,
                max_bots=max_bots,
                max_media=max_media,
            )
            logger.info(
                "[media_warm] complete product=%s bots=%s warmed=%s failed=%s",
                product_id,
                stats.get("bots", 0),
                stats.get("warmed", 0),
                stats.get("failed", 0),
            )
        except Exception as exc:
            logger.warning("[media_warm] failed product=%s error=%s", product_id, exc, exc_info=True)

    return asyncio.create_task(_runner())
