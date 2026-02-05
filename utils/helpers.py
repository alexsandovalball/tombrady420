"""
Helper utilities for the bot.
"""
import logging
from typing import List, Optional
from telegram import Update, User
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from configs.config import Config

logger = logging.getLogger(__name__)

# Admin commands fallback text constant
ADMIN_COMMANDS_FALLBACK = (
    "\n\n👨‍💼 Admin Commands:\n"
    "• /menu - View catalog\n"
    "• /users - Manage users\n"
    "• /send - Send message to user\n"
    "• /broadcast - Broadcast to all users\n"
    "• /setcontact - Set order contact\n"
    "• /recategorize - Recategorize products\n"
    "• /nuke - Delete all products"
)


def get_user_display_name(user: User, escaped: bool = True) -> str:
    """
    Get a user's display name with fallback logic.
    
    Args:
        user: Telegram User object
        escaped: Whether to escape markdown characters (default: True)
    
    Returns:
        User's display name (first + last name, or first name, or username, or "there")
    """
    if user.first_name and user.last_name:
        raw_display_name = f"{user.first_name} {user.last_name}"
    elif user.first_name:
        raw_display_name = user.first_name
    elif user.username:
        raw_display_name = user.username
    else:
        raw_display_name = "there"
    
    return escape_markdown(raw_display_name) if escaped else raw_display_name


def get_admin_ids() -> List[int]:
    """Get admin user IDs from configuration."""
    return Config.ADMIN_IDS


def is_admin(user_id: int) -> bool:
    """Check if a user is an admin."""
    return user_id in get_admin_ids()


def get_channel_id() -> Optional[int]:
    """Get channel ID from configuration."""
    return Config.CHANNEL_ID


def get_channel_username() -> Optional[str]:
    """Get channel username from configuration."""
    username = Config.CHANNEL_USERNAME
    if username and not username.startswith("@"):
        username = "@" + username
    return username if username else None


def get_file_id_and_type(update: Update) -> tuple[Optional[str], Optional[str]]:
    """
    Extract file_id and file_type from a message.
    Returns (file_id, file_type) or (None, None) if no media found.
    """
    message = update.channel_post or update.message
    if not message:
        return None, None
    
    # Check for photo (get highest resolution)
    if message.photo:
        file_id = message.photo[-1].file_id
        return file_id, "photo"
    
    # Check for video
    if message.video:
        return message.video.file_id, "video"
    
    # Check for document
    if message.document:
        return message.document.file_id, "document"
    
    # Check for animation (GIF)
    if message.animation:
        return message.animation.file_id, "animation"
    
    # Check for video note
    if message.video_note:
        return message.video_note.file_id, "video_note"
    
    # Check for audio
    if message.audio:
        return message.audio.file_id, "audio"
    
    # Check for voice
    if message.voice:
        return message.voice.file_id, "voice"
    
    return None, None


def has_media(update: Update) -> bool:
    """Check if message contains media."""
    file_id, _ = get_file_id_and_type(update)
    return file_id is not None


async def send_media_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    file_id: str,
    file_type: str,
    caption: str = None,
    reply_markup=None
):
    """
    Send a media message based on file type.
    """
    try:
        if file_type == "photo":
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=file_id,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=None
            )
        elif file_type == "video":
            await context.bot.send_video(
                chat_id=chat_id,
                video=file_id,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=None
            )
        elif file_type == "animation":
            await context.bot.send_animation(
                chat_id=chat_id,
                animation=file_id,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=None
            )
        elif file_type in ["document", "audio", "voice", "video_note"]:
            await context.bot.send_document(
                chat_id=chat_id,
                document=file_id,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=None
            )
        else:
            # Fallback to text message
            await context.bot.send_message(
                chat_id=chat_id,
                text=caption or "Media",
                reply_markup=reply_markup
            )
    except Exception as e:
        logger.error(f"Error sending media message: {e}")
        # Fallback to text message
        await context.bot.send_message(
            chat_id=chat_id,
            text=f"{caption or 'Media'}\n\n⚠️ Error displaying media. File ID: {file_id}",
            reply_markup=reply_markup
        )

