# utils/helpers.py
import os
from typing import Tuple, Optional
from telegram import Update, Message

def get_channel_id() -> Optional[int]:
    raw = os.getenv("CHANNEL_ID", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None

def get_channel_username() -> Optional[str]:
    username = os.getenv("CHANNEL_USERNAME", "").strip()
    if not username:
        return None
    if not username.startswith("@"):
        username = f"@{username}"
    return username

def has_media(update: Update) -> bool:
    msg = _get_message(update)
    if not msg:
        return False
    return bool(
        (getattr(msg, "photo", None) and len(msg.photo) > 0)
        or getattr(msg, "video", None)
        or getattr(msg, "document", None)
        or getattr(msg, "animation", None)
    )

def get_file_id_and_type(update: Update) -> Tuple[Optional[str], Optional[str]]:
    msg = _get_message(update)
    if not msg:
        return None, None
    if getattr(msg, "photo", None):
        return msg.photo[-1].file_id, "photo"
    if getattr(msg, "video", None):
        return msg.video.file_id, "video"
    if getattr(msg, "document", None):
        return msg.document.file_id, "document"
    if getattr(msg, "animation", None):
        return msg.animation.file_id, "animation"
    return None, None

def _get_message(update: Update) -> Optional[Message]:
    if update.message:
        return update.message
    if update.channel_post:
        return update.channel_post
    if update.callback_query and update.callback_query.message:
        return update.callback_query.message
    return None

def is_admin(user_id: int) -> bool:
    """
    Check whether a user_id is listed in ADMIN_IDS environment variable.
    ADMIN_IDS should be a comma-separated list of numeric IDs, e.g. "12345,67890".
    """
    raw = os.getenv("ADMIN_IDS", "").strip()
    if not raw:
        return False
    try:
        ids = [int(x.strip()) for x in raw.split(",") if x.strip()]
    except ValueError:
        return False
    return user_id in ids
