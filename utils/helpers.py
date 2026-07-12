"""
Helper utilities for the bot.
"""
import asyncio
import logging
import json
import inspect
from typing import List, Optional, Dict, Tuple
from telegram import Bot, Update, User
from telegram.ext import ContextTypes
from telegram.error import Forbidden, BadRequest, TelegramError

from configs.config import Config
from database import Database
import emojis

logger = logging.getLogger(__name__)

# Module-level database singleton — avoids creating a new connection object per call.
_db = Database()


def resolve_stable_bot_identifier(bot_or_username, fallback: str = "unknown") -> str:
    """Resolve a token-rotation-safe bot identity for persistent media mappings."""
    username = bot_or_username if isinstance(bot_or_username, str) else getattr(bot_or_username, "username", None)
    if username:
        return username.lower()
    return fallback


def resolve_admin_scope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> Dict[str, object]:
    """Return explicit global admin scope metadata for multi-bot admin actions."""
    bot = getattr(context, "bot", None)
    bot_data = getattr(context, "bot_data", {}) or {}
    return {
        "admin_scope": "global",
        "runtime_bot_username": (getattr(bot, "username", None) or "").lower() or None,
        "runtime_bot_slot": bot_data.get("bot_slot"),
        "runtime_bot_identity": bot_data.get("identity_hash"),
    }


def _extract_media_identity(message, file_type: str):
    """Return (file_id, file_unique_id, mime_type, file_size) from a Telegram message."""
    media = None
    if file_type == "photo" and getattr(message, "photo", None):
        media = message.photo[-1]
    elif file_type == "video":
        media = getattr(message, "video", None)
    elif file_type == "document":
        media = getattr(message, "document", None)
    elif file_type == "animation":
        media = getattr(message, "animation", None)
    elif file_type == "video_note":
        media = getattr(message, "video_note", None)
    elif file_type == "audio":
        media = getattr(message, "audio", None)
    elif file_type == "voice":
        media = getattr(message, "voice", None)
    if not media:
        return None, None, None, None
    return (
        getattr(media, "file_id", None),
        getattr(media, "file_unique_id", None),
        getattr(media, "mime_type", None),
        getattr(media, "file_size", None),
    )


async def _call_db_optional(method_name: str, *args, **kwargs):
    method = getattr(_db, method_name, None)
    if method is None or not inspect.iscoroutinefunction(method):
        return None
    return await method(*args, **kwargs)


def is_invalid_telegram_file_id_error(exc: object) -> bool:
    """Return True only for Telegram responses that prove a cached file_id is bad."""
    message = str(exc or "").lower()
    return (
        "file_id is invalid" in message
        or "wrong file identifier" in message
        or "file identifier" in message
        or "wrong file_id" in message
    )


def is_transient_telegram_error(exc: object) -> bool:
    """Return True for network/timeout errors that should not poison media caches."""
    message = str(exc or "").lower()
    class_name = exc.__class__.__name__.lower() if exc is not None else ""
    return (
        isinstance(exc, asyncio.TimeoutError)
        or "timedout" in class_name
        or "networkerror" in class_name
        or "timed out" in message
        or "timeout" in message
        or "network" in message
        or "connection" in message
        or "server disconnected" in message
        or "pool timeout" in message
        or "read timeout" in message
        or "write timeout" in message
        or "connect timeout" in message
    )

