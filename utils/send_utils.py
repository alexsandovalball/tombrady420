"""
Robust message sending utilities with retry and fallback support.

This module provides reliable message delivery across multiple bot instances
with automatic retry for transient errors and fallback to alternative bots
for permanent failures.
"""
import logging
import asyncio
from typing import Optional, Dict, Any, List
from datetime import datetime, timezone
from telegram import Bot, InlineKeyboardMarkup
from telegram.error import (
    TelegramError, 
    Forbidden, 
    BadRequest, 
    TimedOut, 
    NetworkError,
    RetryAfter
)

logger = logging.getLogger(__name__)

# Retry configuration
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BASE_DELAY = 1.0  # seconds
DEFAULT_MAX_RETRY_DELAY = 30.0  # seconds

# Permanent error patterns that should trigger immediate fallback
PERMANENT_ERROR_PATTERNS = [
    "chat not found",
    "user not found",
    "user is deactivated",
    "chat_not_found",
    "bot was blocked by the user",
    "user_is_deactivated"
]


class SendAttemptResult:
    """Result of a message send attempt."""
    
    def __init__(
        self, 
        success: bool, 
        user_id: int, 
        bot_username: str,
        bot_key: str,
        error: Optional[str] = None,
        is_permanent: bool = False,
        retry_count: int = 0,
        timestamp: Optional[datetime] = None
    ):
        self.success = success
        self.user_id = user_id
        self.bot_username = bot_username
        self.bot_key = bot_key
        self.error = error
        self.is_permanent = is_permanent
        self.retry_count = retry_count
        self.timestamp = timestamp or datetime.now(timezone.utc)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging/storage."""
        return {
            "success": self.success,
            "user_id": self.user_id,
            "bot_username": self.bot_username,
            "bot_key": self.bot_key,
            "error": self.error,
            "is_permanent": self.is_permanent,
            "retry_count": self.retry_count,
            "timestamp": self.timestamp.isoformat()
        }


def is_permanent_error(error: Exception) -> bool:
    """
    Determine if an error is permanent (requiring fallback) or transient (retry).
    
    Permanent errors:
    - Forbidden: Bot was blocked by user
    - BadRequest: Chat not found, user deleted, etc.
    - Unauthorized: Invalid bot token
    
    Transient errors:
    - TimedOut: Network timeout
    - NetworkError: Connection issues
    - RetryAfter/FloodWait: Rate limiting
    
    Args:
        error: The exception to check
        
    Returns:
        True if error is permanent, False if transient
    """
    # Forbidden errors are always permanent
    if isinstance(error, Forbidden):
        return True
    
    # Check BadRequest for permanent patterns
    if isinstance(error, BadRequest):
        error_msg = str(error).lower()
        return any(pattern in error_msg for pattern in PERMANENT_ERROR_PATTERNS)
    
    # Transient errors that should be retried
    if isinstance(error, (TimedOut, NetworkError, RetryAfter)):
        return False
    
    # For TelegramError, check for rate limit patterns (transient)
    if isinstance(error, TelegramError):
        error_msg = str(error).lower()
        if "too many requests" in error_msg or "retry after" in error_msg:
            return False
    
    # Unknown errors are considered permanent to avoid infinite retries
    return True


async def send_with_retry_and_fallback(
    user_id: int,
    message_text: str,
    primary_bot: Bot,
    primary_bot_key: str,
    fallback_bots: Optional[List[tuple[Bot, str]]] = None,
    parse_mode: str = "Markdown",
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    max_retries: int = DEFAULT_MAX_RETRIES,
    retry_base_delay: float = DEFAULT_RETRY_BASE_DELAY,
    max_retry_delay: float = DEFAULT_MAX_RETRY_DELAY,
    db = None
) -> SendAttemptResult:
    """
    Send a message with retry and fallback logic.
    
    Behavior:
    1. Try primary bot first
    2. On permanent failures (Unauthorized, BadRequest, ChatNotFound):
       - Immediately try next fallback bot
    3. On transient failures (TimedOut, NetworkError, RetryAfter):
       - Retry with exponential backoff
       - After max retries, escalate to fallback bots
    4. Record send attempt metadata to database (if provided)
    5. Ensure idempotency where possible
    
    Args:
        user_id: Target user ID
        message_text: Message text to send
        primary_bot: Primary Bot instance to try first
        primary_bot_key: Environment key for primary bot (e.g., 'BOT_TOKEN')
        fallback_bots: List of (Bot, bot_key) tuples for fallback
        parse_mode: Telegram parse mode (default: Markdown)
        reply_markup: Optional inline keyboard markup
        max_retries: Maximum retry attempts for transient errors
        retry_base_delay: Base delay for exponential backoff (seconds)
        max_retry_delay: Maximum delay between retries (seconds)
        db: Optional database instance for logging send attempts
        
    Returns:
        SendAttemptResult with details of the send attempt
        
    Example:
        >>> result = await send_with_retry_and_fallback(
        ...     user_id=12345,
        ...     message_text="Hello!",
        ...     primary_bot=bot1,
        ...     primary_bot_key='BOT_TOKEN',
        ...     fallback_bots=[(bot2, 'BOT_TOKEN_1'), (bot3, 'BOT_TOKEN_2')]
        ... )
        >>> if result.success:
        ...     print(f"Sent via {result.bot_username}")
    """
    fallback_bots = fallback_bots or []
    all_bots = [(primary_bot, primary_bot_key)] + fallback_bots
    
    logger.info(
        f"[SendUtils] Attempting to send message to user {user_id} "
        f"(primary: {primary_bot_key}, fallbacks: {len(fallback_bots)})"
    )
    
    for bot_index, (bot, bot_key) in enumerate(all_bots):
        is_primary = bot_index == 0
        bot_username = getattr(bot, 'username', 'unknown')
        
        # Try this bot with retries (if transient errors occur)
        retry_count = 0
        
        while retry_count <= max_retries:
            try:
                timestamp = datetime.now(timezone.utc)
                
                logger.info(
                    f"[SendUtils] Send attempt {retry_count + 1}/{max_retries + 1} - "
                    f"user_id={user_id}, bot=@{bot_username} ({bot_key}), "
                    f"message_preview={message_text[:50]}..."
                )
                
                # Attempt to send the message
                sent_message = await bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup
                )
                
                # Success!
                result = SendAttemptResult(
                    success=True,
                    user_id=user_id,
                    bot_username=bot_username,
                    bot_key=bot_key,
                    retry_count=retry_count,
                    timestamp=timestamp
                )
                
                logger.info(
                    f"[SendUtils] ✓ SUCCESS - user_id={user_id}, bot=@{bot_username}, "
                    f"retries={retry_count}, message_id={sent_message.message_id}"
                )
                
                # Log to database if available
                if db:
                    try:
                        await _log_send_attempt(db, result, message_text)
                    except Exception as e:
                        logger.warning(f"[SendUtils] Failed to log send attempt: {e}")
                
                return result
                
            except Exception as e:
                is_perm = is_permanent_error(e)
                error_type = "PERMANENT" if is_perm else "TRANSIENT"
                
                logger.warning(
                    f"[SendUtils] ✗ {error_type} ERROR - "
                    f"user_id={user_id}, bot=@{bot_username} ({bot_key}), "
                    f"retry={retry_count}, error={type(e).__name__}: {str(e)}"
                )
                
                # Create error result
                result = SendAttemptResult(
                    success=False,
                    user_id=user_id,
                    bot_username=bot_username,
                    bot_key=bot_key,
                    error=f"{type(e).__name__}: {str(e)}",
                    is_permanent=is_perm,
                    retry_count=retry_count,
                    timestamp=timestamp
                )
                
                # Log to database if available
                if db:
                    try:
                        await _log_send_attempt(db, result, message_text)
                    except Exception as log_error:
                        logger.warning(f"[SendUtils] Failed to log send attempt: {log_error}")
                
                # If permanent error, immediately try fallback (no retry on this bot)
                if is_perm:
                    logger.info(
                        f"[SendUtils] Permanent error detected, skipping retries "
                        f"and moving to next fallback bot"
                    )
                    break
                
                # If transient error, retry with exponential backoff
                if retry_count < max_retries:
                    delay = min(retry_base_delay * (2 ** retry_count), max_retry_delay)
                    logger.info(
                        f"[SendUtils] Retrying in {delay}s "
                        f"(attempt {retry_count + 2}/{max_retries + 1})"
                    )
                    await asyncio.sleep(delay)
                    retry_count += 1
                else:
                    # Max retries reached for transient error, try fallback
                    logger.warning(
                        f"[SendUtils] Max retries ({max_retries}) reached for transient error, "
                        f"moving to next fallback bot"
                    )
                    break
    
    # All bots failed
    logger.error(
        f"[SendUtils] ✗ ALL BOTS FAILED - user_id={user_id}, "
        f"tried {len(all_bots)} bot(s)"
    )
    
    # Return the last failed result
    return SendAttemptResult(
        success=False,
        user_id=user_id,
        bot_username="none",
        bot_key="none",
        error="All bots failed to deliver message",
        is_permanent=True,
        retry_count=0,
        timestamp=datetime.now(timezone.utc)
    )


async def _log_send_attempt(db, result: SendAttemptResult, message_text: str):
    """
    Log send attempt to database.
    
    This stores detailed metadata about each send attempt for debugging
    and monitoring purposes.
    
    Args:
        db: Database instance
        result: SendAttemptResult to log
        message_text: Full message text
    """
    try:
        # Check if database has log_send_attempt method
        if hasattr(db, 'log_send_attempt'):
            await db.log_send_attempt(
                user_id=result.user_id,
                bot_key=result.bot_key,
                bot_username=result.bot_username,
                success=result.success,
                error=result.error,
                is_permanent=result.is_permanent,
                retry_count=result.retry_count,
                message_preview=message_text[:100],
                timestamp=result.timestamp
            )
        else:
            # Fallback: log to file if database method doesn't exist
            await _log_to_file(result, message_text)
    except Exception as e:
        logger.warning(f"[SendUtils] Failed to log send attempt: {e}")


async def _log_to_file(result: SendAttemptResult, message_text: str):
    """
    Fallback logging to file when database logging is not available.
    
    Args:
        result: SendAttemptResult to log
        message_text: Full message text
    """
    import json
    import os
    
    log_dir = "/tmp/send_attempts"
    os.makedirs(log_dir, exist_ok=True)
    
    log_file = os.path.join(log_dir, "send_attempts.jsonl")
    
    log_entry = {
        **result.to_dict(),
        "message_preview": message_text[:100]
    }
    
    # Append to JSONL file
    with open(log_file, "a") as f:
        f.write(json.dumps(log_entry) + "\n")
