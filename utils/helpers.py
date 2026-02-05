"""
Helper utilities for the bot.
"""
import logging
from typing import List, Optional
from telegram import Update, User
from telegram.ext import ContextTypes

from configs.config import Config

logger = logging.getLogger(__name__)

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
    product_id: int,
    original_file_id: str,
    file_type: str,
    source_chat_id: int,
    source_message_id: int,
    file_index: int = 0
) -> str:
    """
    Get or create a bot-specific file_id for media.
    
    Since Telegram file IDs are bot-specific, this function:
    1. Checks if we have a cached file_id for this bot instance
    2. If not, tries to use the original file_id (may fail on secondary bots)
    3. On failure, downloads and re-uploads the media to get a new file_id
    4. Caches the new file_id for future use
    
    Args:
        context: The bot context
        product_id: Product database ID
        original_file_id: The original file_id (from bot 1 or channel)
        file_type: Type of media (photo, video, etc.)
        source_chat_id: Chat ID where the original media is located
        source_message_id: Message ID in the source chat
        file_index: Index of the file in a media group (0 for single media)
    
    Returns:
        A file_id valid for the current bot instance
    """
    from database import Database
    from configs.config import Config
    db = Database()
    
    # Get the current bot instance from context
    bot_instance = context.bot_data.get('bot_instance', 1)
    
    # Check if we have a cached file_id for this bot instance
    cached_files = await db.get_cached_file_ids(product_id, bot_instance)
    
    if cached_files and len(cached_files) > file_index:
        cached_file_id, cached_file_type = cached_files[file_index]
        logger.debug(f"Using cached file_id for product {product_id}, bot {bot_instance}, index {file_index}")
        return cached_file_id
    
    # No cache - for bot instance 1, the original file_id should work
    if bot_instance == 1:
        # Cache it for future use
        if not cached_files:
            await db.cache_file_ids(product_id, bot_instance, [(original_file_id, file_type)])
        return original_file_id
    
    # For secondary bots, we need to get a new file_id
    # Try using copy_message to get a bot-specific file_id
    try:
        # Get the first admin to use as temporary destination
        admin_ids = Config.ADMIN_IDS
        if not admin_ids:
            logger.warning(f"No admin IDs configured, falling back to original file_id")
            return original_file_id
        
        admin_chat_id = admin_ids[0]
        
        # Copy the message from source channel to admin chat
        # This gives us a new file_id valid for this bot
        copied_msg = await context.bot.copy_message(
            chat_id=admin_chat_id,
            from_chat_id=source_chat_id,
            message_id=source_message_id,
            disable_notification=True
        )
        
        # Extract the new file_id from the copied message
        new_file_id = None
        if hasattr(copied_msg, 'photo') and copied_msg.photo:
            new_file_id = copied_msg.photo[-1].file_id
        elif hasattr(copied_msg, 'video') and copied_msg.video:
            new_file_id = copied_msg.video.file_id
        elif hasattr(copied_msg, 'animation') and copied_msg.animation:
            new_file_id = copied_msg.animation.file_id
        elif hasattr(copied_msg, 'document') and copied_msg.document:
            new_file_id = copied_msg.document.file_id
        
        if new_file_id:
            # Cache the new file_id
            await db.cache_file_ids(product_id, bot_instance, [(new_file_id, file_type)])
            logger.info(f"Cached new file_id for product {product_id}, bot {bot_instance}")
            
            # Delete the copied message to clean up admin chat
            try:
                await context.bot.delete_message(chat_id=admin_chat_id, message_id=copied_msg.message_id)
            except Exception as del_error:
                logger.debug(f"Could not delete copied message from admin chat: {del_error}")
            
            return new_file_id
        else:
            # Fallback: return original file_id and hope it works
            logger.warning(f"Could not extract file_id from copied message for product {product_id}")
            return original_file_id
            
    except Exception as e:
        logger.error(f"Error getting bot-specific file_id: {e}")
        # Fallback: return original file_id
        return original_file_id


