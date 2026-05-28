"""Bot readiness checks for multi-bot media delivery."""
from __future__ import annotations

import logging

from telegram.error import Forbidden, TelegramError

logger = logging.getLogger(__name__)


async def ensure_admin_conversation(bot, admin_id: int) -> bool:
    """Return True when the bot can message the admin used for media rehydration."""
    try:
        msg = await bot.send_message(
            chat_id=admin_id,
            text="Bot media cache readiness check.",
            disable_notification=True,
        )
        try:
            await bot.delete_message(chat_id=admin_id, message_id=msg.message_id)
        except TelegramError:
            logger.debug("Unable to delete admin readiness check message", exc_info=True)
        return True
    except Forbidden as exc:
        logger.error(
            "Admin has not opened bot. Media forwarding cache will fail until admin opens the bot.",
            extra={"admin_id": admin_id, "bot_username": getattr(bot, "username", None)},
        )
        logger.debug("Admin conversation Forbidden detail: %s", exc)
        return False
    except TelegramError as exc:
        logger.warning(
            "Admin conversation readiness check failed",
            extra={"admin_id": admin_id, "bot_username": getattr(bot, "username", None), "reason": str(exc)},
        )
        return False


async def validate_bot_readiness(bot, admin_ids: list[int]) -> dict:
    """Validate one bot and return a compact readiness report."""
    report = {
        "bot_username": getattr(bot, "username", None),
        "admin_conversations": {},
        "ready": True,
    }
    for admin_id in admin_ids:
        ok = await ensure_admin_conversation(bot, admin_id)
        report["admin_conversations"][admin_id] = ok
        if not ok:
            report["ready"] = False
    logger.info("Bot readiness report", extra=report)
    return report
