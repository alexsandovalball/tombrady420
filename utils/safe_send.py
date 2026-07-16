"""
Safe wrappers for Telegram send/edit operations with entity parsing fallback.

This module provides robust wrappers that:
1. Handle entity parsing errors by falling back to plain text
2. Detect deleted/deactivated user accounts and trigger DB cleanup
3. Provide consistent error handling across all message operations
4. Handle Telegram rate limiting (429/RetryAfter) with bounded retries
"""
import logging
import asyncio
import random
from typing import Optional, Any
from html import escape as escape_html
from telegram import Bot, Message, Update
from telegram.error import TelegramError, BadRequest, Forbidden, RetryAfter
from telegram.helpers import escape_markdown as escape_markdown_v2
from database import Database

logger = logging.getLogger(__name__)

# Module-level database singleton — avoids creating a new connection object per user-cleanup call.
_db = Database()

# Constants for rate limiting and user cleanup
MAX_RETRIES = 3
MIN_RETRY_DELAY = 1.0  # seconds
MAX_RETRY_DELAY = 60.0  # seconds
JITTER_RANGE = 0.5  # +/- 50% jitter

# Centralized patterns for unreachable users
DELETED_USER_PATTERNS = [
    "user is deactivated",
    "user not found",
    "chat not found",
    "user has been deleted",
    "bot was blocked by the user",
    "bot can't initiate conversation",
    "user_deleted",
    "user_deactivated"
]


def escape_text_for_parse_mode(text: str, parse_mode: Optional[str]) -> str:
    """
    Escape text based on the parse mode being used.
    
    Args:
        text: The text to escape
        parse_mode: The parse mode ("HTML", "Markdown", "MarkdownV2", or None)
        
    Returns:
        Escaped text appropriate for the parse mode
    """
    if not parse_mode:
        return text
    
    parse_mode_upper = parse_mode.upper()
    
    if parse_mode_upper == "HTML":
        return escape_html(text)
    elif parse_mode_upper == "MARKDOWNV2":
        return escape_markdown_v2(text)
    elif parse_mode_upper == "MARKDOWN":
        # Escape common Markdown special characters
        text = text.replace('_', r'\_')
        text = text.replace('*', r'\*')
        text = text.replace('`', r'\`')
        text = text.replace('[', r'\[')
        return text
    
    return text


async def handle_send_exception(exception: Exception, user_id: int, operation: str = "send") -> bool:
    """
    Handle exceptions from Telegram send/edit operations.
    Detects deleted/deactivated users and triggers DB cleanup.
    
    Args:
        exception: The exception that occurred
        user_id: The user ID associated with the operation
        operation: Description of the operation (for logging)
        
    Returns:
        True if user should be removed from DB, False otherwise
    """
    error_msg = str(exception).lower()
    
    # Check if this is a deleted/blocked user error
    should_remove = False
    
    if isinstance(exception, Forbidden):
        # 403 Forbidden typically means bot was blocked or user is unreachable
        should_remove = True
        logger.info(f"[Safe Send] User {user_id} blocked bot or is unreachable (Forbidden) - operation: {operation}")
    elif isinstance(exception, BadRequest):
        # Check if BadRequest message indicates deleted user
        if any(pattern in error_msg for pattern in DELETED_USER_PATTERNS):
            should_remove = True
            logger.info(f"[Safe Send] User {user_id} account deleted/deactivated (BadRequest) - operation: {operation}")
    
    # If user should be removed, trigger DB cleanup
    if should_remove:
        try:
            await _db.delete_user(user_id)
            logger.info(f"[User Cleanup] Removed user {user_id} from database (reason: {operation} failed)")
            return True
        except Exception as db_error:
            logger.error(f"[User Cleanup] Failed to delete user {user_id} from database: {db_error}", exc_info=True)
            return False
    
    return False


def _normalize_parse_mode(parse_mode: Optional[str]) -> Optional[str]:
    """Return a supported parse mode or a conservative fallback."""
    if parse_mode and parse_mode.upper() not in {"HTML", "MARKDOWN", "MARKDOWNV2"}:
        logger.warning(f"[Safe Send] Unknown parse_mode '{parse_mode}', falling back to Markdown")
        return "Markdown"
    return parse_mode


def _retry_delay_seconds(retry_after: float) -> float:
    """Apply bounded jitter to Telegram RetryAfter values."""
    delay = max(MIN_RETRY_DELAY, retry_after)
    jitter = delay * JITTER_RANGE * (2 * random.random() - 1)
    return max(MIN_RETRY_DELAY, delay + jitter)


