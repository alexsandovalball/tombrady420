"""
Broadcast chat membership handlers.

Handles ``my_chat_member`` updates so that the bot automatically registers
every group or channel it is added to as a broadcast destination.  Chats are
stored in the ``broadcast_chats`` table with ``enabled=0`` by default; an
administrator must explicitly run ``/enable_broadcasts`` to opt the chat in.

When the bot is removed or restricted in a group/channel the destination is
deleted so stale entries do not accumulate.
"""
import logging
from typing import Optional
from telegram import Update, ChatMemberUpdated, ChatMember
from telegram.ext import ContextTypes
from database import Database

logger = logging.getLogger(__name__)
db = Database()

# Statuses that mean the bot is present and can post messages
_ACTIVE_STATUSES = {
    ChatMember.ADMINISTRATOR,
    ChatMember.MEMBER,
}

# Statuses that mean the bot has been removed / restricted
_REMOVED_STATUSES = {
    ChatMember.BANNED,
    ChatMember.LEFT,
}


def _extract_status(member) -> str:
    """Return the member status string from a ChatMember object."""
    return member.status if member else ""


async def my_chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle ``my_chat_member`` updates (bot's own membership changes).

    - Bot added to a group/supergroup → register as broadcast destination (disabled).
    - Bot promoted to admin in a channel → register as broadcast destination (disabled).
    - Bot removed/banned from any chat → delete the destination.
    """
    result: ChatMemberUpdated = update.my_chat_member
    if result is None:
        return

    chat = result.chat
    new_member = result.new_chat_member
    new_status = _extract_status(new_member)
    old_status = _extract_status(result.old_chat_member)

    bot_username = context.bot.username
    chat_id = chat.id
    chat_type = chat.type  # "group" | "supergroup" | "channel" | "private"
    title = chat.title or chat.username or str(chat_id)
    added_by_user_id: Optional[int] = result.from_user.id if result.from_user else None

    # Skip private chats – broadcasts are only meaningful in groups/channels
    if chat_type == "private":
        return

    if new_status in _REMOVED_STATUSES:
        # Bot was kicked or left – remove the destination
        deleted = await db.delete_broadcast_chat(bot_username, chat_id)
        if deleted:
            logger.info(
                f"[my_chat_member] @{bot_username} removed from {chat_type} '{title}' "
                f"(chat_id={chat_id}) – broadcast destination deleted"
            )
        return

    if new_status in _ACTIVE_STATUSES and old_status not in _ACTIVE_STATUSES:
        # Bot newly joined or was promoted – register the destination
        await db.upsert_broadcast_chat(
            bot_username=bot_username,
            chat_id=chat_id,
            chat_type=chat_type,
            title=title,
            added_by_user_id=added_by_user_id,
        )
        logger.info(
            f"[my_chat_member] @{bot_username} added to {chat_type} '{title}' "
            f"(chat_id={chat_id}) – registered as broadcast destination (disabled by default)"
        )