# Admin commands fallback text constant (hardcoded for consistency across deploys)
ADMIN_COMMANDS_FALLBACK = (
    "\n\n👨‍💼 Admin Commands:\n"
    "• /start - Welcome message\n"
    "• /menu - View catalog\n"
    "• /language - Change bot language\n"
    "• /adminstatus - View bot and WebApp status\n"
    "• /users - Manage users\n"
    "• /botusers - View users by bot instance\n"
    "• /send - Send message to user\n"
    "• /broadcast - Broadcast to all users\n"
    "• /block - Block a user\n"
    "• /unblock - Unblock a user\n"
    "• /setcontact - Set order contact\n"
    "• /setlink - Manage home contact links\n"
    "• /recategorize - Recategorize products\n"
    "• /clearcache - Clear file ID cache\n"
    "• /prunebots - Prune inactive bot users\n"
    "• /reinitbots - Reinitialise all bot instances\n"
    "• /enable_broadcasts - Enable broadcasts in this group/channel\n"
    "• /disable_broadcasts - Disable broadcasts in this group/channel\n"
    "• /list_broadcasts - List all broadcast destinations\n"
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
    # Handle None or empty string
    if not text:
        return ""
    
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


def get_bot_username(context: ContextTypes.DEFAULT_TYPE) -> Optional[str]:
    """
    Get bot username from context with fallback handling.

    Args:
        context: Telegram context object

    Returns:
        Bot username or None if not available
    """
    return context.bot.username if hasattr(context.bot, 'username') else None


async def get_user_lang_async(user_id: int) -> str:
    """
    Get user's language preference from database.
    Centralizes the common pattern of fetching user language.

    Args:
        user_id: Telegram user ID

    Returns:
        User's preferred language code
    """
    return await _db.get_user_language(user_id)


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


def is_media_message(message) -> bool:
    """
    Check if a message contains any type of media.

    Args:
        message: Telegram Message object

    Returns:
        True if message contains photo, video, document, animation, or audio
    """
    if not message:
        return False
    return bool(
        message.photo or
        message.video or
        message.document or
        message.animation or
        message.audio
    )


async def send_media_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    file_id: str,
    file_type: str,
    caption: str = None,
    reply_markup=None
):
    """
    Send a media message with robust error handling and fallback.
    Uses safe wrappers for consistent error handling and rate limiting.
    """
    from utils.safe_send import (
        safe_send_photo, safe_send_video, safe_send_animation,
        safe_send_document, safe_send_message
    )
    
    try:
        result = None
        if file_type == "photo":
            result = await safe_send_photo(
                context.bot,
                chat_id,
                photo=file_id,
                caption=caption,
                parse_mode=None,
                reply_markup=reply_markup
            )
        elif file_type == "video":
            result = await safe_send_video(
                context.bot,
                chat_id,
                video=file_id,
                caption=caption,
                parse_mode=None,
                reply_markup=reply_markup
            )
        elif file_type == "animation":
            result = await safe_send_animation(
                context.bot,
                chat_id,
                animation=file_id,
                caption=caption,
                parse_mode=None,
                reply_markup=reply_markup
            )
        elif file_type in ["document", "audio", "voice", "video_note"]:
            result = await safe_send_document(
                context.bot,
                chat_id,
                document=file_id,
                caption=caption,
                parse_mode=None,
                reply_markup=reply_markup
            )
        else:
            # Fallback to text message
            result = await safe_send_message(
                context.bot,
                chat_id,
                text=caption or "Media",
                parse_mode=None,
                reply_markup=reply_markup
            )
        
        # If safe wrapper returned None, try text fallback
        if result is None:
            fallback_text = caption or 'Media'
            fallback_text += f"\n\n{emojis.WARNING} Media temporarily unavailable."
            
            await safe_send_message(
                context.bot,
                chat_id,
                text=fallback_text,
                parse_mode=None,
                reply_markup=reply_markup
            )
            
    except Exception as e:
        logger.error(f"Error sending media message: {e}")
        
        # Final fallback to text message
        fallback_text = caption or 'Media'
        fallback_text += f"\n\n{emojis.WARNING} Error displaying media."
        
        try:
            await safe_send_message(
                context.bot,
                chat_id,
                text=fallback_text,
                parse_mode=None,
                reply_markup=reply_markup
            )
        except Exception as fallback_error:
            logger.error(f"Failed to send fallback message: {fallback_error}")


