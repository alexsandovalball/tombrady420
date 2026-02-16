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
from typing import Optional, Any, Callable, Dict, Tuple
from html import escape as escape_html
from telegram import Bot, Message
from telegram.error import TelegramError, BadRequest, Forbidden, RetryAfter
from telegram.helpers import escape_markdown as escape_markdown_v2

logger = logging.getLogger(__name__)

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
            from database import Database
            db = Database()
            await db.delete_user(user_id)
            logger.info(f"[User Cleanup] Removed user {user_id} from database (reason: {operation} failed)")
            return True
        except Exception as db_error:
            logger.error(f"[User Cleanup] Failed to delete user {user_id} from database: {db_error}")
            return False
    
    return False


async def _execute_with_fallback(
    operation: str,
    chat_id: int,
    telegram_call: Callable,
    text_or_caption: Optional[str],
    parse_mode: Optional[str],
    retry_count: int = 0
) -> Optional[Message]:
    """
    Internal executor for Telegram operations with fallback and rate limiting.
    
    This is the centralized handler that:
    1. Executes the Telegram API call
    2. Handles rate limiting (429/RetryAfter) with bounded retries
    3. Falls back through parse modes on entity errors (original -> escaped -> plain)
    4. Triggers user cleanup for unreachable accounts
    
    Args:
        operation: Operation name for logging (e.g., "send_message", "send_photo")
        chat_id: Target chat ID
        telegram_call: Async callable that executes the Telegram API call
        text_or_caption: Text or caption to process (None for media-only)
        parse_mode: Current parse mode to use
        retry_count: Current retry attempt (for rate limiting)
        
    Returns:
        Message object on success, None on failure
    """
    try:
        logger.debug(f"[Safe Send] {operation} to chat {chat_id}, parse_mode={parse_mode}, retry={retry_count}")
        return await telegram_call()
        
    except RetryAfter as e:
        # Handle Telegram rate limiting (429)
        if retry_count >= MAX_RETRIES:
            logger.warning(f"[Safe Send] Rate limit max retries ({MAX_RETRIES}) exceeded for {operation} to chat {chat_id}")
            return None
        
        # Use server-specified delay with bounded limits and jitter
        delay = max(MIN_RETRY_DELAY, min(e.retry_after, MAX_RETRY_DELAY))
        jitter = delay * JITTER_RANGE * (2 * random.random() - 1)  # +/- JITTER_RANGE
        total_delay = delay + jitter
        
        logger.info(f"[Safe Send] Rate limited (429) for {operation} to chat {chat_id}, "
                   f"retrying in {total_delay:.2f}s (attempt {retry_count + 1}/{MAX_RETRIES})")
        
        await asyncio.sleep(total_delay)
        return await _execute_with_fallback(
            operation, chat_id, telegram_call, text_or_caption, parse_mode, retry_count + 1
        )
        
    except BadRequest as e:
        error_msg = str(e).lower()
        
        # Check if this is an entity parsing error that we can fix
        if text_or_caption and ("can't find end of the entity" in error_msg or "can't parse entities" in error_msg):
            logger.warning(f"[Safe Send] Entity parsing error for {operation} to chat {chat_id}, attempting fallback")
            # Return None to signal wrapper to try escaped/plain text fallback
            return None
        else:
            # Not a parsing error, check if user should be removed
            await handle_send_exception(e, chat_id, operation)
            return None
            
    except (Forbidden, TelegramError) as e:
        # Handle other Telegram errors (user blocked, deactivated, etc.)
        await handle_send_exception(e, chat_id, operation)
        return None
        
    except Exception as e:
        logger.error(f"[Safe Send] Unexpected error in {operation} to chat {chat_id}: {e}")
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
    if parse_mode and parse_mode.upper() not in ["HTML", "MARKDOWN", "MARKDOWNV2"]:
        logger.warning(f"[Safe Send] Unknown parse_mode '{parse_mode}', falling back to Markdown")
        parse_mode = "Markdown"
    
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
            logger.error(f"[Safe Send] Retry failed for send_message to chat {chat_id}: {retry_error}")
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
                logger.error(f"[Safe Send] All send_message attempts failed for chat {chat_id}: {final_error}")
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
        logger.error(f"[Safe Send] Unexpected error in send_message to chat {chat_id}: {e}")
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
    if parse_mode and parse_mode.upper() not in ["HTML", "MARKDOWN", "MARKDOWNV2"]:
        logger.warning(f"[Safe Send] Unknown parse_mode '{parse_mode}', falling back to Markdown")
        parse_mode = "Markdown"
    
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
        await asyncio.sleep(delay)
        
        try:
            return await bot.edit_message_text(
                text=text, chat_id=chat_id, message_id=message_id,
                inline_message_id=inline_message_id, parse_mode=parse_mode, **kwargs
            )
        except Exception as retry_error:
            logger.error(f"[Safe Send] Retry failed for edit_message_text: {retry_error}")
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
        logger.error(f"[Safe Send] Unexpected error in edit_message_text: {e}")
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
    # Validate and normalize parse_mode
    if parse_mode and parse_mode.upper() not in ["HTML", "MARKDOWN", "MARKDOWNV2"]:
        logger.warning(f"[Safe Send] Unknown parse_mode '{parse_mode}', falling back to Markdown")
        parse_mode = "Markdown"
    
    # Attempt 1: Original caption with specified parse_mode
    try:
        return await bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=caption,
            parse_mode=parse_mode,
            **kwargs
        )
    except RetryAfter as e:
        # Handle rate limiting
        if e.retry_after > MAX_RETRY_DELAY:
            logger.warning(f"[Safe Send] Rate limit delay too long ({e.retry_after}s) for send_photo to chat {chat_id}")
            return None
        
        delay = max(MIN_RETRY_DELAY, e.retry_after)
        jitter = delay * JITTER_RANGE * (2 * random.random() - 1)  # +/- JITTER_RANGE as percentage
        total_delay = max(MIN_RETRY_DELAY, delay + jitter)  # Ensure delay is always >= MIN_RETRY_DELAY
        logger.info(f"[Safe Send] Rate limited, retrying send_photo to chat {chat_id} in {total_delay:.2f}s")
        await asyncio.sleep(delay)
        
        try:
            return await bot.send_photo(
                chat_id=chat_id, photo=photo, caption=caption, parse_mode=parse_mode, **kwargs
            )
        except Exception as retry_error:
            logger.error(f"[Safe Send] Retry failed for send_photo to chat {chat_id}: {retry_error}")
            await handle_send_exception(retry_error, chat_id, "send_photo")
            return None
            
    except BadRequest as e:
        error_msg = str(e).lower()
        
        # Check if this is a caption parsing error
        if caption and ("can't find end of the entity" in error_msg or "can't parse entities" in error_msg):
            logger.warning(f"[Safe Send] Caption parsing error for send_photo to chat {chat_id}, attempting fallback")
            
            # Attempt 2: Escaped caption with same parse_mode
            if parse_mode:
                try:
                    escaped_caption = escape_text_for_parse_mode(caption, parse_mode)
                    logger.debug(f"[Safe Send] Fallback stage 1: escaped caption with {parse_mode}")
                    return await bot.send_photo(
                        chat_id=chat_id,
                        photo=photo,
                        caption=escaped_caption,
                        parse_mode=parse_mode,
                        **kwargs
                    )
                except (BadRequest, TelegramError) as retry_error:
                    logger.warning(f"[Safe Send] Escaped caption failed for send_photo to chat {chat_id}, trying plain caption")
            
            # Attempt 3: Plain caption with no parse_mode
            try:
                logger.debug(f"[Safe Send] Fallback stage 2: plain caption (no parse_mode)")
                return await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=caption,
                    parse_mode=None,
                    **kwargs
                )
            except Exception as final_error:
                logger.error(f"[Safe Send] All send_photo attempts failed for chat {chat_id}: {final_error}")
                await handle_send_exception(final_error, chat_id, "send_photo")
                return None
        else:
            # Not a parsing error, check if user should be removed
            await handle_send_exception(e, chat_id, "send_photo")
            return None
            
    except (Forbidden, TelegramError) as e:
        # Handle other Telegram errors
        await handle_send_exception(e, chat_id, "send_photo")
        return None
        
    except Exception as e:
        logger.error(f"[Safe Send] Unexpected error in send_photo to chat {chat_id}: {e}")
        return None


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
    # Validate and normalize parse_mode
    if parse_mode and parse_mode.upper() not in ["HTML", "MARKDOWN", "MARKDOWNV2"]:
        logger.warning(f"[Safe Send] Unknown parse_mode '{parse_mode}', falling back to Markdown")
        parse_mode = "Markdown"
    
    # Attempt 1: Original caption with specified parse_mode
    try:
        return await bot.send_video(
            chat_id=chat_id,
            video=video,
            caption=caption,
            parse_mode=parse_mode,
            **kwargs
        )
    except RetryAfter as e:
        # Handle rate limiting
        if e.retry_after > MAX_RETRY_DELAY:
            logger.warning(f"[Safe Send] Rate limit delay too long ({e.retry_after}s) for send_video to chat {chat_id}")
            return None
        
        delay = max(MIN_RETRY_DELAY, e.retry_after)
        jitter = delay * JITTER_RANGE * (2 * random.random() - 1)  # +/- JITTER_RANGE as percentage
        total_delay = max(MIN_RETRY_DELAY, delay + jitter)  # Ensure delay is always >= MIN_RETRY_DELAY
        logger.info(f"[Safe Send] Rate limited, retrying send_video to chat {chat_id} in {total_delay:.2f}s")
        await asyncio.sleep(delay)
        
        try:
            return await bot.send_video(
                chat_id=chat_id, video=video, caption=caption, parse_mode=parse_mode, **kwargs
            )
        except Exception as retry_error:
            logger.error(f"[Safe Send] Retry failed for send_video to chat {chat_id}: {retry_error}")
            await handle_send_exception(retry_error, chat_id, "send_video")
            return None
            
    except BadRequest as e:
        error_msg = str(e).lower()
        
        # Check if this is a caption parsing error
        if caption and ("can't find end of the entity" in error_msg or "can't parse entities" in error_msg):
            logger.warning(f"[Safe Send] Caption parsing error for send_video to chat {chat_id}, attempting fallback")
            
            # Attempt 2: Escaped caption with same parse_mode
            if parse_mode:
                try:
                    escaped_caption = escape_text_for_parse_mode(caption, parse_mode)
                    logger.debug(f"[Safe Send] Fallback stage 1: escaped caption with {parse_mode}")
                    return await bot.send_video(
                        chat_id=chat_id,
                        video=video,
                        caption=escaped_caption,
                        parse_mode=parse_mode,
                        **kwargs
                    )
                except (BadRequest, TelegramError) as retry_error:
                    logger.warning(f"[Safe Send] Escaped caption failed for send_video to chat {chat_id}, trying plain caption")
            
            # Attempt 3: Plain caption with no parse_mode
            try:
                logger.debug(f"[Safe Send] Fallback stage 2: plain caption (no parse_mode)")
                return await bot.send_video(
                    chat_id=chat_id,
                    video=video,
                    caption=caption,
                    parse_mode=None,
                    **kwargs
                )
            except Exception as final_error:
                logger.error(f"[Safe Send] All send_video attempts failed for chat {chat_id}: {final_error}")
                await handle_send_exception(final_error, chat_id, "send_video")
                return None
        else:
            # Not a parsing error, check if user should be removed
            await handle_send_exception(e, chat_id, "send_video")
            return None
            
    except (Forbidden, TelegramError) as e:
        # Handle other Telegram errors
        await handle_send_exception(e, chat_id, "send_video")
        return None
        
    except Exception as e:
        logger.error(f"[Safe Send] Unexpected error in send_video to chat {chat_id}: {e}")
        return None


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
    # Validate and normalize parse_mode
    if parse_mode and parse_mode.upper() not in ["HTML", "MARKDOWN", "MARKDOWNV2"]:
        logger.warning(f"[Safe Send] Unknown parse_mode '{parse_mode}', falling back to Markdown")
        parse_mode = "Markdown"
    
    # Attempt 1: Original caption with specified parse_mode
    try:
        return await bot.send_animation(
            chat_id=chat_id,
            animation=animation,
            caption=caption,
            parse_mode=parse_mode,
            **kwargs
        )
    except RetryAfter as e:
        # Handle rate limiting
        if e.retry_after > MAX_RETRY_DELAY:
            logger.warning(f"[Safe Send] Rate limit delay too long ({e.retry_after}s) for send_animation to chat {chat_id}")
            return None
        
        delay = max(MIN_RETRY_DELAY, e.retry_after)
        jitter = delay * JITTER_RANGE * (2 * random.random() - 1)  # +/- JITTER_RANGE as percentage
        total_delay = max(MIN_RETRY_DELAY, delay + jitter)  # Ensure delay is always >= MIN_RETRY_DELAY
        logger.info(f"[Safe Send] Rate limited, retrying send_animation to chat {chat_id} in {total_delay:.2f}s")
        await asyncio.sleep(delay)
        
        try:
            return await bot.send_animation(
                chat_id=chat_id, animation=animation, caption=caption, parse_mode=parse_mode, **kwargs
            )
        except Exception as retry_error:
            logger.error(f"[Safe Send] Retry failed for send_animation to chat {chat_id}: {retry_error}")
            await handle_send_exception(retry_error, chat_id, "send_animation")
            return None
            
    except BadRequest as e:
        error_msg = str(e).lower()
        
        # Check if this is a caption parsing error
        if caption and ("can't find end of the entity" in error_msg or "can't parse entities" in error_msg):
            logger.warning(f"[Safe Send] Caption parsing error for send_animation to chat {chat_id}, attempting fallback")
            
            # Attempt 2: Escaped caption with same parse_mode
            if parse_mode:
                try:
                    escaped_caption = escape_text_for_parse_mode(caption, parse_mode)
                    logger.debug(f"[Safe Send] Fallback stage 1: escaped caption with {parse_mode}")
                    return await bot.send_animation(
                        chat_id=chat_id,
                        animation=animation,
                        caption=escaped_caption,
                        parse_mode=parse_mode,
                        **kwargs
                    )
                except (BadRequest, TelegramError) as retry_error:
                    logger.warning(f"[Safe Send] Escaped caption failed for send_animation to chat {chat_id}, trying plain caption")
            
            # Attempt 3: Plain caption with no parse_mode
            try:
                logger.debug(f"[Safe Send] Fallback stage 2: plain caption (no parse_mode)")
                return await bot.send_animation(
                    chat_id=chat_id,
                    animation=animation,
                    caption=caption,
                    parse_mode=None,
                    **kwargs
                )
            except Exception as final_error:
                logger.error(f"[Safe Send] All send_animation attempts failed for chat {chat_id}: {final_error}")
                await handle_send_exception(final_error, chat_id, "send_animation")
                return None
        else:
            # Not a parsing error, check if user should be removed
            await handle_send_exception(e, chat_id, "send_animation")
            return None
            
    except (Forbidden, TelegramError) as e:
        # Handle other Telegram errors
        await handle_send_exception(e, chat_id, "send_animation")
        return None
        
    except Exception as e:
        logger.error(f"[Safe Send] Unexpected error in send_animation to chat {chat_id}: {e}")
        return None


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
    # Validate and normalize parse_mode
    if parse_mode and parse_mode.upper() not in ["HTML", "MARKDOWN", "MARKDOWNV2"]:
        logger.warning(f"[Safe Send] Unknown parse_mode '{parse_mode}', falling back to Markdown")
        parse_mode = "Markdown"
    
    # Attempt 1: Original caption with specified parse_mode
    try:
        return await bot.send_document(
            chat_id=chat_id,
            document=document,
            caption=caption,
            parse_mode=parse_mode,
            **kwargs
        )
    except RetryAfter as e:
        # Handle rate limiting
        if e.retry_after > MAX_RETRY_DELAY:
            logger.warning(f"[Safe Send] Rate limit delay too long ({e.retry_after}s) for send_document to chat {chat_id}")
            return None
        
        delay = max(MIN_RETRY_DELAY, e.retry_after)
        jitter = delay * JITTER_RANGE * (2 * random.random() - 1)  # +/- JITTER_RANGE as percentage
        total_delay = max(MIN_RETRY_DELAY, delay + jitter)  # Ensure delay is always >= MIN_RETRY_DELAY
        logger.info(f"[Safe Send] Rate limited, retrying send_document to chat {chat_id} in {total_delay:.2f}s")
        await asyncio.sleep(delay)
        
        try:
            return await bot.send_document(
                chat_id=chat_id, document=document, caption=caption, parse_mode=parse_mode, **kwargs
            )
        except Exception as retry_error:
            logger.error(f"[Safe Send] Retry failed for send_document to chat {chat_id}: {retry_error}")
            await handle_send_exception(retry_error, chat_id, "send_document")
            return None
            
    except BadRequest as e:
        error_msg = str(e).lower()
        
        # Check if this is a caption parsing error
        if caption and ("can't find end of the entity" in error_msg or "can't parse entities" in error_msg):
            logger.warning(f"[Safe Send] Caption parsing error for send_document to chat {chat_id}, attempting fallback")
            
            # Attempt 2: Escaped caption with same parse_mode
            if parse_mode:
                try:
                    escaped_caption = escape_text_for_parse_mode(caption, parse_mode)
                    logger.debug(f"[Safe Send] Fallback stage 1: escaped caption with {parse_mode}")
                    return await bot.send_document(
                        chat_id=chat_id,
                        document=document,
                        caption=escaped_caption,
                        parse_mode=parse_mode,
                        **kwargs
                    )
                except (BadRequest, TelegramError) as retry_error:
                    logger.warning(f"[Safe Send] Escaped caption failed for send_document to chat {chat_id}, trying plain caption")
            
            # Attempt 3: Plain caption with no parse_mode
            try:
                logger.debug(f"[Safe Send] Fallback stage 2: plain caption (no parse_mode)")
                return await bot.send_document(
                    chat_id=chat_id,
                    document=document,
                    caption=caption,
                    parse_mode=None,
                    **kwargs
                )
            except Exception as final_error:
                logger.error(f"[Safe Send] All send_document attempts failed for chat {chat_id}: {final_error}")
                await handle_send_exception(final_error, chat_id, "send_document")
                return None
        else:
            # Not a parsing error, check if user should be removed
            await handle_send_exception(e, chat_id, "send_document")
            return None
            
    except (Forbidden, TelegramError) as e:
        # Handle other Telegram errors
        await handle_send_exception(e, chat_id, "send_document")
        return None
        
    except Exception as e:
        logger.error(f"[Safe Send] Unexpected error in send_document to chat {chat_id}: {e}")
        return None
