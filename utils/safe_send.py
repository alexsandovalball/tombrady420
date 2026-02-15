"""
Safe wrappers for Telegram send/edit operations with entity parsing fallback.

This module provides robust wrappers that:
1. Handle entity parsing errors by falling back to plain text
2. Detect deleted/deactivated user accounts and trigger DB cleanup
3. Provide consistent error handling across all message operations
"""
import logging
from typing import Optional, Any, Callable, Awaitable
from html import escape as escape_html
import database
from telegram import Bot, Message
from telegram.error import TelegramError, BadRequest, Forbidden
from telegram.helpers import escape_markdown as escape_markdown_v2

logger = logging.getLogger(__name__)


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
    
    # Patterns indicating a permanently unreachable user account
    deleted_user_patterns = [
        "user is deactivated",
        "user not found",
        "chat not found",
        "user has been deleted",
        "bot was blocked by the user",
        "bot can't initiate conversation",
        "user_deleted",
        "user_deactivated"
    ]
    
    # Check if this is a deleted/blocked user error
    should_remove = False
    
    if isinstance(exception, Forbidden):
        # 403 Forbidden typically means bot was blocked or user is unreachable
        should_remove = True
        logger.info(f"User {user_id} blocked bot or is unreachable (Forbidden): {exception}")
    elif isinstance(exception, BadRequest):
        # Check if BadRequest message indicates deleted user
        if any(pattern in error_msg for pattern in deleted_user_patterns):
            should_remove = True
            logger.info(f"User {user_id} account deleted/deactivated (BadRequest): {exception}")
    
    # If user should be removed, trigger DB cleanup
    if should_remove:
        try:
            db = database.Database()
            await db.delete_user(user_id)
            logger.info(f"[User Cleanup] Removed user {user_id} from database (reason: {operation} failed - {exception})")
            return True
        except Exception as db_error:
            logger.error(f"Failed to delete user {user_id} from database: {db_error}")
            return False
    
    return False


def _is_entity_parsing_error(error_msg: str) -> bool:
    """Determine if a BadRequest message is due to entity parsing issues."""
    lowered = error_msg.lower()
    return "can't find end of the entity" in lowered or "can't parse entities" in lowered


async def _execute_with_fallback(
    send_callable: Callable[[Optional[str], Optional[str]], Awaitable[Message]],
    payload: Optional[str],
    parse_mode: Optional[str],
    chat_id: Optional[int],
    operation: str,
    parsing_context: str
) -> Optional[Message]:
    """
    Execute a Telegram send/edit operation with parsing fallbacks.

    Args:
        send_callable: Coroutine function accepting (payload, parse_mode)
        payload: Text or caption to send
        parse_mode: Parse mode to use
        chat_id: Chat ID for error handling
        operation: Operation name for logging/cleanup
        parsing_context: Context label for logging (e.g., "Entity", "Caption")
    """
    try:
        return await send_callable(payload, parse_mode)
    except BadRequest as e:
        if payload and _is_entity_parsing_error(str(e)):
            logger.warning(f"{parsing_context} parsing error for chat {chat_id}, retrying with escaped text: {e}")
            try:
                escaped_payload = escape_text_for_parse_mode(payload, parse_mode)
                return await send_callable(escaped_payload, parse_mode)
            except (BadRequest, TelegramError) as retry_error:
                logger.warning(f"Escaped {parsing_context.lower()} still failed for chat {chat_id}, falling back to plain text: {retry_error}")
                try:
                    return await send_callable(payload, None)
                except Exception as final_error:
                    logger.error(f"All {operation} attempts failed for chat {chat_id}: {final_error}")
                    if chat_id:
                        await handle_send_exception(final_error, chat_id, operation)
                    return None
        if chat_id:
            await handle_send_exception(e, chat_id, operation)
        return None
    except (Forbidden, TelegramError) as e:
        if chat_id:
            await handle_send_exception(e, chat_id, operation)
        return None
    except Exception as e:
        logger.error(f"Unexpected error during {operation}: {e}")
        return None


async def safe_send_message(
    bot: Bot,
    chat_id: int,
    text: str,
    parse_mode: Optional[str] = "Markdown",
    **kwargs
) -> Optional[Message]:
    """
    Safely send a message with entity parsing fallback.
    """
    async def _send(payload: Optional[str], mode: Optional[str]) -> Message:
        return await bot.send_message(chat_id=chat_id, text=payload, parse_mode=mode, **kwargs)

    return await _execute_with_fallback(
        _send,
        text,
        parse_mode,
        chat_id,
        "send_message",
        "Entity"
    )


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
    Safely edit a message with entity parsing fallback.
    """
    async def _edit(payload: Optional[str], mode: Optional[str]) -> Message:
        return await bot.edit_message_text(
            text=payload,
            chat_id=chat_id,
            message_id=message_id,
            inline_message_id=inline_message_id,
            parse_mode=mode,
            **kwargs
        )

    return await _execute_with_fallback(
        _edit,
        text,
        parse_mode,
        chat_id,
        "edit_message_text",
        "Entity"
    )


async def safe_send_photo(
    bot: Bot,
    chat_id: int,
    photo: Any,
    caption: Optional[str] = None,
    parse_mode: Optional[str] = "Markdown",
    **kwargs
) -> Optional[Message]:
    """
    Safely send a photo with caption parsing fallback.
    """
    async def _send(payload: Optional[str], mode: Optional[str]) -> Message:
        return await bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=payload,
            parse_mode=mode,
            **kwargs
        )

    return await _execute_with_fallback(
        _send,
        caption,
        parse_mode,
        chat_id,
        "send_photo",
        "Caption"
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
    Safely send a video with caption parsing fallback.
    """
    async def _send(payload: Optional[str], mode: Optional[str]) -> Message:
        return await bot.send_video(
            chat_id=chat_id,
            video=video,
            caption=payload,
            parse_mode=mode,
            **kwargs
        )

    return await _execute_with_fallback(
        _send,
        caption,
        parse_mode,
        chat_id,
        "send_video",
        "Caption"
    )
