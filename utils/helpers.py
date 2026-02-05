"""
Helper utilities for the bot.
"""
import logging
import json
from typing import List, Optional, Dict, Tuple
from telegram import Update, User
from telegram.ext import ContextTypes

from configs.config import Config

logger = logging.getLogger(__name__)

# Cache for bot-specific file IDs to avoid redundant forwarding
# Format: {(source_chat_id, source_message_id, file_index): file_id}
_file_id_cache: Dict[Tuple[int, int, int], str] = {}

# Admin commands fallback text constant
ADMIN_COMMANDS_FALLBACK = (
    "\n\n👨‍💼 Admin Commands:\n"
    "• /start - Welcome message\n"
    "• /menu - View catalog\n"
    "• /users - Manage users\n"
    "• /send - Send message to user\n"
    "• /broadcast - Broadcast to all users\n"
    "• /setcontact - Set order contact\n"
    "• /recategorize - Recategorize products\n"
    "• /nuke - Delete all products"
)


def escape_markdown_v1(text: str) -> str:
    """
    Escape special characters for Telegram's legacy Markdown (v1) format.
    
    In Markdown v1, only these characters need escaping:
    - _ (underscore) for italic
    - * (asterisk) for bold
    - ` (backtick) for code
    - [ (left bracket) for links
    
    Args:
        text: Text to escape
        
    Returns:
        Escaped text safe for Markdown v1
    """
    # Escape special Markdown v1 characters
    # Note: We don't escape backslash itself as user names typically don't contain them,
    # and escaping backslash would require escaping it first to avoid double-escaping
    text = text.replace('_', r'\_')
    text = text.replace('*', r'\*')
    text = text.replace('`', r'\`')
    text = text.replace('[', r'\[')
    return text


def get_user_display_name(user: User, escaped: bool = True) -> str:
    """
    Get a user's display name with fallback logic.
    
    Args:
        user: Telegram User object
        escaped: Whether to escape markdown characters for Markdown v1 (default: True)
    
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
    
    return escape_markdown_v1(raw_display_name) if escaped else raw_display_name


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


async def get_bot_specific_file_id(
    context: ContextTypes.DEFAULT_TYPE,
    source_chat_id: int,
    source_message_id: int,
    file_type: str,
    file_index: int = 0
) -> Optional[str]:
    """
    Get bot-specific file ID by forwarding a message from the source chat.
    This is necessary when a secondary bot needs to display media that was
    originally posted to a channel using a different bot.
    
    Args:
        context: Bot context
        source_chat_id: ID of the source chat (usually the channel)
        source_message_id: ID of the specific message containing the media
        file_type: Type of file (photo, video, document, etc.)
        file_index: Index of the file (0 for single media, 0..n for media groups)
    
    Returns:
        Bot-specific file ID or None if forwarding fails
    """
    # Check cache first
    cache_key = (source_chat_id, source_message_id, file_index)
    if cache_key in _file_id_cache:
        logger.debug(f"Using cached file ID for message {source_message_id}, index {file_index}")
        return _file_id_cache[cache_key]
    
    try:
        # Forward the message to get the bot-specific file ID
        # We forward to one of the admin users (they have permission to receive)
        admin_ids = get_admin_ids()
        if not admin_ids:
            logger.error("No admin IDs configured - cannot get bot-specific file ID")
            return None
        
        target_chat_id = admin_ids[0]
        
        # Forward the message
        forwarded = await context.bot.forward_message(
            chat_id=target_chat_id,
            from_chat_id=source_chat_id,
            message_id=source_message_id
        )
        
        # Extract the file ID from the forwarded message
        file_id = None
        
        if file_type == "photo" and forwarded.photo:
            file_id = forwarded.photo[-1].file_id
        elif file_type == "video" and forwarded.video:
            file_id = forwarded.video.file_id
        elif file_type == "document" and forwarded.document:
            file_id = forwarded.document.file_id
        elif file_type == "animation" and forwarded.animation:
            file_id = forwarded.animation.file_id
        elif file_type == "video_note" and forwarded.video_note:
            file_id = forwarded.video_note.file_id
        elif file_type == "audio" and forwarded.audio:
            file_id = forwarded.audio.file_id
        elif file_type == "voice" and forwarded.voice:
            file_id = forwarded.voice.file_id
        
        if file_id:
            # Cache the result
            _file_id_cache[cache_key] = file_id
            logger.debug(f"Cached bot-specific file ID for message {source_message_id}, index {file_index}")
            return file_id
        else:
            logger.warning(f"Could not extract file ID from forwarded message {source_message_id}")
            return None
            
    except Exception as e:
        logger.error(f"Error getting bot-specific file ID: {e}")
        return None