async def get_bot_specific_file_id(
    bot_or_context,
    source_chat_id: int,
    source_message_id: int,
    file_type: str,
    file_index: int = 0
) -> Optional[str]:
    """
    Get bot-specific file ID by forwarding a message from the source chat.
    This is necessary when a secondary bot needs to display media that was
    originally posted to a channel using a different bot.
    
    Uses persistent database cache to survive bot restarts.
    
    Args:
        bot_or_context: Bot instance or ContextTypes.DEFAULT_TYPE
        source_chat_id: ID of the source chat (usually the channel)
        source_message_id: ID of the specific message containing the media
        file_type: Type of file (photo, video, document, etc.)
        file_index: Index of the file (0 for single media, 0..n for media groups)
    
    Returns:
        Bot-specific file ID or None if forwarding fails
    """
    # Extract bot from context or use directly.
    # Check isinstance(Bot) first because Bot/ExtBot also exposes a `.bot` property
    # that returns a User object (from get_me()), which would cause the hasattr
    # branch below to wrongly extract a User instead of the Bot controller.
    if isinstance(bot_or_context, Bot):
        # Already a Bot/ExtBot instance
        bot = bot_or_context
    elif hasattr(bot_or_context, 'bot'):
        # This is a Context object
        bot = bot_or_context.bot
    else:
        # Fallback: use directly
        bot = bot_or_context
    
    # Defensive check: ensure bot has forward_message (i.e., it is a Bot, not a User).
    # In multi-bot setups the resolved object may be a User or thin wrapper; attempt recovery
    # before treating this as an error.
    if not hasattr(bot, "forward_message"):
        logger.warning(
            "[get_bot_specific_file_id] Bot object does not implement forward_message – "
            "attempting recovery. type=%s repr=%r",
            type(bot).__name__,
            repr(bot),
        )
        # Try to recover: check for wrapped client or fall back to bot_or_context itself
        candidate = getattr(bot, "client", None) or getattr(bot, "bot", None)
        if candidate and hasattr(candidate, "forward_message"):
            bot = candidate
            logger.debug("[get_bot_specific_file_id] Resolved bot client via wrapper attribute")
        elif hasattr(bot_or_context, "forward_message"):
            bot = bot_or_context
            logger.debug("[get_bot_specific_file_id] Resolved bot client from bot_or_context directly")
        else:
            logger.error(
                "[get_bot_specific_file_id] Unable to resolve Bot client for forwarding. "
                "source_chat=%s source_msg=%s type=%s repr=%r",
                source_chat_id, source_message_id,
                type(bot).__name__, repr(bot),
            )
            return None
    
    # Get current bot username
    bot_username = bot.username
    if not bot_username:
        logger.error("Bot username not available - cannot cache file ID")
        return None
    
    # Check persistent database cache first
    bot_identifier = resolve_stable_bot_identifier(bot, bot_username.lower())
    canonical = None
    get_canonical = getattr(_db, "get_canonical_media_by_origin", None)
    if get_canonical and inspect.iscoroutinefunction(get_canonical):
        canonical = await get_canonical(source_chat_id, source_message_id, file_type)
    if canonical:
        mapped_file_id = await _db.get_bot_media_file_id(bot_identifier, canonical["file_unique_id"])
        if mapped_file_id:
            try:
                await bot.get_file(mapped_file_id)
                logger.debug("[media_registry] using_bot_media_mapping bot=%s msg=%s", bot_identifier, source_message_id)
                return mapped_file_id
            except (TelegramError, asyncio.TimeoutError) as e:
                if is_transient_telegram_error(e):
                    logger.warning(
                        "[media_registry] bot_media_mapping_validation_deferred_refreshing bot=%s msg=%s error=%s",
                        bot_identifier,
                        source_message_id,
                        e,
                    )
                else:
                    await _db.invalidate_bot_media_file_id(bot_identifier, canonical["file_unique_id"])
                    logger.warning("[media_registry] stale_mapping_invalidated bot=%s msg=%s error=%s", bot_identifier, source_message_id, e)

    cached_file_id = await _db.get_bot_file_id(
        source_chat_id,
        source_message_id,
        file_index,
        bot_username,
        file_type=file_type,
    )
    
    if cached_file_id:
        try:
            tg_file = await bot.get_file(cached_file_id)
            file_unique_id = getattr(tg_file, "file_unique_id", None)
            if file_unique_id:
                await _call_db_optional(
                    "upsert_canonical_media",
                    product_id=str(source_message_id),
                    media_type=file_type,
                    file_unique_id=file_unique_id,
                    origin_chat_id=source_chat_id,
                    origin_message_id=source_message_id,
                    original_file_id=cached_file_id,
                    file_size=getattr(tg_file, "file_size", None),
                )
                await _call_db_optional("upsert_bot_media_file_id", bot_identifier, file_unique_id, cached_file_id)
            logger.debug(f"Using cached file ID for bot {bot_username}, message {source_message_id}, index {file_index}")
            return cached_file_id
        except (TelegramError, asyncio.TimeoutError) as e:
            if is_transient_telegram_error(e):
                logger.warning(
                    "[media_registry] cached_file_id_validation_deferred_refreshing bot=%s msg=%s idx=%s error=%s",
                    bot_username,
                    source_message_id,
                    file_index,
                    e,
                )
            else:
                logger.warning(
                    f"Cached file ID invalid for bot {bot_username}, msg {source_message_id}: {e}. "
                    f"Clearing cache and refetching..."
                )
                if canonical:
                    await _db.invalidate_bot_media_file_id(bot_identifier, canonical["file_unique_id"])
                await _db.delete_bot_file_id_cache_entry(
                    source_chat_id,
                    source_message_id,
                    file_index,
                    bot_username
                )
            # Fall through to re-fetch the file ID
    
    # Cache miss - need to get bot-specific file ID by forwarding
    admin_ids = get_admin_ids()
    if not admin_ids:
        logger.error(
            f"[get_bot_specific_file_id] No admin IDs configured - cannot get bot-specific file ID. "
            f"Bot: {bot_username}, Source: chat={source_chat_id}, msg={source_message_id}, type={file_type}"
        )
        return None
    
    target_chat_id = admin_ids[0]

    async def _delete_forwarded_message(forwarded_message) -> None:
        message_id = getattr(forwarded_message, "message_id", None)
        if not message_id:
            logger.warning("[get_bot_specific_file_id] Forwarded message had no message_id; cannot delete admin-chat copy")
            return
        try:
            await bot.delete_message(chat_id=target_chat_id, message_id=message_id)
            logger.debug("[get_bot_specific_file_id] Deleted forwarded message from admin chat")
        except Exception as e:
            logger.warning("[get_bot_specific_file_id] Could not delete forwarded message: %s", e)
    
    # Log context for debugging
    logger.debug(
        f"[get_bot_specific_file_id] Attempting to get bot-specific file ID: "
        f"bot={bot_username}, source_chat={source_chat_id}, source_msg={source_message_id}, "
        f"file_type={file_type}, file_index={file_index}, target_admin={target_chat_id}"
    )
    
    forwarded = None
    try:
        # Forward the message to get the bot-specific file ID
        forwarded = await bot.forward_message(
            chat_id=target_chat_id,
            from_chat_id=source_chat_id,
            message_id=source_message_id
        )
        
        logger.debug(
            f"[get_bot_specific_file_id] Successfully forwarded message {source_message_id} "
            f"from chat {source_chat_id} to admin {target_chat_id}"
        )
        
        # Extract stable and bot-specific media IDs from the forwarded message.
        file_id, file_unique_id, mime_type, file_size = _extract_media_identity(forwarded, file_type)
        
        if file_id:
            if file_unique_id:
                await _call_db_optional(
                    "upsert_canonical_media",
                    product_id=str(source_message_id),
                    media_type=file_type,
                    file_unique_id=file_unique_id,
                    origin_chat_id=source_chat_id,
                    origin_message_id=source_message_id,
                    original_file_id=file_id,
                    mime_type=mime_type,
                    file_size=file_size,
                )
                await _call_db_optional("upsert_bot_media_file_id", bot_identifier, file_unique_id, file_id)
            # Cache the result in database for persistence
            await _db.cache_bot_file_id(
                source_chat_id,
                source_message_id,
                file_index,
                file_type,
                bot_username,
                file_id
            )
            logger.info(
                f"[get_bot_specific_file_id] Successfully cached bot-specific file ID: "
                f"bot={bot_username}, msg={source_message_id}, idx={file_index}, type={file_type}"
            )
            return file_id
        else:
            logger.warning(
                f"[get_bot_specific_file_id] Could not extract {file_type} file ID from forwarded message. "
                f"Source: chat={source_chat_id}, msg={source_message_id}. "
                f"The message may not contain media of the expected type."
            )
            return None
    
    except BadRequest as e:
        error_msg = str(e)
        if "message to forward not found" in error_msg.lower():
            logger.error(
                f"[get_bot_specific_file_id] Source message not found: chat={source_chat_id}, "
                f"msg={source_message_id}. The message may have been deleted from the channel, "
                f"or the bot doesn't have access to it. Error: {e}"
            )
        elif "message can't be forwarded" in error_msg.lower():
            logger.error(
                f"[get_bot_specific_file_id] Message cannot be forwarded: chat={source_chat_id}, "
                f"msg={source_message_id}. The message may have forwarding restrictions. Error: {e}"
            )
        elif "chat not found" in error_msg.lower():
            logger.error(
                f"[get_bot_specific_file_id] Source chat not found: chat={source_chat_id}. "
                f"The bot may have lost access to the channel. Error: {e}"
            )
        else:
            logger.error(
                f"[get_bot_specific_file_id] BadRequest error forwarding message: "
                f"source_chat={source_chat_id}, source_msg={source_message_id}, "
                f"target_admin={target_chat_id}, bot={bot_username}. Error: {e}"
            )
        return None
            
    except Forbidden as e:
        error_msg = str(e)
        if "can't initiate conversation" in error_msg.lower() or "bot was blocked" in error_msg.lower():
            logger.warning(
                f"[get_bot_specific_file_id] Cannot initiate conversation with admin {target_chat_id}. "
                f"SOLUTION: Admin needs to start a conversation with bot {bot_username} by sending /start. "
                f"This is required for secondary bots to cache media file IDs. Error: {e}"
            )
        else:
            logger.error(
                f"[get_bot_specific_file_id] Forbidden error: source_chat={source_chat_id}, "
                f"source_msg={source_message_id}, target_admin={target_chat_id}, bot={bot_username}. Error: {e}"
            )
        return None
    
    except TelegramError as e:
        logger.error(
            f"[get_bot_specific_file_id] Telegram API error: source_chat={source_chat_id}, "
            f"source_msg={source_message_id}, target_admin={target_chat_id}, "
            f"bot={bot_username}, file_type={file_type}. Error: {e}"
        )
        return None
    
    except Exception as e:
        logger.error(
            f"[get_bot_specific_file_id] Unexpected error: source_chat={source_chat_id}, "
            f"source_msg={source_message_id}, target_admin={target_chat_id}, "
            f"bot={bot_username}, file_type={file_type}. Error: {e}",
            exc_info=True
        )
        return None
    finally:
        if forwarded is not None:
            await _delete_forwarded_message(forwarded)