def _is_entity_parsing_error(error_msg: str) -> bool:
    """Return True when Telegram rejected malformed formatting entities."""
    lowered = str(error_msg).lower()
    return "can't find end of the entity" in lowered or "can't parse entities" in lowered


async def _safe_send_media_with_caption(
    bot: Bot,
    *,
    operation: str,
    method_name: str,
    media_arg_name: str,
    media_value: Any,
    chat_id: int,
    caption: Optional[str],
    parse_mode: Optional[str],
    **kwargs,
) -> Optional[Message]:
    """Shared implementation for photo/video/animation/document sends."""
    parse_mode = _normalize_parse_mode(parse_mode)
    method = getattr(bot, method_name)
    media_kwargs = {media_arg_name: media_value, **kwargs}

    async def attempt_send(
        current_caption: Optional[str],
        current_parse_mode: Optional[str],
        retry_count: int = 0,
    ) -> Optional[Message]:
        try:
            return await method(
                chat_id=chat_id,
                caption=current_caption,
                parse_mode=current_parse_mode,
                **media_kwargs,
            )
        except RetryAfter as e:
            if retry_count >= MAX_RETRIES:
                logger.warning(f"[Safe Send] Rate limit max retries ({MAX_RETRIES}) exceeded for {operation} to chat {chat_id}")
                return None
            if e.retry_after > MAX_RETRY_DELAY:
                logger.warning(f"[Safe Send] Rate limit delay too long ({e.retry_after}s) for {operation} to chat {chat_id}")
                return None

            total_delay = _retry_delay_seconds(e.retry_after)
            logger.info(f"[Safe Send] Rate limited, retrying {operation} to chat {chat_id} in {total_delay:.2f}s")
            await asyncio.sleep(total_delay)
            return await attempt_send(current_caption, current_parse_mode, retry_count + 1)

    try:
        return await attempt_send(caption, parse_mode)
    except BadRequest as e:
        if caption and _is_entity_parsing_error(str(e)):
            logger.warning(f"[Safe Send] Caption parsing error for {operation} to chat {chat_id}, attempting fallback")

            if parse_mode:
                try:
                    escaped_caption = escape_text_for_parse_mode(caption, parse_mode)
                    logger.debug(f"[Safe Send] Fallback stage 1: escaped caption with {parse_mode}")
                    return await attempt_send(escaped_caption, parse_mode)
                except (BadRequest, TelegramError):
                    logger.warning(f"[Safe Send] Escaped caption failed for {operation} to chat {chat_id}, trying plain caption")

            try:
                logger.debug("[Safe Send] Fallback stage 2: plain caption (no parse_mode)")
                return await attempt_send(caption, None)
            except Exception as final_error:
                logger.error(f"[Safe Send] All {operation} attempts failed for chat {chat_id}: {final_error}", exc_info=True)
                await handle_send_exception(final_error, chat_id, operation)
                return None

        await handle_send_exception(e, chat_id, operation)
        return None
    except (Forbidden, TelegramError) as e:
        await handle_send_exception(e, chat_id, operation)
        return None
    except Exception as e:
        logger.error(f"[Safe Send] Unexpected error in {operation} to chat {chat_id}: {e}", exc_info=True)
        return None


