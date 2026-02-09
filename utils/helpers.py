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
    
    Implements fallback strategy:
    1. Try Bot.get_file().download_as_bytearray() (preferred, simpler)
    2. Fallback to constructing file API URL and downloading via aiohttp
    3. Retry once on failure
    
    Args:
        bot_token: The bot token to use for authentication
        file_id: Telegram file_id to download
        file_type: Type of media (photo, video, document, etc.)
        
    Returns:
        Media file bytes if successful, None otherwise
    """
    max_retries = 1
    timeout_seconds = 30
    
    for attempt in range(max_retries + 1):
        try:
            # Create a temporary bot instance with the uploader's token
            bot = Bot(token=bot_token)
            
            # Get file info from Telegram
            logger.debug(
                f"Attempt {attempt + 1}/{max_retries + 1}: Getting file info for file_id={file_id[:30]}..."
            )
            tg_file = await bot.get_file(file_id)
            
            # Try preferred method: download_as_bytearray()
            try:
                logger.debug(f"Trying download_as_bytearray() for file_id={file_id[:30]}...")
                file_bytes = await asyncio.wait_for(
                    tg_file.download_as_bytearray(),
                    timeout=timeout_seconds
                )
                logger.info(
                    f"Successfully downloaded media via download_as_bytearray(): "
                    f"file_id={file_id[:30]}..., size={len(file_bytes)} bytes, type={file_type}"
                )
                return bytes(file_bytes)
            except Exception as download_error:
                logger.debug(
                    f"download_as_bytearray() failed: {download_error}. "
                    f"Trying fallback with aiohttp..."
                )
                
                # Fallback: construct file API URL and download via aiohttp
                # The file_path returned by get_file() is already a full URL
                file_url = tg_file.file_path
                
                logger.debug(f"Downloading from URL: {file_url}")
                async with aiohttp.ClientSession() as session:
                    async with asyncio.wait_for(
                        session.get(file_url),
                        timeout=timeout_seconds
                    ) as response:
                        if response.status == 200:
                            file_bytes = await response.read()
                            logger.info(
                                f"Successfully downloaded media via aiohttp fallback: "
                                f"file_id={file_id[:30]}..., size={len(file_bytes)} bytes, type={file_type}"
                            )
                            return file_bytes
                        else:
                            logger.error(
                                f"Failed to download media from Telegram: "
                                f"HTTP {response.status}, file_id={file_id[:30]}..., "
                                f"attempt {attempt + 1}/{max_retries + 1}"
                            )
                            if attempt < max_retries:
                                logger.info(f"Retrying download in 1 second...")
                                await asyncio.sleep(1)
                                continue
                            return None
                        
        except asyncio.TimeoutError:
            logger.error(
                f"Timeout downloading media (>{timeout_seconds}s): "
                f"file_id={file_id[:30]}..., type={file_type}, "
                f"attempt {attempt + 1}/{max_retries + 1}"
            )
            if attempt < max_retries:
                logger.info(f"Retrying download in 1 second...")
                await asyncio.sleep(1)
                continue
            return None
        except Exception as e:
            logger.error(
                f"Error downloading media from uploader bot: {e}, "
                f"file_id={file_id[:30]}..., type={file_type}, "
                f"attempt {attempt + 1}/{max_retries + 1}",
                exc_info=True
            )
            if attempt < max_retries:
                logger.info(f"Retrying download in 1 second...")
                await asyncio.sleep(1)
                continue
            return None
    
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
    uploader_bot_id: Optional[str] = None,
    db = None
):
    """
    Send a media message based on file type.
    
    Implements cross-bot media delivery fallback with per-bot file_id caching:
    - Looks up bot-specific cached file_id before sending
    - Attempts to send using bot-specific file_id (if available) or canonical file_id
    - If sending fails with "Wrong file identifier" error:
      - Invalidates the bot-specific cache entry
      - Downloads media from uploader bot (if uploader_bot_id provided)
      - Re-uploads with current bot
      - Caches the new bot-specific file_id
    - If reupload succeeds and product_id is provided, updates the DB with the new file_id
    
    Args:
        context: Telegram context
        chat_id: Target chat ID
        file_id: Telegram file_id to send (canonical/original)
        file_type: Type of media (photo, video, document, etc.)
        caption: Optional caption text
        reply_markup: Optional inline keyboard
        product_id: Optional product ID for DB updates and caching
        uploader_bot_id: Optional bot env key that uploaded this media (e.g., 'BOT_TOKEN', 'BOT_TOKEN_1')
        db: Optional Database instance for file_id caching and product updates
    """
    from utils.bot_key import get_bot_env_key
    
    async def _send_media(file_id_or_input):
        """
        Internal helper to send media by type. Returns sent message.
        
        Args:
            file_id_or_input: Either a file_id string or an InputFile object
        """
        if file_type == "photo":
            return await context.bot.send_photo(
                chat_id=chat_id,
                photo=file_id_or_input,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=None
            )
        elif file_type == "video":
            return await context.bot.send_video(
                chat_id=chat_id,
                video=file_id_or_input,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=None
            )
        elif file_type == "animation":
            return await context.bot.send_animation(
                chat_id=chat_id,
                animation=file_id_or_input,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=None
            )
        elif file_type in ["document", "audio", "voice", "video_note"]:
            return await context.bot.send_document(
                chat_id=chat_id,
                document=file_id_or_input,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode=None
            )
        else:
            # Fallback to text message
            return await context.bot.send_message(
                chat_id=chat_id,
                text=caption or "Media",
                reply_markup=reply_markup
            )
    
    # Get current bot key for file_id caching
    current_bot_key = get_bot_env_key()
    
    # Try to get bot-specific cached file_id if product_id and db are available
    file_id_to_send = file_id  # Start with canonical file_id
    used_cached_file_id = False
    
    if product_id and db:
        try:
            cached_file_id = await db.get_bot_file_id(current_bot_key, product_id)
            if cached_file_id:
                logger.debug(
                    f"Using cached bot-specific file_id for bot={current_bot_key}, "
                    f"product_id={product_id}, file_id={cached_file_id[:30]}..."
                )
                file_id_to_send = cached_file_id
                used_cached_file_id = True
            else:
                logger.debug(
                    f"No cached file_id for bot={current_bot_key}, product_id={product_id}, "
                    f"will try canonical file_id"
                )
        except Exception as cache_error:
            logger.warning(f"Error checking bot file_id cache: {cache_error}")
            # Continue with canonical file_id
    
    # Try to send media with selected file_id (cached or canonical)
    last_error = None
    try:
        sent_message = await _send_media(file_id_to_send)
        
        # On successful send, cache the bot-specific file_id if not already cached
        if product_id and db and not used_cached_file_id:
            try:
                # Extract the bot-specific file_id from the sent message
                bot_file_id = None
                if file_type == "photo" and sent_message.photo:
                    bot_file_id = sent_message.photo[-1].file_id
                elif file_type == "video" and sent_message.video:
                    bot_file_id = sent_message.video.file_id
                elif file_type == "animation" and sent_message.animation:
                    bot_file_id = sent_message.animation.file_id
                elif file_type == "document" and sent_message.document:
                    bot_file_id = sent_message.document.file_id
                elif file_type == "audio" and sent_message.audio:
                    bot_file_id = sent_message.audio.file_id
                elif file_type == "voice" and sent_message.voice:
                    bot_file_id = sent_message.voice.file_id
                elif file_type == "video_note" and sent_message.video_note:
                    bot_file_id = sent_message.video_note.file_id
                
                if bot_file_id:
                    success = await db.set_bot_file_id(current_bot_key, product_id, bot_file_id)
                    if success:
                        logger.info(
                            f"Cached bot-specific file_id: bot={current_bot_key}, "
                            f"product_id={product_id}, file_id={bot_file_id[:30]}..."
                        )
            except Exception as cache_error:
                logger.warning(f"Error caching bot file_id after successful send: {cache_error}")
        
        return  # Success - no fallback needed
        
    except BadRequest as e:
        last_error = e
        error_msg = str(e)
        
        # Check if this is a cross-bot file_id error that we can recover from
        is_file_id_error = "Wrong file identifier" in error_msg or "http url specified" in error_msg.lower()
        
        if is_file_id_error:
            logger.warning(
                f"File_id error detected for bot={current_bot_key}, product_id={product_id}: {error_msg}"
            )
            
            # Invalidate cached file_id if we used one
            if used_cached_file_id and product_id and db:
                try:
                    invalidated = await db.invalidate_bot_file_id(current_bot_key, product_id)
                    if invalidated:
                        logger.info(
                            f"Invalidated cached file_id for bot={current_bot_key}, "
                            f"product_id={product_id}"
                        )
                except Exception as inv_error:
                    logger.warning(f"Error invalidating cached file_id: {inv_error}")
            
            # Attempt fallback if uploader_bot_id is provided
            if uploader_bot_id:
                logger.warning(
                    f"Attempting cross-bot fallback download:\n"
                    f"  product_id: {product_id}\n"
                    f"  file_id: {file_id[:50]}...\n"
                    f"  file_type: {file_type}\n"
                    f"  uploader_bot_id: {uploader_bot_id}\n"
                    f"  current_bot: {context.bot.username}"
                )
                
                # Resolve uploader_bot_id to bot token
                bot_token = None
                if uploader_bot_id == 'BOT_TOKEN':
                    # Primary bot - use first token in list for consistency
                    bot_token = Config.BOT_TOKENS[0] if Config.BOT_TOKENS else Config.BOT_TOKEN
                elif uploader_bot_id.startswith('BOT_TOKEN_'):
                    # Secondary bot - parse index from 'BOT_TOKEN_N'
                    try:
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
                    # Fall through to text fallback at the end
                else:
                    # Download media from uploader bot
                    media_bytes = await download_media_from_bot(bot_token, file_id, file_type)
                    
                    if media_bytes:
                    if media_bytes:
                        # Re-upload media with current bot
                        try:
                            logger.info(
                                f"Re-uploading media with current bot (@{context.bot.username}): "
                                f"size={len(media_bytes)} bytes, type={file_type}"
                            )
                            
                            # Map file_type to proper extension
                            extension_map = {
                                "photo": "jpg",
                                "video": "mp4",
                                "animation": "gif",
                                "document": "pdf",
                                "audio": "mp3",
                                "voice": "ogg",
                                "video_note": "mp4"
                            }
                            extension = extension_map.get(file_type, "bin")
                            
                            # Create InputFile from bytes
                            media_file = InputFile(io.BytesIO(media_bytes), filename=f"media.{extension}")
                            
                            # Send using current bot (which will get a new file_id)
                            sent_message = await _send_media(media_file)
                            
                            logger.info(
                                f"Successfully delivered media via cross-bot fallback: "
                                f"product_id={product_id}, uploader={uploader_bot_id}, "
                                f"current_bot=@{context.bot.username}"
                            )
                            
                            # Extract the new bot-specific file_id from the sent message
                            new_file_id = None
                            if file_type == "photo" and sent_message.photo:
                                new_file_id = sent_message.photo[-1].file_id
                            elif file_type == "video" and sent_message.video:
                                new_file_id = sent_message.video.file_id
                            elif file_type == "animation" and sent_message.animation:
                                new_file_id = sent_message.animation.file_id
                            elif file_type == "document" and sent_message.document:
                                new_file_id = sent_message.document.file_id
                            elif file_type == "audio" and sent_message.audio:
                                new_file_id = sent_message.audio.file_id
                            elif file_type == "voice" and sent_message.voice:
                                new_file_id = sent_message.voice.file_id
                            elif file_type == "video_note" and sent_message.video_note:
                                new_file_id = sent_message.video_note.file_id
                            
                            # Cache the new bot-specific file_id
                            if new_file_id and product_id and db:
                                try:
                                    success = await db.set_bot_file_id(current_bot_key, product_id, new_file_id)
                                    if success:
                                        logger.info(
                                            f"Cached new bot-specific file_id after fallback: "
                                            f"bot={current_bot_key}, product_id={product_id}, "
                                            f"file_id={new_file_id[:30]}..."
                                        )
                                except Exception as cache_error:
                                    logger.warning(f"Error caching new bot file_id: {cache_error}")
                            
                            # Update DB with new file_id if product_id is provided and we have a DB connection
                            if product_id and db and new_file_id:
                                try:
                                    # Update product with new file_id and current bot as uploader
                                    success = await db.update_product_file_id(
                                        product_id=product_id,
                                        file_id=new_file_id,
                                        uploader_bot_id=current_bot_key
                                    )
                                    
                                    if success:
                                        logger.info(
                                            f"Updated product file_id in DB: product_id={product_id}, "
                                            f"new_file_id={new_file_id[:30]}..., "
                                            f"new_uploader={current_bot_key}"
                                        )
                                    else:
                                        logger.warning(
                                            f"Failed to update product file_id in DB for product_id={product_id}"
                                        )
                                except Exception as db_error:
                                    logger.error(
                                        f"Error updating DB with new file_id: {db_error}",
                                        exc_info=True
                                    )
                                    # Continue anyway - the message was sent successfully
                            elif not new_file_id:
                                logger.warning(
                                    f"Could not extract new file_id from sent message for "
                                    f"product_id={product_id}, file_type={file_type}"
                                )
                            
                            return  # Success via fallback
                        
                    except Exception as reupload_error:
                        logger.error(
                            f"Failed to re-upload media after download: {reupload_error}",
                            exc_info=True
                        )
                        last_error = reupload_error
                        # Fall through to text fallback
                else:
                    logger.error("Failed to download media from uploader bot - cannot recover")
                    # Fall through to text fallback
        
        # Fall through to text fallback for all error cases
        
    except Exception as e:
        last_error = e
        # Log all other errors
        error_msg = str(e)
        file_id_preview = file_id[:50] if file_id and len(file_id) > 50 else file_id or "None"
        file_id_suffix = "..." if file_id and len(file_id) > 50 else ""
        
        logger.exception(
            f"Error sending media message:\n"
            f"  chat_id: {chat_id}\n"
            f"  file_id: {file_id_preview}{file_id_suffix}\n"
            f"  file_type: {file_type}\n"
            f"  product_id: {product_id}\n"
            f"  uploader_bot_id: {uploader_bot_id}\n"
            f"  error: {error_msg}"
        )
        # Fall through to text fallback
    
    # Text fallback (reached if any error occurred and wasn't recovered)
    error_msg = str(last_error) if last_error else "Unknown error"
    reply_markup_preview = str(reply_markup)[:100] if reply_markup else "None"
    
    logger.warning(
        f"Sending text fallback message for media failure:\n"
        f"  chat_id: {chat_id}\n"
        f"  product_id: {product_id}\n"
        f"  error: {error_msg}\n"
        f"  reply_markup: {reply_markup_preview}"
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
