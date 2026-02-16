"""
Safe wrappers for Telegram send/edit operations with entity parsing fallback.

This module provides robust wrappers that:
1. Handle entity parsing errors by falling back to plain text
2. Detect deleted/deactivated user accounts and trigger DB cleanup
3. Provide consistent error handling across all message operations
"""
import logging
from typing import Optional, Any
from html import escape as escape_html
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
            from database import Database
            db = Database()
            await db.delete_user(user_id)
            logger.info(f"[User Cleanup] Removed user {user_id} from database (reason: {operation} failed - {exception})")
            return True
        except Exception as db_error:
            logger.error(f"Failed to delete user {user_id} from database: {db_error}")
            return False
    
    return False


async def safe_send_message(
    bot: Bot,
    chat_id: int,
    text: str,
    parse_mode: Optional[str] = "Markdown",
    **kwargs
) -> Optional[Message]:
    """
    Safely send a message with entity parsing fallback.
    
    If entity parsing fails, retries with escaped text and then with no parse_mode.
    If user is deleted/deactivated, removes them from DB.
    
    Args:
        bot: Telegram bot instance
        chat_id: Target chat ID
        text: Message text
        parse_mode: Parse mode to use (default: "Markdown")
        **kwargs: Additional arguments to pass to send_message
        
    Returns:
        Message object on success, None on failure
    """
    try:
        # First attempt with specified parse_mode
        return await bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=parse_mode,
            **kwargs
        )
    except BadRequest as e:
        error_msg = str(e).lower()
        
        # Check if this is an entity parsing error
        if "can't find end of the entity" in error_msg or "can't parse entities" in error_msg:
            logger.warning(f"Entity parsing error for chat {chat_id}, retrying with escaped text: {e}")
            
            try:
                # Retry with escaped text
                escaped_text = escape_text_for_parse_mode(text, parse_mode)
                return await bot.send_message(
                    chat_id=chat_id,
                    text=escaped_text,
                    parse_mode=parse_mode,
                    **kwargs
                )
            except (BadRequest, TelegramError) as retry_error:
                logger.warning(f"Escaped text still failed for chat {chat_id}, falling back to plain text: {retry_error}")
                
                try:
                    # Final fallback: send as plain text with no parse_mode
                    return await bot.send_message(
                        chat_id=chat_id,
                        text=text,
                        parse_mode=None,
                        **kwargs
                    )
                except Exception as final_error:
                    logger.error(f"All send attempts failed for chat {chat_id}: {final_error}")
                    await handle_send_exception(final_error, chat_id, "send_message")
                    return None
        else:
            # Not a parsing error, check if user should be removed
            await handle_send_exception(e, chat_id, "send_message")
            return None
            
    except (Forbidden, TelegramError) as e:
        # Handle other Telegram errors (user blocked, deactivated, etc.)
        await handle_send_exception(e, chat_id, "send_message")
        return None
    except Exception as e:
        logger.error(f"Unexpected error sending message to chat {chat_id}: {e}")
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
    Safely edit a message with entity parsing fallback.
    
    If entity parsing fails, retries with escaped text and then with no parse_mode.
    If user is deleted/deactivated, removes them from DB.
    
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
    try:
        # First attempt with specified parse_mode
        return await bot.edit_message_text(
            text=text,
            chat_id=chat_id,
            message_id=message_id,
            inline_message_id=inline_message_id,
            parse_mode=parse_mode,
            **kwargs
        )
    except BadRequest as e:
        error_msg = str(e).lower()
        
        # Check if this is an entity parsing error
        if "can't find end of the entity" in error_msg or "can't parse entities" in error_msg:
            logger.warning(f"Entity parsing error editing message, retrying with escaped text: {e}")
            
            try:
                # Retry with escaped text
                escaped_text = escape_text_for_parse_mode(text, parse_mode)
                return await bot.edit_message_text(
                    text=escaped_text,
                    chat_id=chat_id,
                    message_id=message_id,
                    inline_message_id=inline_message_id,
                    parse_mode=parse_mode,
                    **kwargs
                )
            except (BadRequest, TelegramError) as retry_error:
                logger.warning(f"Escaped text still failed, falling back to plain text: {retry_error}")
                
                try:
                    # Final fallback: edit as plain text with no parse_mode
                    return await bot.edit_message_text(
                        text=text,
                        chat_id=chat_id,
                        message_id=message_id,
                        inline_message_id=inline_message_id,
                        parse_mode=None,
                        **kwargs
                    )
                except Exception as final_error:
                    logger.error(f"All edit attempts failed: {final_error}")
                    if chat_id:
                        await handle_send_exception(final_error, chat_id, "edit_message_text")
                    return None
        else:
            # Not a parsing error, check if user should be removed
            if chat_id:
                await handle_send_exception(e, chat_id, "edit_message_text")
            return None
            
    except (Forbidden, TelegramError) as e:
        # Handle other Telegram errors (user blocked, deactivated, etc.)
        if chat_id:
            await handle_send_exception(e, chat_id, "edit_message_text")
        return None
    except Exception as e:
        logger.error(f"Unexpected error editing message: {e}")
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
    Safely send a photo with caption parsing fallback.
    
    If caption parsing fails, retries with escaped caption and then with no parse_mode.
    If user is deleted/deactivated, removes them from DB.
    
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
    try:
        # First attempt with specified parse_mode
        return await bot.send_photo(
            chat_id=chat_id,
            photo=photo,
            caption=caption,
            parse_mode=parse_mode,
            **kwargs
        )
    except BadRequest as e:
        error_msg = str(e).lower()
        
        # Check if this is a caption parsing error
        if caption and ("can't find end of the entity" in error_msg or "can't parse entities" in error_msg):
            logger.warning(f"Caption parsing error for chat {chat_id}, retrying with escaped caption: {e}")
            
            try:
                # Retry with escaped caption
                escaped_caption = escape_text_for_parse_mode(caption, parse_mode)
                return await bot.send_photo(
                    chat_id=chat_id,
                    photo=photo,
                    caption=escaped_caption,
                    parse_mode=parse_mode,
                    **kwargs
                )
            except (BadRequest, TelegramError) as retry_error:
                logger.warning(f"Escaped caption still failed for chat {chat_id}, falling back to plain caption: {retry_error}")
                
                try:
                    # Final fallback: send with plain caption (no parse_mode)
                    return await bot.send_photo(
                        chat_id=chat_id,
                        photo=photo,
                        caption=caption,
                        parse_mode=None,
                        **kwargs
                    )
                except Exception as final_error:
                    logger.error(f"All photo send attempts failed for chat {chat_id}: {final_error}")
                    await handle_send_exception(final_error, chat_id, "send_photo")
                    return None
        else:
            # Not a parsing error, check if user should be removed
            await handle_send_exception(e, chat_id, "send_photo")
            return None
            
    except (Forbidden, TelegramError) as e:
        # Handle other Telegram errors (user blocked, deactivated, etc.)
        await handle_send_exception(e, chat_id, "send_photo")
        return None
    except Exception as e:
        logger.error(f"Unexpected error sending photo to chat {chat_id}: {e}")
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
    Safely send a video with caption parsing fallback.
    
    If caption parsing fails, retries with escaped caption and then with no parse_mode.
    If user is deleted/deactivated, removes them from DB.
    
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
    try:
        # First attempt with specified parse_mode
        return await bot.send_video(
            chat_id=chat_id,
            video=video,
            caption=caption,
            parse_mode=parse_mode,
            **kwargs
        )
    except BadRequest as e:
        error_msg = str(e).lower()
        
        # Check if this is a caption parsing error
        if caption and ("can't find end of the entity" in error_msg or "can't parse entities" in error_msg):
            logger.warning(f"Caption parsing error for chat {chat_id}, retrying with escaped caption: {e}")
            
            try:
                # Retry with escaped caption
                escaped_caption = escape_text_for_parse_mode(caption, parse_mode)
                return await bot.send_video(
                    chat_id=chat_id,
                    video=video,
                    caption=escaped_caption,
                    parse_mode=parse_mode,
                    **kwargs
                )
            except (BadRequest, TelegramError) as retry_error:
                logger.warning(f"Escaped caption still failed for chat {chat_id}, falling back to plain caption: {retry_error}")
                
                try:
                    # Final fallback: send with plain caption (no parse_mode)
                    return await bot.send_video(
                        chat_id=chat_id,
                        video=video,
                        caption=caption,
                        parse_mode=None,
                        **kwargs
                    )
                except Exception as final_error:
                    logger.error(f"All video send attempts failed for chat {chat_id}: {final_error}")
                    await handle_send_exception(final_error, chat_id, "send_video")
                    return None
        else:
            # Not a parsing error, check if user should be removed
            await handle_send_exception(e, chat_id, "send_video")
            return None
            
    except (Forbidden, TelegramError) as e:
        # Handle other Telegram errors (user blocked, deactivated, etc.)
        await handle_send_exception(e, chat_id, "send_video")
        return None
    except Exception as e:
        logger.error(f"Unexpected error sending video to chat {chat_id}: {e}")
        return None