async def safe_send_message(
    bot: Bot,
    chat_id: int,
    text: str,
    parse_mode: Optional[str] = "Markdown",
    **kwargs
) -> Optional[Message]:
    """
    Safely send a message with entity parsing fallback and rate limiting.
    
    Falls back through: original -> escaped -> plain text if parsing fails.
    Handles rate limiting (429) with automatic retries.
    Removes deleted/blocked users from DB.
    
    Args:
        bot: Telegram bot instance
        chat_id: Target chat ID
        text: Message text
        parse_mode: Parse mode to use (default: "Markdown")
        **kwargs: Additional arguments to pass to send_message
        
    Returns:
        Message object on success, None on failure
    """
    # Validate and normalize parse_mode
    parse_mode = _normalize_parse_mode(parse_mode)
    
    # Attempt 1: Original text with specified parse_mode
    try:
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            **kwargs
        )
    except RetryAfter as e:
        # Handle rate limiting
        if e.retry_after > MAX_RETRY_DELAY:
            logger.warning(f"[Safe Send] Rate limit delay too long ({e.retry_after}s) for send_message to chat {chat_id}")
            return None
        
        delay = max(MIN_RETRY_DELAY, e.retry_after)
        jitter = delay * JITTER_RANGE * (2 * random.random() - 1)  # +/- JITTER_RANGE as percentage
        total_delay = max(MIN_RETRY_DELAY, delay + jitter)  # Ensure delay is always >= MIN_RETRY_DELAY
        logger.info(f"[Safe Send] Rate limited, retrying send_message to chat {chat_id} in {total_delay:.2f}s")
        await asyncio.sleep(total_delay)
        
        try:
            return await bot.send_message(chat_id=chat_id, text=text, parse_mode=parse_mode, **kwargs)
        except Exception as retry_error:
            logger.error(f"[Safe Send] Retry failed for send_message to chat {chat_id}: {retry_error}", exc_info=True)
            await handle_send_exception(retry_error, chat_id, "send_message")
            return None
            
    except BadRequest as e:
        error_msg = str(e).lower()
        
        # Check if this is an entity parsing error
        if "can't find end of the entity" in error_msg or "can't parse entities" in error_msg:
            logger.warning(f"[Safe Send] Entity parsing error for send_message to chat {chat_id}, attempting fallback")
            
            # Attempt 2: Escaped text with same parse_mode
            if parse_mode:
                try:
                    escaped_text = escape_text_for_parse_mode(text, parse_mode)
                    logger.debug(f"[Safe Send] Fallback stage 1: escaped text with {parse_mode}")
                    return await bot.send_message(
                        chat_id=chat_id,
                        text=escaped_text,
                        parse_mode=parse_mode,
                        **kwargs
                    )
                except (BadRequest, TelegramError) as retry_error:
                    logger.warning(f"[Safe Send] Escaped text failed for send_message to chat {chat_id}, trying plain text")
            
            # Attempt 3: Plain text with no parse_mode
            try:
                logger.debug(f"[Safe Send] Fallback stage 2: plain text (no parse_mode)")
                return await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=None,
                    **kwargs
                )
            except Exception as final_error:
                logger.error(f"[Safe Send] All send_message attempts failed for chat {chat_id}: {final_error}", exc_info=True)
                await handle_send_exception(final_error, chat_id, "send_message")
                return None
        else:
            # Not a parsing error, check if user should be removed
            await handle_send_exception(e, chat_id, "send_message")
            return None
            
    except (Forbidden, TelegramError) as e:
        # Handle other Telegram errors
        await handle_send_exception(e, chat_id, "send_message")
        return None
        
    except Exception as e:
        logger.error(f"[Safe Send] Unexpected error in send_message to chat {chat_id}: {e}", exc_info=True)
        return None


