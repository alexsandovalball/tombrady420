# utils/helpers.py
import os
import logging
from typing import Tuple, Optional, Any
from telegram import Update, Message

logger = logging.getLogger(__name__)


def get_channel_id() -> Optional[int]:
    """Return CHANNEL_ID from env as int, or None if not set or invalid."""
    raw = os.getenv("CHANNEL_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning("Invalid CHANNEL_ID in environment: %r", raw)
        return None


def get_channel_username() -> Optional[str]:
    """Return CHANNEL_USERNAME from env normalized with leading @, or None if not set."""
    username = os.getenv("CHANNEL_USERNAME", "").strip()
    if not username:
        return None
    if not username.startswith("@"):
        username = f"@{username}"
    return username


def has_media(update: Update) -> bool:
    """
    Return True if the update contains any supported media type.
    Recognizes photos, videos, documents, animations (gifs), audio and voice.
    """
    msg = _get_message(update)
    if not msg:
        return False
    return bool(
        (getattr(msg, "photo", None) and len(msg.photo) > 0)
        or getattr(msg, "video", None)
        or getattr(msg, "document", None)
        or getattr(msg, "animation", None)
        or getattr(msg, "audio", None)
        or getattr(msg, "voice", None)
    )


def get_file_id_and_type(update: Update) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """
    Extract a single file_id and its type from the update's message.
    Returns a tuple: (file_id, file_type, media_group_id).
    - file_type is one of: "photo", "video", "document", "animation", "audio", "voice"
    - media_group_id is returned unchanged (may be None)
    If no supported media is found, returns (None, None, media_group_id).
    """
    msg = _get_message(update)
    if not msg:
        return None, None, None

    # Preserve media_group_id exactly as provided by Telegram (may be None)
    mgid = getattr(msg, "media_group_id", None)

    # Photos: choose the highest-resolution variant (last in the list)
    if getattr(msg, "photo", None):
        try:
            # msg.photo is a list of PhotoSize objects; choose the largest (last)
            return msg.photo[-1].file_id, "photo", mgid
        except Exception:
            logger.exception("Failed to extract photo file_id")
            return None, None, mgid

    if getattr(msg, "video", None):
        try:
            return msg.video.file_id, "video", mgid
        except Exception:
            logger.exception("Failed to extract video file_id")
            return None, None, mgid

    if getattr(msg, "document", None):
        try:
            return msg.document.file_id, "document", mgid
        except Exception:
            logger.exception("Failed to extract document file_id")
            return None, None, mgid

    if getattr(msg, "animation", None):
        try:
            return msg.animation.file_id, "animation", mgid
        except Exception:
            logger.exception("Failed to extract animation file_id")
            return None, None, mgid

    if getattr(msg, "audio", None):
        try:
            return msg.audio.file_id, "audio", mgid
        except Exception:
            logger.exception("Failed to extract audio file_id")
            return None, None, mgid

    if getattr(msg, "voice", None):
        try:
            return msg.voice.file_id, "voice", mgid
        except Exception:
            logger.exception("Failed to extract voice file_id")
            return None, None, mgid

    # No supported media found; return mgid so callers can still use it if present
    return None, None, mgid


def _get_message(update: Update) -> Optional[Message]:
    """
    Return the most relevant Message object from an Update.
    Checks in order: message, edited_message, channel_post, edited_channel_post, callback_query.message.
    """
    if update is None:
        return None
    # Prefer explicit attributes in the order most likely to contain user/channel content
    if getattr(update, "message", None):
        return update.message
    if getattr(update, "edited_message", None):
        return update.edited_message
    if getattr(update, "channel_post", None):
        return update.channel_post
    if getattr(update, "edited_channel_post", None):
        return update.edited_channel_post
    if getattr(update, "callback_query", None) and getattr(update.callback_query, "message", None):
        return update.callback_query.message
    return None


def is_admin(user_id: int) -> bool:
    """
    Check whether a user_id is listed in ADMIN_IDS environment variable.
    ADMIN_IDS should be a comma-separated list of numeric IDs, e.g. "12345,67890".
    Returns False if ADMIN_IDS is not set or contains invalid entries.
    """
    raw = os.getenv("ADMIN_IDS", "").strip()
    if not raw:
        return False
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    ids = []
    for p in parts:
        try:
            ids.append(int(p))
        except ValueError:
            logger.warning("Invalid ADMIN_IDS entry ignored: %r", p)
    return user_id in ids