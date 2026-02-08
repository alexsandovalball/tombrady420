"""
Helper utilities for the bot.
"""
import logging
import json
import asyncio
import aiohttp
import io
from typing import List, Optional, Dict, Tuple
from telegram import Update, User, Bot, InputFile
from telegram.ext import ContextTypes
from telegram.error import Forbidden, BadRequest

from configs.config import Config
from utils.bot_key import is_primary_bot_process

logger = logging.getLogger(__name__)


def is_primary_bot_instance(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Check if this is the primary bot instance.
    
    The primary bot is identified by having the 'BOT_TOKEN' environment variable
    (without numeric suffix). Uses is_primary_bot_process() for consistent detection.
    
    Used for operations that should only happen once across all bot instances,
    such as sending categorization prompts to admins.
    
    Args:
        context: Telegram context with bot information
        
    Returns:
        True if this is the primary instance, False otherwise
    """
    is_primary = is_primary_bot_process()
    
    bot_username = context.bot.username if hasattr(context.bot, 'username') else 'unknown'
    
    if is_primary:
        logger.debug(f"[Bot @{bot_username}] Primary instance check: PASS (BOT_TOKEN env var present)")
    else:
        logger.debug(f"[Bot @{bot_username}] Primary instance check: FAIL (secondary bot - using BOT_TOKEN_N)")
    
    return is_primary


# Cache for bot-specific file IDs to avoid redundant forwarding
# Format: {(source_chat_id, source_message_id, file_index): file_id}
_file_id_cache: Dict[Tuple[int, int, int], str] = {}
_file_id_cache_lock = asyncio.Lock()

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


async def download_media_from_bot(
    bot_token: str,
    file_id: str,
    file_type: str
) -> Optional[bytes]:
    """
    Download media file from Telegram using a specific bot's token.
    
    This is used for cross-bot media delivery: when bot A uploaded a file,
    bot B can use this function with bot A's token to download the media bytes.
    
    Args:
        bot_token: The bot token to use for authentication
        file_id: Telegram file_id to download
        file_type: Type of media (photo, video, document, etc.)
        
    Returns:
        Media file bytes if successful, None otherwise
    """
    try:
        # Create a temporary bot instance with the uploader's token
        bot = Bot(token=bot_token)
        
        # Get file info from Telegram
        file = await bot.get_file(file_id)
        
        # Download file bytes using aiohttp
        async with aiohttp.ClientSession() as session:
            async with session.get(file.file_path) as response:
                if response.status == 200:
                    file_bytes = await response.read()
                    logger.info(
                        f"Successfully downloaded media from uploader bot: "
                        f"file_id={file_id[:30]}..., size={len(file_bytes)} bytes, type={file_type}"
                    )
                    return file_bytes
                else:
                    logger.error(
                        f"Failed to download media from Telegram: "
                        f"HTTP {response.status}, file_id={file_id[:30]}..."
                    )
                    return None
                    
    except Exception as e:
        logger.error(
            f"Error downloading media from uploader bot: {e}, "
            f"file_id={file_id[:30]}..., type={file_type}"
        )
        return None


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
    reply_markup=None,
    product_id: Optional[int] = None,
    uploader_bot_id: Optional[str] = None
):
    """
    Send a media message based on file type.
    
    Implements cross-bot media delivery fallback:
    - If sending fails with "Wrong file identifier" error and uploader_bot_id is provided,
      downloads the media from the uploader bot and re-uploads it with the current bot.
    
    Args:
        context: Telegram context
        chat_id: Target chat ID
        file_id: Telegram file_id to send
        file_type: Type of media (photo, video, document, etc.)
        caption: Optional caption text
        reply_markup: Optional inline keyboard
        product_id: Optional product ID for DB updates after fallback
        uploader_bot_id: Optional bot env key that uploaded this media (e.g., 'BOT_TOKEN', 'BOT_TOKEN_1')
    """
    async def _send_media(fid: str) -> None:
        """Internal helper to send media by type."""
        if file_type == "photo":
            await context.bot.send_photo(
                chat_id=chat_id,
                photo=fid,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=None
            )
        elif file_type == "video":
            await context.bot.send_video(
                chat_id=chat_id,
                video=fid,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=None
            )
        elif file_type == "animation":
            await context.bot.send_animation(
                chat_id=chat_id,
                animation=fid,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=None
            )
        elif file_type in ["document", "audio", "voice", "video_note"]:
            await context.bot.send_document(
                chat_id=chat_id,
                document=fid,
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
    
    # Try to send media with original file_id
    try:
        await _send_media(file_id)
        return  # Success - no fallback needed
        
    except BadRequest as e:
        error_msg = str(e)
        
        # Check if this is a cross-bot file_id error that we can recover from
        is_file_id_error = "Wrong file identifier" in error_msg or "file_id" in error_msg.lower()
        
        if is_file_id_error and uploader_bot_id:
            # Attempt cross-bot fallback
            logger.warning(
                f"Cross-bot file_id error detected. Attempting fallback download:\n"
                f"  product_id: {product_id}\n"
                f"  file_id: {file_id[:50]}...\n"
                f"  file_type: {file_type}\n"
                f"  uploader_bot_id: {uploader_bot_id}\n"
                f"  current_bot: {context.bot.username}"
            )
            
            # Resolve uploader_bot_id to bot token
            bot_token = None
            if uploader_bot_id == 'BOT_TOKEN' and Config.BOT_TOKEN:
                bot_token = Config.BOT_TOKEN
            elif uploader_bot_id.startswith('BOT_TOKEN_'):
                # Extract index from 'BOT_TOKEN_N'
                try:
                    # BOT_TOKENS list: [BOT_TOKEN, BOT_TOKEN_1, BOT_TOKEN_2, ...]
                    # BOT_TOKEN is at index 0, BOT_TOKEN_1 at index 1, etc.
                    if uploader_bot_id == 'BOT_TOKEN':
                        bot_token = Config.BOT_TOKENS[0] if Config.BOT_TOKENS else None
                    else:
                        # Parse the number from BOT_TOKEN_N
                        suffix = uploader_bot_id.replace('BOT_TOKEN_', '')
                        token_index = int(suffix)
                        if token_index < len(Config.BOT_TOKENS):
                            bot_token = Config.BOT_TOKENS[token_index]
                except (ValueError, IndexError) as parse_error:
                    logger.error(f"Failed to parse uploader_bot_id '{uploader_bot_id}': {parse_error}")
            
            if not bot_token:
                logger.error(
                    f"Cannot resolve uploader_bot_id '{uploader_bot_id}' to bot token. "
                    f"Available tokens: {len(Config.BOT_TOKENS)}"
                )
                raise  # Re-raise original error - can't recover
            
            # Download media from uploader bot
            media_bytes = await download_media_from_bot(bot_token, file_id, file_type)
            
            if not media_bytes:
                logger.error("Failed to download media from uploader bot - cannot recover")
                raise  # Re-raise original error
            
            # Re-upload media with current bot
            try:
                logger.info(
                    f"Re-uploading media with current bot (@{context.bot.username}): "
                    f"size={len(media_bytes)} bytes, type={file_type}"
                )
                
                # Create InputFile from bytes
                media_file = InputFile(io.BytesIO(media_bytes), filename=f"media.{file_type}")
                
                # Send using current bot (which will get a new file_id)
                await _send_media(media_file)
                
                logger.info(
                    f"Successfully delivered media via cross-bot fallback: "
                    f"product_id={product_id}, uploader={uploader_bot_id}, "
                    f"current_bot=@{context.bot.username}"
                )
                
                # TODO: Optionally update DB with new file_id for this bot
                # This would require capturing the new file_id from the sent message
                # For now, we just log success and continue
                
                return  # Success via fallback
                
            except Exception as reupload_error:
                logger.error(
                    f"Failed to re-upload media after download: {reupload_error}",
                    exc_info=True
                )
                raise  # Re-raise original error - fallback failed
        
        # If not a file_id error or no uploader_bot_id, just re-raise
        raise
        
    except Exception as e:
        # Log all other errors and provide fallback text message
        error_msg = str(e)
        file_id_preview = file_id[:50] if file_id and len(file_id) > 50 else file_id or "None"
        file_id_suffix = "..." if file_id and len(file_id) > 50 else ""
        reply_markup_preview = str(reply_markup)[:100] if reply_markup else "None"
        
        logger.exception(
            f"Error sending media message:\n"
            f"  chat_id: {chat_id}\n"
            f"  file_id: {file_id_preview}{file_id_suffix}\n"
            f"  file_type: {file_type}\n"
            f"  product_id: {product_id}\n"
            f"  uploader_bot_id: {uploader_bot_id}\n"
            f"  reply_markup: {reply_markup_preview}\n"
            f"  error: {error_msg}"
        )
        
        # Provide helpful error messages based on the error type
        fallback_text = caption or 'Media'
        if "Wrong file identifier" in error_msg or "file_id" in error_msg.lower():
            fallback_text += (
                "\n\n⚠️ Media temporarily unavailable (file ID issue).\n"
                "If this persists, please report this product to an admin."
            )
        else:
            fallback_text += f"\n\n⚠️ Error displaying media."
        
        # Fallback to text message with reply_markup still attached
        await context.bot.send_message(
            chat_id=chat_id,
            text=fallback_text,
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
    # Check cache first (with lock for thread safety)
    cache_key = (source_chat_id, source_message_id, file_index)
    async with _file_id_cache_lock:
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
            # Cache the result (with lock for thread safety)
            async with _file_id_cache_lock:
                _file_id_cache[cache_key] = file_id
            logger.debug(f"Cached bot-specific file ID for message {source_message_id}, index {file_index}")
            
            # Delete the forwarded message to avoid cluttering admin chat
            try:
                await context.bot.delete_message(
                    chat_id=target_chat_id,
                    message_id=forwarded.message_id
                )
            except Exception as e:
                logger.warning(f"Could not delete forwarded message: {e}")
            
            return file_id
        else:
            logger.warning(f"Could not extract file ID from forwarded message {source_message_id}")
            
            # Still try to delete the forwarded message
            try:
                await context.bot.delete_message(
                    chat_id=target_chat_id,
                    message_id=forwarded.message_id
                )
            except Exception as e:
                logger.warning(f"Could not delete forwarded message: {e}")
            
            return None
            
    except Forbidden as e:
        # Handle "bot can't initiate conversation with a user" error
        error_msg = str(e)
        if "can't initiate conversation" in error_msg.lower() or "bot was blocked" in error_msg.lower():
            logger.warning(
                f"Unable to forward message to admin {target_chat_id} for bot-specific file ID: {e}\n"
                f"SOLUTION: Admin with ID {target_chat_id} needs to start a conversation with this bot "
                f"by sending /start to it. This is required for secondary bots to cache media file IDs."
            )
        else:
            logger.error(f"Forbidden error getting bot-specific file ID: {e}")
        return None
    except Exception as e:
        logger.error(f"Error getting bot-specific file ID: {e}")
        return None


async def clear_file_id_cache():
    """Clear the bot-specific file ID cache (useful for forcing refresh of old entries)."""
    async with _file_id_cache_lock:
        _file_id_cache.clear()
    logger.info("File ID cache cleared")


def get_file_id_cache_size() -> int:
    """Get the current size of the file ID cache."""
    return len(_file_id_cache)