async def safe_edit_message_text(
    bot: Bot,
    text: str,
    chat_id: Optional[int] = None,
    message_id: Optional[int] = None,
    inline_message_id: Optional[str] = None,
    parse_mode: Optional[str] = "Markdown",
    **kwargs
) -> Optional[Message]:
    """
    Safely edit a message with entity parsing fallback and rate limiting.
    
    Falls back through: original -> escaped -> plain text if parsing fails.
    Handles rate limiting (429) with automatic retries.
    Removes deleted/blocked users from DB.
    
    Args:
        bot: Telegram bot instance
        text: New message text
        chat_id: Target chat ID (required if not using inline_message_id)
        message_id: Message ID to edit (required if not using inline_message_id)
        inline_message_id: Inline message ID to edit
        parse_mode: Parse mode to use (default: "Markdown")
        **kwargs: Additional arguments to pass to edit_message_text
        
    Returns:
        Message object on success, None on failure
    """
    # Validate and normalize parse_mode
    parse_mode = _normalize_parse_mode(parse_mode)
    
    # Attempt 1: Original text with specified parse_mode
    try:
        return await bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=message_id,
            inline_message_id=inline_message_id,
            parse_mode=parse_mode,
            **kwargs
        )
    except RetryAfter as e:
        # Handle rate limiting
        if e.retry_after > MAX_RETRY_DELAY:
            logger.warning(f"[Safe Send] Rate limit delay too long ({e.retry_after}s) for edit_message_text")
            return None
        
        delay = max(MIN_RETRY_DELAY, e.retry_after)
        jitter = delay * JITTER_RANGE * (2 * random.random() - 1)  # +/- JITTER_RANGE as percentage
        total_delay = max(MIN_RETRY_DELAY, delay + jitter)  # Ensure delay is always >= MIN_RETRY_DELAY
        logger.info(f"[Safe Send] Rate limited, retrying edit_message_text in {total_delay:.2f}s")
        await asyncio.sleep(total_delay)
        
        try:
            return await bot.edit_message_text(
                text=text, chat_id=chat_id, message_id=message_id,
                inline_message_id=inline_message_id, parse_mode=parse_mode, **kwargs
            )
        except Exception as retry_error:
            logger.error(f"[Safe Send] Retry failed for edit_message_text: {retry_error}", exc_info=True)
            if chat_id:
                await handle_send_exception(retry_error, chat_id, "edit_message_text")
            return None
            
    except BadRequest as e:
        error_msg = str(e).lower()
        
        # Check if this is an entity parsing error
        if "can't find end of the entity" in error_msg or "can't parse entities" in error_msg:
            logger.warning(f"[Safe Send] Entity parsing error for edit_message_text, attempting fallback")
            
            # Attempt 2: Escaped text with same parse_mode
            if parse_mode:
                try:
                    escaped_text = escape_text_for_parse_mode(text, parse_mode)
                    logger.debug(f"[Safe Send] Fallback stage 1: escaped text with {parse_mode}")
                    return await bot.edit_message_text(
                        text=escaped_text,
                        chat_id=chat_id,
                        message_id=message_id,
                        inline_message_id=inline_message_id,
                        parse_mode=parse_mode,
                        **kwargs
                    )
                except (BadRequest, TelegramError) as retry_error:
                    logger.warning(f"[Safe Send] Escaped text failed for edit_message_text, trying plain text")
            
            # Attempt 3: Plain text with no parse_mode
            try:
                logger.debug(f"[Safe Send] Fallback stage 2: plain text (no parse_mode)")
                return await bot.edit_message_text(
                    text=text,
                    chat_id=chat_id,
                    message_id=message_id,
                    inline_message_id=inline_message_id,
                    parse_mode=None,
                    **kwargs
                )
            except Exception as final_error:
                logger.error(f"[Safe Send] All edit_message_text attempts failed: {final_error}")
                if chat_id:
                    await handle_send_exception(final_error, chat_id, "edit_message_text")
                return None
        else:
            # Not a parsing error, check if user should be removed
            if chat_id:
                await handle_send_exception(e, chat_id, "edit_message_text")
            return None
            
    except (Forbidden, TelegramError) as e:
        # Handle other Telegram errors
        if chat_id:
            await handle_send_exception(e, chat_id, "edit_message_text")
        return None
        
    except Exception as e:
        logger.error(f"[Safe Send] Unexpected error in edit_message_text: {e}", exc_info=True)
        return None


async def safe_send_photo(
    bot: Bot,
    chat_id: int,
    photo: Any,
    caption: Optional[str] = None,
    parse_mode: Optional[str] = "Markdown",
    **kwargs
) -> Optional[Message]:
    """
    Safely send a photo with caption parsing fallback and rate limiting.
    
    Falls back through: original -> escaped -> plain caption if parsing fails.
    Handles rate limiting (429) with automatic retries.
    Removes deleted/blocked users from DB.
    
    Args:
        bot: Telegram bot instance
        chat_id: Target chat ID
        photo: Photo to send (file_id, URL, or file object)
        caption: Photo caption
        parse_mode: Parse mode to use for caption (default: "Markdown")
        **kwargs: Additional arguments to pass to send_photo
        
    Returns:
        Message object on success, None on failure
    """
    return await _safe_send_media_with_caption(
        bot,
        operation="send_photo",
        method_name="send_photo",
        media_arg_name="photo",
        media_value=photo,
        chat_id=chat_id,
        caption=caption,
        parse_mode=parse_mode,
        **kwargs,
    )


async def safe_send_video(
    bot: Bot,
    chat_id: int,
    video: Any,
    caption: Optional[str] = None,
    parse_mode: Optional[str] = "Markdown",
    **kwargs
) -> Optional[Message]:
    """
    Safely send a video with caption parsing fallback and rate limiting.
    
    Falls back through: original -> escaped -> plain caption if parsing fails.
    Handles rate limiting (429) with automatic retries.
    Removes deleted/blocked users from DB.
    
    Args:
        bot: Telegram bot instance
        chat_id: Target chat ID
        video: Video to send (file_id, URL, or file object)
        caption: Video caption
        parse_mode: Parse mode to use for caption (default: "Markdown")
        **kwargs: Additional arguments to pass to send_video
        
    Returns:
        Message object on success, None on failure
    """
    return await _safe_send_media_with_caption(
        bot,
        operation="send_video",
        method_name="send_video",
        media_arg_name="video",
        media_value=video,
        chat_id=chat_id,
        caption=caption,
        parse_mode=parse_mode,
        **kwargs,
    )