def register_bot_handlers(application: "Application", bot_index: int = 0):
    """Register all bot command and message handlers on the given Application instance.

    This shared function eliminates duplicate handler registration between
    main.py (polling mode) and webhook_server.py (webhook mode).

    Args:
        application: The telegram.ext.Application instance to register handlers on.
        bot_index: Index of the bot (0 for primary, 1+ for additional bots).
                   Only the primary bot (index 0) monitors the channel.
    """
    from telegram.ext import (
        CommandHandler, MessageHandler, CallbackQueryHandler,
        InlineQueryHandler, ChosenInlineResultHandler, ChatMemberHandler, filters
    )
    # Import command handlers directly from their defining modules to avoid the
    # circular dependency: utils/helpers.py → main.py → utils/helpers.py.
    # Only the four core update-routing handlers (channel_post_handler,
    # callback_query_handler, message_handler, error_handler) still live in main.py
    # because they orchestrate cross-cutting concerns that belong there.
    from handlers.start import start_command, subscribe_command, unsubscribe_command
    from handlers.menu import menu_command
    from handlers.language import language_command
    from handlers.admin import (
        nuke_command, recategorize_command, users_command,
        block_command, unblock_command, send_command, broadcast_command,
        setcontact_command, setlink_command, clearcache_command,
        botusers_command, prunebots_command, reinitbots_command,
        adminstatus_command,
        seostatus_command,
        enable_broadcasts_command, disable_broadcasts_command, list_broadcasts_command,
    )
    from handlers.broadcast import my_chat_member_handler
    from handlers.inline import handle_inline_query, handle_chosen_inline_result
    from main import channel_post_handler, callback_query_handler, message_handler, error_handler

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("language", language_command))
    application.add_handler(CommandHandler("adminstatus", adminstatus_command))
    application.add_handler(CommandHandler("seostatus", seostatus_command))
    application.add_handler(CommandHandler("nuke", nuke_command))
    application.add_handler(CommandHandler("recategorize", recategorize_command))
    application.add_handler(CommandHandler("users", users_command))
    application.add_handler(CommandHandler("botusers", botusers_command))
    application.add_handler(CommandHandler("prunebots", prunebots_command))
    application.add_handler(CommandHandler("reinitbots", reinitbots_command))
    application.add_handler(CommandHandler("subscribe", subscribe_command))
    application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    application.add_handler(CommandHandler("block", block_command))
    application.add_handler(CommandHandler("unblock", unblock_command))
    application.add_handler(CommandHandler("send", send_command))
    application.add_handler(CommandHandler("broadcast", broadcast_command))
    application.add_handler(CommandHandler("setcontact", setcontact_command))
    application.add_handler(CommandHandler("setlink", setlink_command))
    application.add_handler(CommandHandler("clearcache", clearcache_command))
    application.add_handler(CommandHandler("enable_broadcasts", enable_broadcasts_command))
    application.add_handler(CommandHandler("disable_broadcasts", disable_broadcasts_command))
    application.add_handler(CommandHandler("list_broadcasts", list_broadcasts_command))

    # my_chat_member: fires when the bot's own membership in any chat changes
    application.add_handler(ChatMemberHandler(my_chat_member_handler, ChatMemberHandler.MY_CHAT_MEMBER))

    # Channel post handler (primary bot only)
    if bot_index == 0:
        channel_id = get_channel_id()
        channel_username = get_channel_username()

        if channel_id:
            channel_filter = filters.Chat(chat_id=channel_id)
        elif channel_username:
            channel_filter = filters.Chat(username=channel_username.lstrip("@"))
        else:
            logger.warning("No CHANNEL_ID or CHANNEL_USERNAME set. Channel monitoring disabled.")
            channel_filter = None

        if channel_filter:
            application.add_handler(
                MessageHandler(
                    channel_filter & filters.ChatType.CHANNEL,
                    channel_post_handler
                )
            )
            logger.info(f"Channel monitoring enabled for: {channel_id or channel_username}")
    else:
        logger.info(
            f"Channel monitoring DISABLED for secondary bot {bot_index} "
            "(only primary bot monitors channel)"
        )

    application.add_handler(CallbackQueryHandler(callback_query_handler))
    application.add_handler(InlineQueryHandler(handle_inline_query))
    application.add_handler(ChosenInlineResultHandler(handle_chosen_inline_result))
    application.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            message_handler
        )
    )
    application.add_error_handler(error_handler)


async def clear_file_id_cache():
    """Clear the bot-specific file ID cache (useful for forcing refresh of old entries)."""
    deleted = await _db.clear_bot_file_id_cache()
    logger.info(f"File ID cache cleared: {deleted} entries removed from database")
    return deleted


async def get_file_id_cache_size() -> int:
    """Get the current size of the file ID cache."""
    async with _db.get_connection() as conn:
        async with conn.execute("SELECT COUNT(*) FROM bot_file_id_cache") as cursor:
            row = await cursor.fetchone()
            return row[0] if row else 0