async def safe_send_animation(
    bot: Bot,
    chat_id: int,
    animation: Any,
    caption: Optional[str] = None,
    parse_mode: Optional[str] = "Markdown",
    **kwargs
) -> Optional[Message]:
    """
    Safely send an animation (GIF) with caption parsing fallback and rate limiting.
    
    Falls back through: original -> escaped -> plain caption if parsing fails.
    Handles rate limiting (429) with automatic retries.
    Removes deleted/blocked users from DB.
    
    Args:
        bot: Telegram bot instance
        chat_id: Target chat ID
        animation: Animation to send (file_id, URL, or file object)
        caption: Animation caption
        parse_mode: Parse mode to use for caption (default: "Markdown")
        **kwargs: Additional arguments to pass to send_animation
        
    Returns:
        Message object on success, None on failure
    """
    return await _safe_send_media_with_caption(
        bot,
        operation="send_animation",
        method_name="send_animation",
        media_arg_name="animation",
        media_value=animation,
        chat_id=chat_id,
        caption=caption,
        parse_mode=parse_mode,
        **kwargs,
    )


async def safe_send_document(
    bot: Bot,
    chat_id: int,
    document: Any,
    caption: Optional[str] = None,
    parse_mode: Optional[str] = "Markdown",
    **kwargs
) -> Optional[Message]:
    """
    Safely send a document with caption parsing fallback and rate limiting.
    
    Falls back through: original -> escaped -> plain caption if parsing fails.
    Handles rate limiting (429) with automatic retries.
    Removes deleted/blocked users from DB.
    
    Args:
        bot: Telegram bot instance
        chat_id: Target chat ID
        document: Document to send (file_id, URL, or file object)
        caption: Document caption
        parse_mode: Parse mode to use for caption (default: "Markdown")
        **kwargs: Additional arguments to pass to send_document
        
    Returns:
        Message object on success, None on failure
    """
    return await _safe_send_media_with_caption(
        bot,
        operation="send_document",
        method_name="send_document",
        media_arg_name="document",
        media_value=document,
        chat_id=chat_id,
        caption=caption,
        parse_mode=parse_mode,
        **kwargs,
    )


async def handle_handler_error(
    update: Update,
    user_id: int,
    error: Exception,
    logger_msg: str,
    logger_obj: logging.Logger = None
) -> None:
    """
    Centralized error handling for command handlers with translation support.

    This consolidates the common pattern of:
    1. Logging the error
    2. Getting user's language preference
    3. Getting translated error message
    4. Sending error message (callback query or regular message)

    Args:
        update: Telegram update object
        user_id: User ID for language preference lookup
        error: The exception that occurred
        logger_msg: Custom error message for logging
        logger_obj: Optional logger instance (uses module logger if not provided)
    """
    if logger_obj is None:
        logger_obj = logger

    # Log the error
    logger_obj.error(f"{logger_msg}: {error}")

    # Get user's language and translated error message
    try:
        from translations.translator import get_translated_string_async

        user_lang = await _db.get_user_language(user_id)
        error_text = await get_translated_string_async("error_occurred", user_lang)

        # Send error message based on update type
        if update.callback_query:
            await update.callback_query.answer(error_text, show_alert=True)
        else:
            await update.message.reply_text(error_text)
    except Exception as handler_error:
        logger_obj.error(f"Error in error handler: {handler_error}")


async def safe_update_message(
    update: Update,
    context,
    text: str,
    reply_markup=None,
    parse_mode: Optional[str] = "Markdown"
) -> Optional[Message]:
    """
    Safely update or send a message based on context.

    This consolidates the common pattern of:
    1. Check if update is from callback query
    2. Check if message contains media
    3. Send new message if media (can't edit media messages)
    4. Edit message if text-only
    5. Reply to message if not from callback query

    Args:
        update: Telegram update object
        context: Telegram context object
        text: Message text to send/edit
        reply_markup: Optional keyboard markup
        parse_mode: Parse mode for text (default: Markdown)

    Returns:
        Message object on success, None on failure
    """
    from utils.helpers import is_media_message

    if update.callback_query:
        query = update.callback_query
        message = query.message

        # Check if message contains media
        if is_media_message(message):
            # Send new message (can't edit media messages)
            return await safe_send_message(
                context.bot,
                chat_id=update.effective_chat.id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
        else:
            # Edit existing text message
            return await safe_edit_message_text(
                context.bot,
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode
            )
    else:
        # Reply to original message
        return await safe_send_message(
            context.bot,
            chat_id=update.effective_chat.id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode
        )

