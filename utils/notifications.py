"""
Notification service for sending product notifications to users.
Implements rate limiting to respect Telegram's anti-spam policies.
"""
import logging
import asyncio
import json
import re
import random
import time
from typing import List, Optional, Dict, Any
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError, Forbidden, BadRequest, TimedOut, NetworkError, RetryAfter

from database import Database
from utils.categories import get_category_display_name, get_subcategory_display_name, NOTIFICATION_EXCLUDED_CATEGORIES
from translations.translator import translate_text_async
from configs.config import Config
from utils.bot_key import get_bot_env_key, get_bot_key_display
from utils import broadcast_forwarder
from utils import rate_limiter
# Redis pause manager removed - using database-based pause management
from database import (
    get_pause_state_db, 
    get_remaining_pause_seconds_db, 
    is_paused_db, 
    set_pause_state_db,
    clear_pause_state_db
)

logger = logging.getLogger(__name__)

# Rate limiting configuration
NOTIFICATION_TASKS_KEY = "notification_tasks"
MAX_NOTIFICATIONS_PER_HOUR = 5  # Maximum notifications per user per hour
NOTIFICATION_BATCH_SIZE = 10  # Send notifications in batches
BATCH_DELAY_SECONDS = 3  # Delay between batches to avoid hitting rate limits

# Staggered delivery configuration
ENABLE_STAGGERED_DELIVERY = True  # Enable staggered delivery to avoid spam flags
STAGGER_GROUP_SIZE = 10  # Number of users per group for staggered delivery
STAGGER_INTERVAL_SECONDS = 10  # Delay in seconds between groups (reduced from 30 to improve responsiveness)

# Custom message rate limiting
MAX_CUSTOM_MESSAGES_PER_HOUR = 3  # Maximum custom messages per user per hour
CUSTOM_MESSAGE_BATCH_SIZE = 5  # Send custom messages in smaller batches
CUSTOM_MESSAGE_DELAY_SECONDS = 5  # Longer delay for custom messages

# Concurrent notification sending
NOTIFICATION_CONCURRENCY = 20  # Maximum number of concurrent message sends


def _mask_token(token: str) -> str:
    """
    Mask a bot token for safe logging.
    Shows first 8 and last 8 characters.
    
    Args:
        token: Bot token to mask
    
    Returns:
        Masked token string
    """
    if not token or len(token) < 16:
        return "***"
    return f"{token[:8]}...{token[-8:]}"


async def send_message_with_rate_handling(
    bot,
    chat_id: int,
    text: str = None,
    parse_mode: str = None,
    reply_markup = None,
    photo: str = None,
    video: str = None,
    document: str = None,
    animation: str = None,
    audio: str = None,
    caption: str = None,
    bot_key: str = None,
    max_retries: int = None,
    base_backoff: float = None
) -> Dict[str, Any]:
    """
    Send a Telegram message with rate-limit handling and automatic retry.
    
    Handles:
    - RetryAfter exceptions with server-provided retry_after delay
    - TimedOut and NetworkError with exponential backoff
    - Shared pause state via database so all workers respect RetryAfter windows
    
    Args:
        bot: Telegram bot instance
        chat_id: Chat ID to send to
        text: Message text (for text messages)
        parse_mode: Parse mode (e.g., "Markdown", "HTML")
        reply_markup: Reply markup (keyboard)
        photo/video/document/animation/audio: Media file_id
        caption: Caption for media
        bot_key: Bot identifier for pause state (defaults to get_bot_env_key())
        max_retries: Max retry attempts (defaults to Config.BROADCAST_MAX_SEND_RETRIES)
        base_backoff: Base backoff seconds (defaults to Config.BROADCAST_BASE_BACKOFF_SECONDS)
    
    Returns:
        dict with {"success": bool, "error": Optional[str], "retry_after": Optional[float]}
    """
    if bot_key is None:
        bot_key = get_bot_env_key()
    
    if max_retries is None:
        max_retries = Config.BROADCAST_MAX_SEND_RETRIES
    
    if base_backoff is None:
        base_backoff = Config.BROADCAST_BASE_BACKOFF_SECONDS
    
    # Mask token for logging
    token = getattr(bot, 'token', '')
    token_display = _mask_token(token)
    
    # Determine message type
    media_type = None
    media_id = None
    if photo:
        media_type, media_id = "photo", photo
    elif video:
        media_type, media_id = "video", video
    elif document:
        media_type, media_id = "document", document
    elif animation:
        media_type, media_id = "animation", animation
    elif audio:
        media_type, media_id = "audio", audio
    
    retry_count = 0
    last_error = None
    last_retry_after = None
    
    # Get bot username for Redis pause state
    bot_username = getattr(bot, 'username', None)
    
    # Get exponential backoff config
    max_backoff = getattr(Config, 'MAX_BACKOFF_SECONDS', 300.0)
    jitter_factor = getattr(Config, 'BACKOFF_JITTER_FACTOR', 0.1)
    
    while retry_count <= max_retries:
        try:
            # Check if bot is paused in database (shared pause state)
            if bot_username:
                remaining_pause = await get_remaining_pause_seconds_db(Config.DB_PATH, bot_username)
                if remaining_pause > 0:
                    logger.info(f"[RateHandler] Bot @{bot_username} (token={token_display}) paused for {remaining_pause:.1f}s, waiting...")
                    await asyncio.sleep(remaining_pause)
            else:
                # Fallback to database pause if no bot username
                remaining_pause = await rate_limiter.get_remaining_pause_seconds(bot_key)
                if remaining_pause > 0:
                    logger.info(f"[RateHandler] Bot {bot_key} (token={token_display}) paused for {remaining_pause:.1f}s, waiting...")
                    await asyncio.sleep(remaining_pause)
            
            # Send the message
            if media_type:
                # Send media message
                send_method = getattr(bot, f'send_{media_type}')
                await send_method(
                    chat_id=chat_id,
                    **{media_type: media_id},
                    caption=caption or text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup
                )
            else:
                # Send text message
                await bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=parse_mode,
                    reply_markup=reply_markup
                )
            
            return {"success": True, "error": None, "retry_after": None}
        
        except (Forbidden, BadRequest) as e:
            # Permanent errors - don't retry
            # NOTE: BadRequest inherits from NetworkError, so catch it first
            error_msg = str(e)
            logger.debug(f"[RateHandler] Permanent error for chat {chat_id}: {type(e).__name__}: {error_msg}")
            return {"success": False, "error": error_msg, "retry_after": None}
        
        except RetryAfter as e:
            retry_after = e.retry_after if hasattr(e, 'retry_after') else 30
            last_retry_after = retry_after
            
            logger.warning(
                f"[RateHandler] RetryAfter for chat {chat_id} via bot {bot_key} (token={token_display}): "
                f"retry_after={retry_after}s"
            )
            
            # Set shared pause state in database so all workers wait
            if bot_username:
                try:
                    await set_pause_state_db(
                        db_path=Config.DB_PATH,
                        bot_key=bot_username,
                        retry_after_seconds=retry_after,
                        buffer_seconds=getattr(Config, 'PAUSE_BUFFER_SECONDS', 5.0)
                    )
                except Exception as pause_error:
                    logger.error(f"[RateHandler] Failed to set pause in database: {pause_error}")
                    # Fallback to rate_limiter pause
                    jitter = random.uniform(0, min(retry_after * 0.1, 5.0))
                    pause_duration = retry_after + jitter
                    pause_until = time.time() + pause_duration
                    await rate_limiter.set_paused_until(bot_key, pause_until)
            else:
                # No bot username, use database pause
                jitter = random.uniform(0, min(retry_after * 0.1, 5.0))
                pause_duration = retry_after + jitter
                pause_until = time.time() + pause_duration
                await rate_limiter.set_paused_until(bot_key, pause_until)
            
            if retry_count < max_retries:
                # Wait for retry_after + buffer + jitter
                buffer_seconds = getattr(Config, 'PAUSE_BUFFER_SECONDS', 5.0)
                jitter = random.uniform(0, retry_after * jitter_factor)
                delay = retry_after + buffer_seconds + jitter
                await asyncio.sleep(delay)
                retry_count += 1
                last_error = f"RetryAfter({retry_after}s)"
            else:
                logger.error(
                    f"[RateHandler] Max retries ({max_retries}) reached after RetryAfter for chat {chat_id} "
                    f"via bot {bot_key} (token={token_display})"
                )
                return {"success": False, "error": f"RetryAfter({retry_after}s)", "retry_after": retry_after}
        
        except (TimedOut, NetworkError) as e:
            error_type = type(e).__name__
            if retry_count < max_retries:
                # Exponential backoff with jitter and cap
                base_delay = base_backoff * (2 ** retry_count)
                jitter = random.uniform(0, base_delay * jitter_factor)
                delay = min(base_delay + jitter, max_backoff)
                logger.warning(
                    f"[RateHandler] {error_type} for chat {chat_id} via bot {bot_key} (token={token_display}), "
                    f"retrying in {delay:.1f}s (attempt {retry_count + 1}/{max_retries})"
                )
                await asyncio.sleep(delay)
                retry_count += 1
                last_error = error_type
            else:
                logger.error(
                    f"[RateHandler] Max retries ({max_retries}) reached after {error_type} for chat {chat_id} "
                    f"via bot {bot_key} (token={token_display})"
                )
                return {"success": False, "error": error_type, "retry_after": None}
        
        except TelegramError as e:
            error_msg = str(e)
            # Check for rate limit errors in generic TelegramError messages
            if "Too Many Requests" in error_msg or "retry after" in error_msg.lower() or "429" in error_msg:
                # Try to extract retry_after from error message
                retry_after_extracted = None
                try:
                    # Try to parse "retry after X seconds" pattern
                    import re
                    match = re.search(r'retry after (\d+)', error_msg, re.IGNORECASE)
                    if match:
                        retry_after_extracted = int(match.group(1))
                    else:
                        # Default to configured value
                        retry_after_extracted = getattr(Config, 'DEFAULT_RETRY_AFTER_SECONDS', 30.0)
                except Exception:
                    retry_after_extracted = getattr(Config, 'DEFAULT_RETRY_AFTER_SECONDS', 30.0)
                
                last_retry_after = retry_after_extracted
                
                # Set pause in database
                if bot_username and retry_after_extracted:
                    try:
                        await set_pause_state_db(
                            db_path=Config.DB_PATH,
                            bot_key=bot_username,
                            retry_after_seconds=retry_after_extracted,
                            buffer_seconds=getattr(Config, 'PAUSE_BUFFER_SECONDS', 5.0)
                        )
                    except Exception as pause_error:
                        logger.error(f"[RateHandler] Failed to set pause in database: {pause_error}")
                
                # Treat as transient rate limit error with retry
                if retry_count < max_retries:
                    buffer_seconds = getattr(Config, 'PAUSE_BUFFER_SECONDS', 5.0)
                    jitter = random.uniform(0, retry_after_extracted * jitter_factor)
                    delay = retry_after_extracted + buffer_seconds + jitter
                    logger.warning(
                        f"[RateHandler] Rate limit (TelegramError) for chat {chat_id} via bot {bot_key} (token={token_display}), "
                        f"retrying in {delay:.1f}s (attempt {retry_count + 1}/{max_retries})"
                    )
                    await asyncio.sleep(delay)
                    retry_count += 1
                    last_error = "rate_limited"
                else:
                    logger.error(f"[RateHandler] Max retries ({max_retries}) reached after rate limit for chat {chat_id} via bot {bot_key} (token={token_display})")
                    return {"success": False, "error": "rate_limit_max_retries", "retry_after": retry_after_extracted}
            else:
                # Other Telegram errors
                logger.error(f"[RateHandler] TelegramError for chat {chat_id} via bot {bot_key} (token={token_display}): {error_msg}")
                return {"success": False, "error": error_msg, "retry_after": None}
        
        except Exception as e:
            error_msg = str(e)
            logger.exception(f"[RateHandler] Unexpected exception for chat {chat_id}: {error_msg}")
            return {"success": False, "error": f"Exception: {error_msg}", "retry_after": None}
    
    # If we exit the loop, all retries were exhausted
    logger.error(f"[RateHandler] All retries exhausted for chat {chat_id} via bot {bot_key} (token={token_display})")
    return {"success": False, "error": f"Max retries reached: {last_error}", "retry_after": last_retry_after}
    
    # Should not reach here, but handle edge case
    return {"success": False, "error": last_error or "Unknown", "retry_after": None}


class NotificationService:
    """Service for managing and sending product notifications."""
    
    # Compiled regex pattern for matching t.me deep-link URLs
    # Matches: https://t.me/{valid_telegram_username}?start={anything}
    # Telegram usernames start with a letter and contain only letters, digits, and underscores
    _TELEGRAM_DEEPLINK_PATTERN = re.compile(r'https://t\.me/[a-zA-Z][a-zA-Z0-9_]*(\?start=[^"\s]*)')
    
    def __init__(self, db: Database):
        self.db = db
    
    @staticmethod
    def _is_chat_not_found(error_msg: str) -> bool:
        """
        Check if a Telegram error message indicates chat not found.
        
        This should ONLY match permanent errors where the chat is truly inaccessible,
        not temporary issues or other errors.
        
        Confirmed permanent errors:
        - "Chat not found" - chat was deleted or never existed
        - "User not found" - user account deleted
        - "Forbidden: bot was blocked by the user" - handled separately as Forbidden
        - "Forbidden: user is deactivated" - user deactivated their account
        
        Args:
            error_msg: The error message from the Telegram exception
            
        Returns:
            True if the error indicates PERMANENT chat/user inaccessibility, False otherwise
        """
        error_lower = error_msg.lower()
        
        # Only match specific permanent error patterns
        permanent_patterns = [
            "chat not found",
            "user not found", 
            "user is deactivated",
            "chat_not_found"  # Alternative format
        ]
        
        return any(pattern in error_lower for pattern in permanent_patterns)
    
    def _get_task_storage(self, context: ContextTypes.DEFAULT_TYPE) -> list:
        """
        Get the task storage list from application.bot_data for resilient task tracking.
        Falls back to context.notification_tasks for backwards compatibility.
        
        In webhook deployments, context is ephemeral and tasks stored in it can be
        garbage-collected. Using application.bot_data ensures tasks persist.
        
        Args:
            context: Telegram context
            
        Returns:
            List for storing background task references
        """
        try:
            # Try to use application.bot_data (preferred - persists in webhook mode)
            if hasattr(context, 'application') and context.application is not None:
                bot_data = context.application.bot_data
                if NOTIFICATION_TASKS_KEY not in bot_data:
                    bot_data[NOTIFICATION_TASKS_KEY] = []
                logger.debug("[TaskStorage] Using application.bot_data for task storage (resilient)")
                return bot_data[NOTIFICATION_TASKS_KEY]
        except Exception as e:
            logger.warning(f"[TaskStorage] Could not access application.bot_data: {e}")
        
        # Fallback to context.notification_tasks (less resilient but backwards compatible)
        if not hasattr(context, NOTIFICATION_TASKS_KEY):
            setattr(context, NOTIFICATION_TASKS_KEY, [])
        logger.warning("[TaskStorage] Falling back to context.notification_tasks (ephemeral - may be lost in webhook mode)")
        return getattr(context, NOTIFICATION_TASKS_KEY)
    
    def _store_task(self, context: ContextTypes.DEFAULT_TYPE, task: asyncio.Task, task_name: str = "notification"):
        """
        Store a background task reference to prevent garbage collection.
        Adds exception handling callback and stores in application.bot_data.
        
        Args:
            context: Telegram context
            task: The async task to store
            task_name: Human-readable task name for logging
        """
        task_storage = self._get_task_storage(context)
        
        # Add exception handling callback
        def log_task_exception(task_obj):
            try:
                task_obj.result()
                logger.info(f"[TaskStorage] {task_name} task completed successfully (id={id(task_obj)})")
            except Exception as e:
                logger.error(f"[TaskStorage] Error in {task_name} task (id={id(task_obj)}): {e}", exc_info=True)
            finally:
                # Clean up completed task from storage
                try:
                    if task_obj in task_storage:
                        task_storage.remove(task_obj)
                        logger.debug(f"[TaskStorage] Removed completed task from storage (id={id(task_obj)})")
                except Exception as cleanup_error:
                    logger.warning(f"[TaskStorage] Error removing task from storage: {cleanup_error}")
        
        task.add_done_callback(log_task_exception)
        task_storage.append(task)
        
        logger.info(f"[TaskStorage] Stored {task_name} task (id={id(task)}, total_tasks={len(task_storage)})")
    
    def _is_primary_instance(self, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """
        Check if this is the primary bot instance.
        Only the primary bot (first token in BOT_TOKENS) should process notifications.
        
        Args:
            context: Telegram context with bot information
            
        Returns:
            True if this is the primary instance, False otherwise
        """
        if not hasattr(context, 'bot') or not hasattr(context.bot, 'token'):
            # If we can't determine, assume it's primary to maintain backward compatibility
            logger.warning("Unable to determine bot instance, assuming primary")
            return True
        
        current_token = context.bot.token
        bot_tokens = Config.BOT_TOKENS
        
        # Validate BOT_TOKENS is a non-empty list
        if not bot_tokens or not isinstance(bot_tokens, list) or len(bot_tokens) == 0:
            # No tokens configured or invalid configuration, assume primary
            logger.warning("BOT_TOKENS is empty or invalid, assuming primary instance")
            return True
        
        # Fallback: if only one bot token configured, always return True
        if len(bot_tokens) == 1:
            logger.info(
                f"Primary instance check: PASS (only one bot configured: "
                f"...{current_token[-5:]})"
            )
            return True
        
        # Validate that current token exists in BOT_TOKENS list
        if current_token not in bot_tokens:
            logger.warning(
                f"Primary instance check: Current bot token (...{current_token[-5:]}) "
                f"not found in BOT_TOKENS list. This indicates a configuration error. "
                f"Configured tokens: {len(bot_tokens)}. Assuming primary to maintain functionality."
            )
            return True
        
        # Primary bot is the first token in the list (index 0)
        is_primary = current_token == bot_tokens[0]
        
        if is_primary:
            logger.info(
                f"Primary instance check: PASS (current: ...{current_token[-5:]}, "
                f"primary: ...{bot_tokens[0][-5:]})"
            )
        else:
            logger.warning(
                f"Primary instance check: FAIL - Notifications will not be sent. "
                f"Current bot token: ...{current_token[-5:]}, "
                f"Expected primary: ...{bot_tokens[0][-5:]}, "
                f"Total bots configured: {len(bot_tokens)}"
            )
        
        return is_primary
    
    async def notify_users_about_product(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        product_id: int
    ):
        """
        Notify subscribed users about a new categorized product.
        Implements rate limiting to prevent spam.
        Can be called from any bot instance - database-level deduplication prevents duplicates.
        """
        try:
            # Get bot username for logging
            bot_username = context.bot.username if hasattr(context.bot, 'username') else 'unknown'
            logger.info(f"[Notifications] notify_users_about_product: Bot @{bot_username} processing product {product_id}")
            
            # Get product details
            product = await self.db.get_product(product_id)
            if not product:
                logger.warning(f"[Notifications] Product {product_id} not found for notification")
                return
            
            # Only notify if product is categorized
            if not product.get("category"):
                logger.debug(f"[Notifications] Product {product_id} not categorized, skipping notification")
                return
            
            # Skip notifications for excluded categories (DATEDPROOFS, CLIENTTOUCHDOWNS)
            # Note: ANNOUNCEMENTS is NOT excluded from notifications
            category = product.get("category")
            if category in NOTIFICATION_EXCLUDED_CATEGORIES:
                logger.info(f"[Notifications] Product {product_id} is in excluded category '{category}', skipping notification")
                return
            
            logger.info(f"[NOTIFY] START: Processing product {product_id}, category={category}")
            
            # Get all subscribed users
            subscribed_users = await self.db.get_subscribed_users()
            subscribed_count = len(subscribed_users) if subscribed_users else 0
            
            logger.info(f"[Notifications] Found {subscribed_count} subscribed users for product {product_id}")
            
            if not subscribed_users:
                logger.info(f"[Notifications] No subscribed users to notify about product {product_id}")
                return
            
            # Get user preferences (last_bot_key) for routing notifications to correct bots
            # This ensures each user gets notification from their preferred bot
            users_with_targets = []
            current_bot_key = get_bot_env_key()
            
            # Fetch all users to get their last_bot_key
            all_users_data = await self.db.get_all_users()
            user_bot_keys = {u['user_id']: u.get('last_bot_key') for u in all_users_data}
            
            for user_id in subscribed_users:
                # Use user's preferred bot (last_bot_key), fallback to current bot
                target_bot_key = user_bot_keys.get(user_id) or current_bot_key
                users_with_targets.append((user_id, target_bot_key))
            
            # Queue notifications for all subscribed users with individual bot targeting
            # Database-level deduplication prevents duplicate notifications
            queued_count = await self.db.queue_notifications_batch_with_targets(
                users_with_targets, 
                product_id
            )
            
            logger.info(
                f"[Notifications] Queued {queued_count} notifications for product {product_id} "
                f"across {len(set(tk for _, tk in users_with_targets))} different bots"
            )
            logger.info(f"[NOTIFY] Queued count: {queued_count}/{subscribed_count} notifications")
            
            # Warn if significantly fewer notifications were queued than expected
            if queued_count < len(subscribed_users) * 0.5:  # Less than 50% queued
                logger.warning(
                    f"[Notifications] Only {queued_count}/{len(subscribed_users)} notifications queued "
                    f"for product {product_id} - possible duplicate filtering or recent notifications"
                )
            
            # Process the notification queue asynchronously (non-blocking)
            # Store task in application.bot_data for resilience in webhook deployments
            task = asyncio.create_task(self.process_notification_queue(context))
            self._store_task(context, task, f"notify_product_{product_id}")
            
            logger.info(f"[Notifications] Background task created and stored for product {product_id}")
            logger.info(f"[NOTIFY] Background task started for product {product_id} (task_id={id(task)})")
            
        except Exception as e:
            logger.error(f"Error notifying users about product {product_id}: {e}", exc_info=True)
    
    async def process_notification_queue(self, context: ContextTypes.DEFAULT_TYPE):
        """
        Process pending notifications from the queue with rate limiting and staggered delivery.
        Groups users and sends notifications at staggered intervals to avoid spam flags.
        
        In multi-bot setups:
        - Primary bot queues notifications (to avoid duplicates)
        - ALL bots process their own users' notifications from the queue
        - Each bot filters by bot_username to only send to its own users
        """
        try:
            # Get bot username for filtering (skip primary check for processing)
            bot_username = context.bot.username if hasattr(context.bot, 'username') else None
            current_bot_key = get_bot_env_key()
            bot_display = get_bot_key_display(current_bot_key, bot_username)
            logger.info(f"[Notifications] process_notification_queue: Starting for {bot_display}")
            logger.info(f"[NOTIFY] Processing queue - START")
            
            pending = await self.db.get_pending_notifications(limit=100)
            
            logger.info(f"[Notifications] Found {len(pending)} total pending notifications before filtering")
            logger.info(f"[NOTIFY] Pending notifications count (before filter): {len(pending)}")
            
            if not pending:
                logger.debug(f"[Notifications] No pending notifications to process")
                return
            
            # Filter notifications for this bot using target_bot_key
            original_count = len(pending)
            
            # Pre-fetch bot users for backward compatibility filtering
            bot_user_set = set(await self.db.get_users_by_bot(bot_username)) if bot_username else set()
            
            # Filter to messages targeted to this bot OR with NULL target_bot_key (backward compatibility)
            filtered_pending = []
            for n in pending:
                target_key = n.get("target_bot_key")
                
                if target_key == current_bot_key:
                    # Explicitly targeted to this bot
                    filtered_pending.append(n)
                elif target_key is None:
                    # NULL target_bot_key - fall back to bot_username filtering for backward compatibility
                    if bot_username:
                        if n["user_id"] in bot_user_set:
                            filtered_pending.append(n)
                    else:
                        # No bot_username to filter by, include it
                        filtered_pending.append(n)
            
            pending = filtered_pending
            filtered_out = original_count - len(pending)
            logger.info(f"[Notifications] Filtered out {filtered_out} notifications not for this bot, {len(pending)} remaining")
            
            if not pending:
                logger.debug(f"[Notifications] No pending notifications for {bot_display} after filtering")
                return
            
            logger.info(f"[Notifications] Processing {len(pending)} pending notifications for {bot_display}")
            
            # Group notifications by user for rate limiting
            user_notifications = {}
            for notification in pending:
                user_id = notification["user_id"]
                if user_id not in user_notifications:
                    user_notifications[user_id] = []
                user_notifications[user_id].append(notification)
            
            # Filter users who haven't exceeded rate limits
            eligible_users = []
            for user_id, notifications in user_notifications.items():
                # Check rate limit for this user
                recent_count = await self.db.get_recent_notifications_count(user_id, minutes=60)
                
                if recent_count >= MAX_NOTIFICATIONS_PER_HOUR:
                    logger.debug(f"[Notifications] User {user_id} reached rate limit ({recent_count}/{MAX_NOTIFICATIONS_PER_HOUR}), skipping")
                    continue
                
                # Calculate how many more notifications we can send to this user
                remaining = MAX_NOTIFICATIONS_PER_HOUR - recent_count
                
                # Add user with their limited notifications
                eligible_users.append((user_id, notifications[:remaining]))
            
            if not eligible_users:
                logger.info(f"[Notifications] No eligible users after rate limiting")
                return
            
            logger.info(f"[Notifications] {len(eligible_users)} users eligible after rate limiting")
            
            # Implement staggered delivery by groups
            sent_count = 0
            failed_count = 0
            
            if ENABLE_STAGGERED_DELIVERY and len(eligible_users) >= STAGGER_GROUP_SIZE:
                logger.info(f"[Notifications] Using staggered delivery: {len(eligible_users)} users in groups of {STAGGER_GROUP_SIZE}")
                
                # Split users into groups
                user_groups = []
                for i in range(0, len(eligible_users), STAGGER_GROUP_SIZE):
                    user_groups.append(eligible_users[i:i + STAGGER_GROUP_SIZE])
                
                logger.info(f"[Notifications] Created {len(user_groups)} groups for staggered delivery")
                
                # Process each group with staggered delay
                for group_idx, user_group in enumerate(user_groups):
                    logger.info(f"[Notifications] Processing group {group_idx + 1}/{len(user_groups)} with {len(user_group)} users")
                    
                    # Collect all notifications for this group
                    group_batch = []
                    for user_id, notifications in user_group:
                        group_batch.extend(notifications)
                    
                    # Send notifications in smaller batches within the group
                    for i in range(0, len(group_batch), NOTIFICATION_BATCH_SIZE):
                        batch = group_batch[i:i + NOTIFICATION_BATCH_SIZE]
                        sent = await self._send_notification_batch(context, batch)
                        sent_count += sent
                        failed_count += len(batch) - sent
                        
                        # Small delay between batches within a group
                        if i + NOTIFICATION_BATCH_SIZE < len(group_batch):
                            await asyncio.sleep(BATCH_DELAY_SECONDS)
                    
                    # Stagger delay before next group (except for last group)
                    if group_idx < len(user_groups) - 1:
                        logger.info(f"[Notifications] Waiting {STAGGER_INTERVAL_SECONDS}s before next group...")
                        await asyncio.sleep(STAGGER_INTERVAL_SECONDS)
            else:
                # Standard batch processing without staggering
                logger.info(f"[Notifications] Using standard batch processing (staggering disabled or small user count)")
                batch = []
                
                for user_id, notifications in eligible_users:
                    batch.extend(notifications)
                    
                    # Send in batches to avoid overwhelming Telegram
                    if len(batch) >= NOTIFICATION_BATCH_SIZE:
                        sent = await self._send_notification_batch(context, batch)
                        sent_count += sent
                        failed_count += len(batch) - sent
                        batch = []
                        await asyncio.sleep(BATCH_DELAY_SECONDS)
                
                # Send remaining batch
                if batch:
                    sent = await self._send_notification_batch(context, batch)
                    sent_count += sent
                    failed_count += len(batch) - sent
            
            logger.info(f"[Notifications] Completed: {sent_count} sent successfully, {failed_count} failed (bot @{bot_username})")
            logger.info(f"[NOTIFY] Processing queue - COMPLETE (sent={sent_count}, failed={failed_count})")
            
        except Exception as e:
            logger.error(f"Error processing notification queue: {e}", exc_info=True)
            logger.error(f"[NOTIFY] Processing queue - ERROR: {e}")
    
    async def _send_notification_batch(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        notifications: List[dict]
    ) -> int:
        """
        Send a batch of notifications.
        Returns the number of successfully sent notifications.
        Optimized to prefetch product and user data to reduce DB calls.
        """
        sent_count = 0
        
        # Extract unique product IDs and user IDs in a single pass
        product_ids = set()
        user_ids = set()
        for n in notifications:
            product_ids.add(n["product_id"])
            user_ids.add(n["user_id"])
        
        # Prefetch product details concurrently using asyncio.gather
        products_cache = {}
        if product_ids:
            product_results = await asyncio.gather(
                *[self.db.get_product(pid) for pid in product_ids],
                return_exceptions=True
            )
            for pid, product in zip(product_ids, product_results):
                if not isinstance(product, Exception) and product:
                    products_cache[pid] = product
        
        # Prefetch user languages concurrently using asyncio.gather
        user_langs_cache = {}
        if user_ids:
            lang_results = await asyncio.gather(
                *[self.db.get_user_language(uid) for uid in user_ids],
                return_exceptions=True
            )
            for uid, lang in zip(user_ids, lang_results):
                if not isinstance(lang, Exception):
                    user_langs_cache[uid] = lang if lang else "en"
        
        logger.debug(f"[Notifications] Prefetched {len(products_cache)} products and {len(user_langs_cache)} user languages for batch")
        
        for notification in notifications:
            try:
                user_id = notification["user_id"]
                product_id = notification["product_id"]
                notification_id = notification["id"]
                
                # Check if user is invalid for this bot before attempting to send
                current_bot_key = get_bot_env_key()
                is_invalid = await self.db.is_user_invalid_for_bot(user_id, current_bot_key)
                if is_invalid:
                    logger.debug(f"Skipping notification for user {user_id} - marked invalid for bot {current_bot_key}")
                    await self.db.mark_notification_sent(notification_id)
                    continue
                
                # Get product from cache
                product = products_cache.get(product_id)
                
                if not product:
                    # Product deleted, mark notification as sent
                    await self.db.mark_notification_sent(notification_id)
                    continue
                
                # Get user's language preference from cache
                user_lang = user_langs_cache.get(user_id, "en")
                
                # Prepare notification message
                category = product.get("category", "Unknown")
                subcategory = product.get("subcategory")
                
                category_text = get_category_display_name(category)
                if subcategory:
                    translated_subcategory = get_subcategory_display_name(subcategory, user_lang)
                    category_text += f" • {translated_subcategory}"
                
                caption = product.get("caption", "")
                
                # Translate caption if user language is not English
                if user_lang and user_lang not in ["en", "en-US"] and caption:
                    try:
                        caption = await translate_text_async(caption, user_lang)
                    except Exception as e:
                        logger.error(f"Error translating caption for notification: {e}")
                        # Keep original caption if translation fails
                
                caption_preview = caption[:100] + "..." if len(caption) > 100 else caption
                
                message_text = (
                    f"🆕 **New Product Available!**\n\n"
                    f"📂 Category: {category_text}\n"
                    f"📝 {caption_preview}\n\n"
                    f"Use /menu to browse the catalog!"
                )
                
                # Create inline keyboard with view product button
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("👁️ View Product", callback_data=f"product|{product_id}")],
                    [InlineKeyboardButton("📋 View Catalog", callback_data="categories")],
                    [InlineKeyboardButton("🔕 Unsubscribe", callback_data="unsubscribe_notifications")]
                ])
                
                # Send notification
                await context.bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
                
                # Mark as sent
                await self.db.mark_notification_sent(notification_id)
                sent_count += 1
                
                logger.debug(f"Sent notification to user {user_id} for product {product_id}")
                
            except Forbidden:
                # User blocked the bot, disable notifications
                logger.info(f"User {user_id} blocked the bot (Forbidden), disabling notifications")
                await self.db.set_user_notifications(user_id, False)
                await self.db.mark_notification_sent(notification_id)
            
            except BadRequest as e:
                error_msg = str(e)
                # Check for chat not found errors
                if self._is_chat_not_found(error_msg):
                    # Get current bot key for bot-specific invalidation
                    current_bot_key = get_bot_env_key()
                    logger.info(f"User {user_id} chat not found for bot {current_bot_key} (BadRequest: {error_msg}), marking as invalid for this bot only")
                    # Mark invalid ONLY for this specific bot, not globally
                    await self.db.mark_user_invalid(user_id, bot_id=current_bot_key, reason="chat_not_found")
                    await self.db.mark_notification_sent(notification_id)
                else:
                    # Other BadRequest errors - DO NOT block the user
                    logger.warning(f"BadRequest error sending notification to {user_id}: {error_msg} - NOT blocking user")
                    await self.db.mark_notification_sent(notification_id)
                
            except TelegramError as e:
                logger.error(f"Telegram error sending notification to {user_id}: {e}")
                # Don't mark as sent, will retry later
                
            except Exception as e:
                logger.error(f"Error sending notification to {user_id}: {e}")
                # Mark as sent to avoid infinite retries
                await self.db.mark_notification_sent(notification_id)
        
        return sent_count
    
    async def send_custom_message_to_user(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        user_id: int,
        message_text: str = None
    ) -> bool:
        """
        Send a custom text message to a specific user via broadcast forwarder.
        
        This now uses the broadcast forwarder pattern so that ALL bot instances
        that the user has started will deliver the message. This ensures the user
        receives the message regardless of which bot they're currently using.
        
        Text-only - media is not supported for queued/broadcast messages.
        
        Returns:
            bool: True if message was published successfully, False otherwise
        """
        try:
            logger.info(f"[Notifications] send_custom_message_to_user: Publishing message for user {user_id} via broadcast forwarder")
            
            # Check if user is blocked
            is_blocked = await self.db.is_user_blocked(user_id)
            if is_blocked:
                logger.warning(f"[Notifications] Cannot send message to blocked user {user_id}")
                return False
            
            # Use broadcast forwarder to ensure all bot instances deliver to this user
            # Create payload with target_user_id so each bot knows this is a single-user send
            payload = {"target_user_id": user_id}
            
            broadcast_id = await broadcast_forwarder.publish_broadcast(
                self.db, 
                message_text, 
                payload=payload
            )
            
            logger.info(f"[Notifications] Published single-user message as broadcast {broadcast_id} for user {user_id}")
            
            return True
            
        except Exception as e:
            logger.error(f"Error sending custom message to user {user_id}: {e}", exc_info=True)
            return False
    
    def _create_empty_result_task(self) -> asyncio.Task:
        """
        Create an asyncio task that returns an empty result.
        Helper to avoid code duplication.
        """
        async def empty_task():
            return {"sent_count": 0, "failed_count": 0}
        return asyncio.create_task(empty_task())
    
    async def broadcast_custom_message(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        message_text: str = None,
        exclude_blocked: bool = True
    ) -> tuple:
        """
        Broadcast a custom text message to all users with rate limiting.
        Text-only - media is not supported for queued/broadcast messages.
        
        In multi-bot setups, queues messages with each user's preferred bot (last_bot_key)
        so the correct bot process delivers the message.
        
        Returns:
            tuple: (queued_count: int, processing_task: asyncio.Task)
        """
        try:
            current_bot_key = get_bot_env_key()
            bot_username = context.bot.username if hasattr(context.bot, 'username') else None
            bot_display = get_bot_key_display(current_bot_key, bot_username)
            
            logger.info(f"[Notifications] broadcast_custom_message: Starting broadcast from {bot_display}")
            
            # Get all users
            all_users = await self.db.get_all_users()
            
            if not all_users:
                logger.info(f"[Notifications] broadcast_custom_message: No users to broadcast to")
                # Return empty task that completes immediately
                return (0, self._create_empty_result_task())
            
            logger.info(f"[Notifications] broadcast_custom_message: Found {len(all_users)} total users")
            
            # Pre-fetch admin IDs once (optimization: avoid repeated function calls)
            from utils.helpers import get_admin_ids
            admin_ids_set = set(get_admin_ids())
            
            logger.info(f"[Notifications] Filtering broadcast: exclude_blocked={exclude_blocked}, admin_count={len(admin_ids_set)}")
            
            # Collect eligible users with their preferred bot keys
            users_with_targets = []  # List of (user_id, target_bot_key)
            blocked_count = 0
            admin_count = 0
            
            for user in all_users:
                user_id = user["user_id"]
                
                # Skip blocked users if requested
                if exclude_blocked and user.get("is_blocked", 0) == 1:
                    blocked_count += 1
                    continue
                
                # Skip admin users
                if user_id in admin_ids_set:
                    admin_count += 1
                    continue
                
                # Get user's preferred bot (last_bot_key), fallback to primary bot token if not set
                last_bot_key = user.get("last_bot_key")
                target_bot_key = last_bot_key if last_bot_key is not None else current_bot_key
                users_with_targets.append((user_id, target_bot_key))
            
            logger.info(f"[Notifications] Eligible users: {len(users_with_targets)} (filtered {blocked_count} blocked, {admin_count} admins)")
            
            if not users_with_targets:
                logger.info(f"[Notifications] No eligible users after filtering")
                # Return empty task that completes immediately
                return (0, self._create_empty_result_task())
            
            # Batch queue all messages with individual target_bot_key for routing
            queued = await self.db.queue_custom_messages_batch_with_targets(
                users_with_targets,
                message_text,
                media_type=None,
                media_file_id=None
            )
            
            # Log target_bot_key distribution for visibility
            target_distribution = {}
            for _, target_key in users_with_targets:
                target_distribution[target_key] = target_distribution.get(target_key, 0) + 1
            logger.info(f"[Notifications] Queued {queued} custom messages. Target distribution: {target_distribution}")
            logger.info(f"[NOTIFY] Broadcast queued count: {queued}/{len(users_with_targets)} messages")
            
            # Spawn background task to process the queue for users assigned to this bot instance
            # Store task in application.bot_data for resilience in webhook deployments
            task = asyncio.create_task(self.process_custom_message_queue(context, process_all_bots=False))
            self._store_task(context, task, "broadcast_custom_msg")
            
            logger.info(f"[Notifications] Background task created for broadcast (task_id={id(task)})")
            
            return (queued, task)
            
        except Exception as e:
            logger.error(f"Error broadcasting custom message: {e}", exc_info=True)
            # Return error immediately - don't wrap in a task that will fail
            raise
    
    async def process_custom_message_queue(self, context: ContextTypes.DEFAULT_TYPE, process_all_bots: bool = False) -> dict:
        """
        Process pending custom messages from the queue with rate limiting.
        
        In multi-bot setups:
        - Primary bot queues custom messages (to avoid duplicates)
        - When process_all_bots=False (default): Each bot processes only its own users' messages
        - When process_all_bots=True: Process all pending messages across all bots (for global broadcasts)
        
        Args:
            context: Telegram context
            process_all_bots: If True, process messages for all users regardless of bot.
                            If False (default), only process messages for users of this bot.
        
        Returns:
            dict: Summary with {"sent_count": int, "failed_count": int, "invalidated_count": int, 
                                 "failure_reasons": dict, "failed_user_ids": list}
        """
        # Get bot username for filtering and pause check
        bot_username = context.bot.username if hasattr(context.bot, 'username') else None
        current_bot_key = get_bot_env_key()
        bot_display = get_bot_key_display(current_bot_key, bot_username)
        
        # Check if bot is paused due to rate limiting
        if bot_username:
            is_paused = await is_paused_db(Config.DB_PATH, bot_username)
            if is_paused:
                remaining = await get_remaining_pause_seconds_db(Config.DB_PATH, bot_username)
                logger.info(
                    f"[{bot_display}] Bot is paused due to rate limiting, "
                    f"remaining: {remaining:.1f}s - skipping processing"
                )
                return {
                    "sent_count": 0,
                    "failed_count": 0,
                    "invalidated_count": 0,
                    "failure_reasons": {"paused": 1},
                    "failed_user_ids": []
                }
        
        sent_count = 0
        failed_count = 0
        invalidated_count = 0
        failure_reasons = {}
        failed_user_ids = []
        rate_limit_info = None  # Will be populated if rate limiting occurs
        failure_samples_list = []  # Collect sample failure messages
        
        try:
            pending = await self.db.get_pending_custom_messages(limit=100)
            
            if not pending:
                return {"sent_count": 0, "failed_count": 0, "invalidated_count": 0, 
                        "failure_reasons": {}, "failed_user_ids": []}
            
            # Filter messages for this bot using target_bot_key (unless process_all_bots is True)
            if not process_all_bots:
                original_count = len(pending)
                
                # Pre-fetch bot users for backward compatibility filtering
                bot_user_set = set(await self.db.get_users_by_bot(bot_username)) if bot_username else set()
                
                # Filter to messages targeted to this bot OR with NULL target_bot_key (backward compatibility)
                filtered_pending = []
                for m in pending:
                    target_key = m.get("target_bot_key")
                    
                    if target_key == current_bot_key:
                        # Explicitly targeted to this bot
                        filtered_pending.append(m)
                    elif target_key is None:
                        # NULL target_bot_key - fall back to bot_username filtering for backward compatibility
                        if bot_username:
                            if m["user_id"] in bot_user_set:
                                filtered_pending.append(m)
                        else:
                            # No bot_username to filter by, include it
                            filtered_pending.append(m)
                
                pending = filtered_pending
            
            if not pending:
                logger.debug(f"No pending custom messages for {bot_display}")
                return {"sent_count": 0, "failed_count": 0, "invalidated_count": 0,
                        "failure_reasons": {}, "failed_user_ids": []}
            
            mode_desc = "all bots" if process_all_bots else bot_display
            logger.info(f"[{mode_desc}] Processing {len(pending)} pending custom messages")
            
            # Group messages by user for rate limiting
            user_messages = {}
            for message in pending:
                user_id = message["user_id"]
                if user_id not in user_messages:
                    user_messages[user_id] = []
                user_messages[user_id].append(message)
            
            # Process messages in batches
            batch = []
            
            for user_id, messages in user_messages.items():
                # Check if user is blocked
                is_blocked = await self.db.is_user_blocked(user_id)
                if is_blocked:
                    # Log invalidation record details for debugging
                    # First try to get bot-specific invalidation, then fall back to any active invalidation
                    invalidation_record = await self.db.get_user_invalidation_record(user_id, bot_id=current_bot_key)
                    if not invalidation_record:
                        # Try without bot_id to get any active invalidation
                        invalidation_record = await self.db.get_user_invalidation_record(user_id)
                    
                    if invalidation_record:
                        logger.info(
                            f"[{bot_display}] User {user_id} is blocked - active invalidation: "
                            f"bot_id={invalidation_record.get('bot_id')}, "
                            f"reason={invalidation_record.get('reason')}, "
                            f"created_at={invalidation_record.get('created_at')}"
                        )
                    else:
                        logger.info(f"[{bot_display}] User {user_id} is globally blocked (is_blocked=1)")
                    
                    # Mark as sent to remove from queue using atomic update
                    for msg in messages:
                        marked = await self.db.mark_custom_message_sent_atomic(msg["id"])
                        if not marked:
                            logger.warning(
                                f"[{bot_display}] Race condition: message {msg['id']} already marked as sent for user {user_id}"
                            )
                    failed_count += len(messages)
                    failure_reasons["blocked"] = failure_reasons.get("blocked", 0) + len(messages)
                    if user_id not in failed_user_ids:
                        failed_user_ids.append(user_id)
                    continue
                
                # Check rate limit for this user
                recent_count = await self.db.get_recent_custom_messages_count(user_id, minutes=60)
                
                if recent_count >= MAX_CUSTOM_MESSAGES_PER_HOUR:
                    logger.info(
                        f"[{bot_display}] User {user_id} hit rate limit: "
                        f"recent_count ({recent_count}) >= MAX_CUSTOM_MESSAGES_PER_HOUR ({MAX_CUSTOM_MESSAGES_PER_HOUR}) "
                        f"in last 60 minutes, skipping {len(messages)} message(s)"
                    )
                    failed_count += len(messages)
                    failure_reasons["rate_limited"] = failure_reasons.get("rate_limited", 0) + len(messages)
                    if user_id not in failed_user_ids:
                        failed_user_ids.append(user_id)
                    continue
                
                # Calculate how many more messages we can send to this user
                remaining = MAX_CUSTOM_MESSAGES_PER_HOUR - recent_count
                
                # Send only the allowed number of messages
                for message in messages[:remaining]:
                    batch.append(message)
                    
                    # Send in batches to avoid overwhelming Telegram
                    if len(batch) >= CUSTOM_MESSAGE_BATCH_SIZE:
                        result = await self._send_custom_message_batch(context, batch)
                        sent_count += result["sent"]
                        failed_count += result["failed"]
                        # Track failure reasons and invalidations
                        for reason, count in result.get("failure_reasons", {}).items():
                            failure_reasons[reason] = failure_reasons.get(reason, 0) + count
                        invalidated_count += result.get("invalidated", 0)
                        failed_user_ids.extend(result.get("failed_user_ids", []))
                        
                        # Check if pause was set during batch (429 response)
                        if result.get("pause_set"):
                            logger.info(f"[{bot_display}] Pause was set during batch processing, stopping immediately")
                            batch = []
                            # Stop processing entirely - don't process more messages
                            return {
                                "sent_count": sent_count,
                                "failed_count": failed_count,
                                "invalidated_count": invalidated_count,
                                "failure_reasons": failure_reasons,
                                "failed_user_ids": failed_user_ids
                            }
                        
                        batch = []
                        await asyncio.sleep(CUSTOM_MESSAGE_DELAY_SECONDS)
                
                # Mark messages that exceeded quota as sent (failed due to rate limit)
                if len(messages) > remaining:
                    excess_messages = messages[remaining:]
                    for msg in excess_messages:
                        await self.db.mark_custom_message_sent(msg["id"])
                    failed_count += len(excess_messages)
                    failure_reasons["rate_limited"] = failure_reasons.get("rate_limited", 0) + len(excess_messages)
                    logger.debug(f"User {user_id}: {len(excess_messages)} messages skipped due to rate limit")
            
            # Send remaining batch
            if batch:
                result = await self._send_custom_message_batch(context, batch)
                sent_count += result["sent"]
                failed_count += result["failed"]
                # Track failure reasons and invalidations
                for reason, count in result.get("failure_reasons", {}).items():
                    failure_reasons[reason] = failure_reasons.get(reason, 0) + count
                invalidated_count += result.get("invalidated", 0)
                failed_user_ids.extend(result.get("failed_user_ids", []))
                
                # Check if pause was set during batch (429 response)
                if result.get("pause_set"):
                    logger.info(f"[{bot_display}] Pause was set during final batch, stopping immediately")
                    return {
                        "sent_count": sent_count,
                        "failed_count": failed_count,
                        "invalidated_count": invalidated_count,
                        "failure_reasons": failure_reasons,
                        "failed_user_ids": failed_user_ids
                    }
                
                # Collect rate limit info and failure samples
                if "rate_limit_info" in result:
                    rate_limit_info = result["rate_limit_info"]
                if "failure_samples" in result:
                    failure_samples_list.extend(result["failure_samples"])
            
            # Log comprehensive telemetry
            telemetry = {
                "bot": bot_display,
                "total_queued": len(pending),
                "sent": sent_count,
                "failed": failed_count,
                "invalidated": invalidated_count,
                "failure_reasons": failure_reasons,
                "failed_user_ids_sample": failed_user_ids[:10]  # First 10 for debugging
            }
            
            # Add rate limit info if available
            if rate_limit_info:
                telemetry["rate_limit_info"] = rate_limit_info
            
            # Add failure samples
            if failure_samples_list:
                telemetry["failure_reason_samples"] = failure_samples_list[:20]  # First 20 samples
            
            logger.info(f"[{mode_desc}] Broadcast telemetry: {json.dumps(telemetry)}")
            
            return {
                "sent_count": sent_count, 
                "failed_count": failed_count, 
                "invalidated_count": invalidated_count,
                "failure_reasons": failure_reasons,
                "failed_user_ids": failed_user_ids
            }
            
        except Exception as e:
            logger.error(f"Error processing custom message queue: {e}", exc_info=True)
            # Re-raise to allow caller to detect failure
            raise
    
    def _normalize_reply_markup_urls(self, reply_markup_data: dict, bot_username: str) -> dict:
        """
        Normalize t.me deep-link URLs in reply_markup to use the current bot's username.
        This fixes issues where queued messages contain URLs with different bot usernames.
        
        Args:
            reply_markup_data: Parsed reply_markup JSON (InlineKeyboardMarkup.to_dict() format)
            bot_username: Username of the bot that will send the message
            
        Returns:
            Updated reply_markup_data with normalized URLs
        """
        if not reply_markup_data or not isinstance(reply_markup_data, dict):
            return reply_markup_data
        
        # InlineKeyboardMarkup structure: {"inline_keyboard": [[button, ...], ...]}
        inline_keyboard = reply_markup_data.get("inline_keyboard", [])
        
        for row in inline_keyboard:
            if not isinstance(row, list):
                continue
            for button in row:
                if not isinstance(button, dict):
                    continue
                # Check if button has a URL field
                if "url" in button and isinstance(button["url"], str):
                    original_url = button["url"]
                    # Replace old username with new username, preserving the query string
                    # Uses pre-compiled pattern from class constant
                    normalized_url = self._TELEGRAM_DEEPLINK_PATTERN.sub(
                        lambda m: f'https://t.me/{bot_username}{m.group(1)}',
                        original_url
                    )
                    if normalized_url != original_url:
                        logger.debug(f"Normalized URL: {original_url} -> {normalized_url}")
                        button["url"] = normalized_url
        
        return reply_markup_data
    
    async def _send_single_custom_message(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        message: dict,
        reply_markup: Optional[InlineKeyboardMarkup] = None,
        retry_count: int = 0
    ) -> dict:
        """
        Send a single custom message with retry logic and rate-limit handling.
        
        Features:
        - Uses send_message_with_rate_handling for robust RetryAfter handling
        - Implements retry-on-recent-start heuristic for "invalid_for_bot" errors
        - Tracks retry metadata in database
        
        Args:
            context: Telegram context
            message: Message data dict
            reply_markup: Pre-parsed reply markup (to avoid re-parsing)
            retry_count: Current retry attempt (default: 0, incremented internally during retries)
            
        Returns:
            dict with {"success": bool, "user_id": int, "message_id": int, "reason": str (if failed), 
                      "retry_after": Optional[float]}
        """
        user_id = message["user_id"]
        message_id = message["id"]
        message_text = message.get("message_text", "")
        retry_count_db = message.get("retry_count", 0)  # Track retries from database
        enqueued_at = message.get("enqueued_at")  # When broadcast was enqueued
        
        # Get bot identity for logging
        bot_username = context.bot.username if hasattr(context.bot, 'username') else 'unknown'
        current_bot_key = get_bot_env_key()
        bot_token = getattr(context.bot, 'token', '')
        token_fingerprint = _mask_token(bot_token)
        
        logger.debug(f"[Send] Attempting to send to user {user_id} via bot @{bot_username} (key={current_bot_key}, token={token_fingerprint})")
        
        try:
            # Check if user is blocked globally
            is_blocked = await self.db.is_user_blocked(user_id)
            if is_blocked:
                # Mark as sent to remove from queue using atomic update
                marked = await self.db.mark_custom_message_sent_atomic(message_id)
                if not marked:
                    logger.warning(f"[Send] Race condition: message {message_id} already marked as sent for user {user_id}")
                logger.debug(f"[Send] Skipped user {user_id} (globally blocked)")
                return {"success": False, "user_id": user_id, "message_id": message_id, "reason": "blocked"}
            
            # Check if user is invalid for this specific bot
            is_invalid_for_bot = await self.db.is_user_invalid_for_bot(user_id, current_bot_key)
            if is_invalid_for_bot:
                # Check retry-on-recent-start heuristic
                should_retry_recent_start = False
                if enqueued_at and retry_count_db == 0:
                    # Get user's started_at timestamp
                    user_data = await self.db.get_user(user_id)
                    if user_data and user_data.get('started_at'):
                        from datetime import datetime as dt
                        started_at = user_data['started_at']
                        # Parse timestamps (could be datetime objects or strings)
                        if isinstance(started_at, str):
                            started_at = dt.fromisoformat(started_at)
                        if isinstance(enqueued_at, str):
                            enqueued_at = dt.fromisoformat(enqueued_at)
                        
                        # Check if user started within RETRY_ON_RECENT_START_SECONDS after broadcast
                        if isinstance(started_at, datetime) and isinstance(enqueued_at, datetime):
                            time_diff = (started_at - enqueued_at).total_seconds()
                            if 0 <= time_diff <= Config.BROADCAST_RETRY_ON_RECENT_START_SECONDS:
                                should_retry_recent_start = True
                                logger.info(
                                    f"[Send] User {user_id} started {time_diff:.1f}s after broadcast enqueue "
                                    f"(within {Config.BROADCAST_RETRY_ON_RECENT_START_SECONDS}s window), "
                                    f"will retry after {Config.BROADCAST_RECENT_START_RETRY_DELAY_SECONDS}s"
                                )
                
                if should_retry_recent_start:
                    # Schedule retry after delay
                    await asyncio.sleep(Config.BROADCAST_RECENT_START_RETRY_DELAY_SECONDS)
                    # Update retry count
                    await self.db.increment_custom_message_retry_count(message_id)
                    # Retry (this will fail if still invalid, but gives user a chance)
                    is_invalid_for_bot = await self.db.is_user_invalid_for_bot(user_id, current_bot_key)
                    if is_invalid_for_bot:
                        # Still invalid after retry, mark as sent using atomic update
                        marked = await self.db.mark_custom_message_sent_atomic(message_id)
                        if not marked:
                            logger.warning(f"[Send] Race condition: message {message_id} already marked as sent for user {user_id}")
                        logger.debug(f"[Send] User {user_id} still invalid for bot {current_bot_key} after recent-start retry")
                        return {"success": False, "user_id": user_id, "message_id": message_id, "reason": "invalid_for_bot_after_retry"}
                else:
                    # Mark as sent to remove from queue using atomic update
                    marked = await self.db.mark_custom_message_sent_atomic(message_id)
                    if not marked:
                        logger.warning(f"[Send] Race condition: message {message_id} already marked as sent for user {user_id}")
                    logger.debug(f"[Send] Skipped user {user_id} (invalid for bot {current_bot_key})")
                    return {"success": False, "user_id": user_id, "message_id": message_id, "reason": "invalid_for_bot"}
            
            # Send message using rate-limit-aware helper
            result = await send_message_with_rate_handling(
                bot=context.bot,
                chat_id=user_id,
                text=message_text,
                parse_mode="Markdown",
                reply_markup=reply_markup,
                bot_key=current_bot_key
            )
            
            if result["success"]:
                # Mark as sent using atomic update
                marked = await self.db.mark_custom_message_sent_atomic(message_id)
                if not marked:
                    logger.warning(f"[Send] Race condition: message {message_id} already marked as sent for user {user_id}")
                logger.debug(f"[Send] ✓ Sent to user {user_id} via bot @{bot_username}")
                return {"success": True, "user_id": user_id, "message_id": message_id}
            else:
                # Handle different error types
                error = result.get("error", "unknown")
                retry_after = result.get("retry_after")
                
                # Check for permanent errors that should mark message as sent
                if "Forbidden" in error or "blocked" in error.lower() or "bot was blocked" in error.lower():
                    logger.info(f"[Send] User {user_id} blocked bot @{bot_username}, marking as blocked")
                    await self.db.block_user(user_id)
                    marked = await self.db.mark_custom_message_sent_atomic(message_id)
                    if not marked:
                        logger.warning(f"[Send] Race condition: message {message_id} already marked as sent for user {user_id}")
                    return {"success": False, "user_id": user_id, "message_id": message_id, "reason": "forbidden", "retry_after": retry_after}
                
                if self._is_chat_not_found(error):
                    logger.info(
                        f"[Send] User {user_id} chat not found for bot @{bot_username}, "
                        f"marking invalid for bot {current_bot_key} ONLY (not global)"
                    )
                    # CRITICAL: Always pass bot_id to ensure bot-scoped invalidation
                    await self.db.mark_user_invalid(user_id, bot_id=current_bot_key, reason="chat_not_found")
                    marked = await self.db.mark_custom_message_sent_atomic(message_id)
                    if not marked:
                        logger.warning(f"[Send] Race condition: message {message_id} already marked as sent for user {user_id}")
                    return {"success": False, "user_id": user_id, "message_id": message_id, "reason": "chat_not_found", "retry_after": retry_after}
                
                if "BadRequest" in error or "message too long" in error.lower() or "can't parse" in error.lower() or "Message is too long" in error:
                    logger.warning(f"[Send] BadRequest error for user {user_id} via bot @{bot_username}: {error}")
                    marked = await self.db.mark_custom_message_sent_atomic(message_id)
                    if not marked:
                        logger.warning(f"[Send] Race condition: message {message_id} already marked as sent for user {user_id}")
                    return {"success": False, "user_id": user_id, "message_id": message_id, "reason": "bad_request", "retry_after": retry_after}
                
                # RetryAfter is transient - check for it and set pause state
                if "RetryAfter" in error or retry_after or "Too Many Requests" in error or "retry after" in error.lower() or "rate_limit" in error.lower() or "429" in error:
                    logger.warning(
                        f"[Send] Rate limited for user {user_id}: error_type={type(result.get('exception', Exception)).__name__}, "
                        f"error_msg={error}, retry_after={retry_after}, bot=@{bot_username}"
                    )
                    
                    # Set pause state in database for this bot
                    pause_set = False
                    if bot_username and retry_after:
                        try:
                            pause_set = await set_pause_state_db(
                                db_path=Config.DB_PATH,
                                bot_key=bot_username,
                                retry_after_seconds=retry_after,
                                buffer_seconds=getattr(Config, 'PAUSE_BUFFER_SECONDS', 5.0)
                            )
                            if pause_set:
                                buffer_seconds = getattr(Config, 'PAUSE_BUFFER_SECONDS', 5.0)
                                logger.info(
                                    f"[Send] ✓ Set pause for bot @{bot_username} (key={current_bot_key}) "
                                    f"for {retry_after}s (+buffer={buffer_seconds}s)"
                                )
                            else:
                                logger.warning(f"[Send] Failed to set pause for bot @{bot_username}")
                        except Exception as pause_error:
                            logger.error(f"[Send] Failed to set pause state: {pause_error}")
                    
                    # Don't mark as sent - will retry in next batch (after pause expires)
                    # Preserve the reason from send_message_with_rate_handling
                    reason = "rate_limit_max_retries" if "max_retries" in error.lower() else "rate_limited"
                    return {
                        "success": False,
                        "user_id": user_id,
                        "message_id": message_id,
                        "reason": reason,
                        "retry_after": retry_after,
                        "pause_set": pause_set
                    }
                
                # Other transient errors (TimedOut, NetworkError) - don't mark as sent, will retry later
                logger.warning(f"[Send] Transient error for user {user_id} via bot @{bot_username}: {error}")
                return {"success": False, "user_id": user_id, "message_id": message_id, "reason": "transient_error", "retry_after": retry_after}
            
        except Forbidden:
            # User blocked the bot, block them in our system
            logger.info(f"[Send] User {user_id} blocked bot @{bot_username} (Forbidden exception), marking as blocked")
            await self.db.block_user(user_id)
            marked = await self.db.mark_custom_message_sent_atomic(message_id)
            if not marked:
                logger.warning(f"[Send] Race condition: message {message_id} already marked as sent for user {user_id}")
            return {"success": False, "user_id": user_id, "message_id": message_id, "reason": "forbidden"}
        
        except BadRequest as e:
            error_msg = str(e)
            logger.info(
                f"[Send] BadRequest exception for user {user_id} via bot @{bot_username}: "
                f"exception_type=BadRequest, error_msg={error_msg}"
            )
            # Check for chat not found errors (chat was deleted, user deactivated, etc.)
            if self._is_chat_not_found(error_msg):
                logger.info(
                    f"[Send] User {user_id} chat not found for bot @{bot_username} (BadRequest: {error_msg}), "
                    f"marking invalid for bot {current_bot_key} ONLY (not global)"
                )
                # CRITICAL: Always pass bot_id to ensure bot-scoped invalidation
                await self.db.mark_user_invalid(user_id, bot_id=current_bot_key, reason="chat_not_found")
                marked = await self.db.mark_custom_message_sent_atomic(message_id)
                if not marked:
                    logger.warning(f"[Send] Race condition: message {message_id} already marked as sent for user {user_id}")
                return {"success": False, "user_id": user_id, "message_id": message_id, "reason": "chat_not_found"}
            else:
                # Other BadRequest errors (e.g., message too long, invalid formatting)
                logger.warning(f"[Send] Non-permanent BadRequest error for user {user_id} via bot @{bot_username}: {error_msg}")
                marked = await self.db.mark_custom_message_sent_atomic(message_id)
                if not marked:
                    logger.warning(f"[Send] Race condition: message {message_id} already marked as sent for user {user_id}")
                return {"success": False, "user_id": user_id, "message_id": message_id, "reason": "bad_request"}
            
        except Exception as e:
            logger.exception(
                f"[Send] Unexpected exception sending to user {user_id} via bot @{bot_username}: "
                f"exception_type={type(e).__name__}, error_msg={str(e)}"
            )
            # Mark as sent to avoid infinite retries for non-recoverable errors
            marked = await self.db.mark_custom_message_sent_atomic(message_id)
            if not marked:
                logger.warning(f"[Send] Race condition: message {message_id} already marked as sent for user {user_id}")
            return {"success": False, "user_id": user_id, "message_id": message_id, "reason": "exception"}
    
    async def _send_custom_message_batch(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        messages: List[dict]
    ) -> dict:
        """
        Send a batch of custom text messages concurrently with rate limiting.
        Media messages are not supported for queued/broadcast messages.
        Returns dict with {"sent": int, "failed": int} counts.
        """
        if not messages:
            return {"sent": 0, "failed": 0}
        
        sent_count = 0
        failed_count = 0
        
        # Parse reply_markup once if all messages share the same markup
        # (common case for notifications about the same product)
        first_reply_markup_json = messages[0].get("reply_markup")
        shared_reply_markup = None
        # Quick check: compare first message with a sample of others to detect shared markup
        # Most batches either all share or all differ, so sampling is efficient
        sample_size = min(5, len(messages))
        all_same_markup = all(messages[i].get("reply_markup") == first_reply_markup_json for i in range(sample_size))
        
        # If sample suggests same markup, verify all (optimization for common case)
        if all_same_markup and len(messages) > sample_size:
            all_same_markup = all(m.get("reply_markup") == first_reply_markup_json for m in messages[sample_size:])
        
        if all_same_markup and first_reply_markup_json:
            try:
                reply_markup_data = json.loads(first_reply_markup_json)
                # Normalize t.me URLs to use the current bot's username
                bot_username = context.bot.username
                if bot_username:
                    reply_markup_data = self._normalize_reply_markup_urls(reply_markup_data, bot_username)
                shared_reply_markup = InlineKeyboardMarkup.de_json(reply_markup_data, context.bot)
                logger.debug(f"Reusing reply markup for batch of {len(messages)} messages")
            except Exception as e:
                logger.warning(f"Failed to parse shared reply_markup: {e}")
        
        # Create semaphore for concurrency control
        semaphore = asyncio.Semaphore(NOTIFICATION_CONCURRENCY)
        
        async def send_with_semaphore(message: dict):
            async with semaphore:
                # Parse individual reply_markup if not shared
                reply_markup = shared_reply_markup
                if not all_same_markup:
                    reply_markup_json = message.get("reply_markup")
                    if reply_markup_json:
                        try:
                            reply_markup_data = json.loads(reply_markup_json)
                            bot_username = context.bot.username
                            if bot_username:
                                reply_markup_data = self._normalize_reply_markup_urls(reply_markup_data, bot_username)
                            reply_markup = InlineKeyboardMarkup.de_json(reply_markup_data, context.bot)
                        except Exception as e:
                            logger.warning(f"Failed to parse reply_markup for message {message['id']}: {e}")
                
                return await self._send_single_custom_message(context, message, reply_markup)
        
        # Send all messages concurrently
        tasks = [send_with_semaphore(msg) for msg in messages]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Count successes, failures, and track reasons
        failure_reasons = {}
        invalidated_count = 0
        failed_user_ids = []
        retry_after_samples = []  # Track RetryAfter values
        failure_samples = []  # Track sample error messages
        pause_set = False  # Track if pause was set due to 429
        
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Exception in concurrent send: {result}")
                failed_count += 1
                failure_reasons["exception"] = failure_reasons.get("exception", 0) + 1
                failure_samples.append(f"Exception: {str(result)}")
            elif isinstance(result, dict):
                if result.get("success"):
                    sent_count += 1
                else:
                    failed_count += 1
                    reason = result.get("reason", "unknown")
                    failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
                    
                    # Track RetryAfter samples
                    retry_after = result.get("retry_after")
                    if retry_after and len(retry_after_samples) < 10:
                        retry_after_samples.append(retry_after)
                    
                    # Check if pause was set for this result
                    if result.get("pause_set"):
                        pause_set = True
                    
                    # Track failure samples
                    if len(failure_samples) < 20:
                        failure_samples.append(f"{reason}: {result.get('error', 'N/A')}")
                    
                    if reason == "chat_not_found":
                        invalidated_count += 1
                    user_id = result.get("user_id")
                    if user_id and user_id not in failed_user_ids:
                        failed_user_ids.append(user_id)
            else:
                logger.warning(f"Unexpected result type: {type(result)}")
                failed_count += 1
                failure_reasons["unknown_type"] = failure_reasons.get("unknown_type", 0) + 1
        
        # Build rate_limit_info if we have RetryAfter samples
        rate_limit_info = None
        if retry_after_samples:
            rate_limit_info = {
                "count": len(retry_after_samples),
                "retry_after_samples": retry_after_samples,
                "max_retry_after": max(retry_after_samples)
            }
        
        return {
            "sent": sent_count, 
            "failed": failed_count,
            "failure_reasons": failure_reasons,
            "invalidated": invalidated_count,
            "failed_user_ids": failed_user_ids,
            "rate_limit_info": rate_limit_info,
            "failure_samples": failure_samples,
            "pause_set": pause_set  # Indicate if pause was set during this batch
        }
    
    async def notify_subscribers_about_product(self, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> int:
        """
        Notify subscribers about a new product via broadcast forwarder.
        
        This now uses the broadcast forwarder pattern so that ALL bot instances
        deliver product notifications to their own subscribed users. This ensures
        users receive notifications from all bots they've started.
        
        Args:
            context: Telegram context
            product_id: ID of the product to notify about
            
        Returns:
            1 if broadcast was published successfully, 0 otherwise
        """
        try:
            # Get bot username for logging
            bot_username = context.bot.username if hasattr(context.bot, 'username') else 'unknown'
            logger.info(f"[NotifySubscribers] Starting notification for product {product_id} (bot @{bot_username})")
            
            # Load product details
            product = await self.db.get_product(product_id)
            if not product:
                logger.warning(f"[NotifySubscribers] Product {product_id} not found")
                return 0
            
            # Check if product is categorized
            category = product.get("category")
            if not category:
                logger.info(f"[NotifySubscribers] Product {product_id} is not categorized, skipping")
                return 0
            
            # Skip notifications for excluded categories
            if category in NOTIFICATION_EXCLUDED_CATEGORIES:
                logger.info(f"[NotifySubscribers] Product {product_id} is in excluded category '{category}', skipping")
                return 0
            
            # Build message preview
            subcategory = product.get("subcategory")
            caption = product.get("caption", "")
            
            # Get user language for category display (default to English)
            category_display = get_category_display_name(category)
            subcategory_display = get_subcategory_display_name(subcategory) if subcategory else None
            
            # Build category text
            category_text = f"📂 {category_display}"
            if subcategory_display:
                category_text += f" • {subcategory_display}"
            
            # Build caption preview (first 100 chars)
            caption_preview = caption[:100] + "..." if len(caption) > 100 else caption
            caption_line = f"\n\n📝 {caption_preview}" if caption_preview else ""
            
            # Build message text
            message_text = (
                f"🆕 **New Product Available!**\n\n"
                f"{category_text}"
                f"{caption_line}\n\n"
                f"👉 Use /menu to view the catalog"
            )
            
            # Build inline keyboard with product actions
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("👁️ View Product", callback_data=f"product|{product_id}")],
                [InlineKeyboardButton("📋 View Catalog", callback_data="categories")],
                [InlineKeyboardButton("🔕 Unsubscribe", callback_data=f"toggle_notifications|{product_id}")]
            ])
            
            # Serialize keyboard to JSON
            reply_markup_json = json.dumps(keyboard.to_dict())
            
            # Use broadcast forwarder to ensure all bot instances deliver to their subscribed users
            # Create payload with product_id and reply_markup so each bot knows this is a product notification
            payload = {
                "product_id": product_id,
                "reply_markup_json": reply_markup_json
            }
            
            broadcast_id = await broadcast_forwarder.publish_broadcast(
                self.db, 
                message_text, 
                payload=payload
            )
            
            logger.info(f"[NotifySubscribers] Published product notification as broadcast {broadcast_id} for product {product_id}")
            
            return 1
            
        except Exception as e:
            logger.error(f"[NotifySubscribers] Error notifying subscribers about product {product_id}: {e}", exc_info=True)
            return 0


# Module-level helper for broadcast forwarding
async def enqueue_local_broadcast(db: Database, context: dict, message_text: str) -> None:
    """
    Enqueue a broadcast message for local delivery by this bot instance.
    
    This is used by the broadcast forwarder to process broadcast events.
    Each bot instance delivers the broadcast to ALL users who have started that bot
    (not just users who last interacted with it).
    
    Args:
        db: Database instance to use for queries and queueing
        context: Dictionary with optional payload data. Can include:
                 - target_user_id: If set, only deliver to this specific user (if they've started this bot)
                 - product_id: If set, only deliver to subscribers (if they've started this bot)
        message_text: The broadcast message text to send
    
    Note:
        Changed to use bot_username instead of last_bot_key to ensure all users
        who have ever started this bot receive the message.
    """
    try:
        # Get this bot's environment key and username
        from utils.bot_key import get_bot_env_key, get_bot_username
        from utils.helpers import get_admin_ids
        
        bot_key = get_bot_env_key()
        bot_username = get_bot_username()
        
        # Extract optional payload fields
        target_user_id = context.get("target_user_id") if context else None
        product_id = context.get("product_id") if context else None
        reply_markup_json = context.get("reply_markup_json") if context else None
        
        logger.info(f"[BroadcastEnqueue] Processing broadcast for bot @{bot_username} (key={bot_key}): {message_text[:50]}...")
        if target_user_id:
            logger.info(f"[BroadcastEnqueue] Single-user target: {target_user_id}")
        if product_id:
            logger.info(f"[BroadcastEnqueue] Product notification: {product_id}")
        
        admin_ids_set = set(get_admin_ids())
        
        # Get users who have started this bot (based on bot_username)
        if bot_username:
            bot_user_ids = set(await db.get_users_by_bot(bot_username))
            # Log sample of user IDs for debugging (first 10)
            import itertools
            sample_user_ids = list(itertools.islice(bot_user_ids, 10))
            logger.info(f"[BroadcastEnqueue] Found {len(bot_user_ids)} users for bot @{bot_username} (sample: {sample_user_ids}{'...' if len(bot_user_ids) > 10 else ''})")
        else:
            # Fallback: get all users if bot_username is not available
            logger.warning(f"[BroadcastEnqueue] bot_username not available for {bot_key}, using all users")
            all_users = await db.get_all_users()
            bot_user_ids = set(u["user_id"] for u in all_users)
        
        # Build the list of users to send to
        users_to_notify = []
        
        if target_user_id:
            # Single-user send: check if user has started this bot
            if target_user_id in admin_ids_set:
                logger.info(f"[BroadcastEnqueue] User {target_user_id} is admin, skipping")
            elif target_user_id in bot_user_ids:
                # User has started this bot
                is_blocked = await db.is_user_blocked(target_user_id)
                if not is_blocked:
                    users_to_notify.append(target_user_id)
                    logger.info(f"[BroadcastEnqueue] User {target_user_id} has started bot @{bot_username}, will deliver")
                else:
                    logger.info(f"[BroadcastEnqueue] User {target_user_id} is blocked, skipping")
            else:
                # User has NOT started this bot - attempt fallback
                logger.info(f"[BroadcastEnqueue] User {target_user_id} has not started bot @{bot_username}, attempting fallback")
                
                # Step 1: Query for other bot usernames the user has started
                user_bot_usernames = await db.get_user_bot_usernames(target_user_id)
                
                if user_bot_usernames:
                    logger.info(f"[BroadcastEnqueue] User {target_user_id} has started {len(user_bot_usernames)} other bot(s): {user_bot_usernames}")
                    
                    # Queue message for each bot the user has started
                    for other_bot_username in user_bot_usernames:
                        # Map bot_username to bot_key
                        other_bot_key = await db.get_bot_key_for_username(other_bot_username)
                        
                        if other_bot_key:
                            logger.info(f"[BroadcastEnqueue] Queuing single-user message for user {target_user_id} to bot @{other_bot_username} (key={other_bot_key})")
                            # Queue directly to the other bot
                            await db.queue_custom_messages_batch_with_targets(
                                [(target_user_id, other_bot_key)],
                                message_text,
                                media_type=None,
                                media_file_id=None,
                                reply_markup=reply_markup_json
                            )
                        else:
                            logger.warning(f"[BroadcastEnqueue] Could not map @{other_bot_username} to bot_key for user {target_user_id}")
                    
                    # Successfully queued to other bot(s), so we're done
                    logger.info(f"[BroadcastEnqueue] Single-user fallback completed for user {target_user_id}")
                    return
                else:
                    # No bot_username rows found, try last_bot_key fallback
                    logger.info(f"[BroadcastEnqueue] No bot_username rows found for user {target_user_id}, trying last_bot_key fallback")
                    last_bot_key = await db.get_user_last_bot_key(target_user_id)
                    
                    if last_bot_key:
                        logger.info(f"[BroadcastEnqueue] Queuing single-user message for user {target_user_id} to last_bot_key={last_bot_key}")
                        await db.queue_custom_messages_batch_with_targets(
                            [(target_user_id, last_bot_key)],
                            message_text,
                            media_type=None,
                            media_file_id=None,
                            reply_markup=reply_markup_json
                        )
                        logger.info(f"[BroadcastEnqueue] Single-user fallback to last_bot_key completed for user {target_user_id}")
                        return
                    else:
                        # No bots found at all
                        logger.info(f"[BroadcastEnqueue] No bots found for user {target_user_id} (not in bot_users table or no last_bot_key), skipping")
                        return
        
        elif product_id:
            # Product notification: send to subscribed users who have started this bot
            subscribed_users = set(await db.get_subscribed_users())
            skipped_not_subscribed = 0
            skipped_blocked = 0
            for user_id in bot_user_ids:
                if user_id in admin_ids_set:
                    continue  # Skip admins
                if user_id in subscribed_users:
                    is_blocked = await db.is_user_blocked(user_id)
                    if not is_blocked:
                        users_to_notify.append(user_id)
                    else:
                        skipped_blocked += 1
                else:
                    skipped_not_subscribed += 1
            logger.info(f"[BroadcastEnqueue] Product notification: {len(users_to_notify)} subscribed users from bot @{bot_username} (skipped: {skipped_not_subscribed} not subscribed, {skipped_blocked} blocked)")
        
        else:
            # Broadcast to all: send to all users who have started this bot
            skipped_admin = 0
            skipped_blocked = 0
            for user_id in bot_user_ids:
                if user_id in admin_ids_set:
                    skipped_admin += 1
                    continue
                is_blocked = await db.is_user_blocked(user_id)
                if not is_blocked:
                    users_to_notify.append(user_id)
                else:
                    skipped_blocked += 1
            logger.info(f"[BroadcastEnqueue] Broadcast to all: {len(users_to_notify)} eligible users from bot @{bot_username} (skipped: {skipped_admin} admins, {skipped_blocked} blocked)")

        
        if not users_to_notify:
            logger.info(f"[BroadcastEnqueue] No users to notify for bot @{bot_username}")
            return
        
        # Queue messages with target_bot_key for proper routing
        users_with_targets = [(user_id, bot_key) for user_id in users_to_notify]
        queued = await db.queue_custom_messages_batch_with_targets(
            users_with_targets,
            message_text,
            media_type=None,
            media_file_id=None,
            reply_markup=reply_markup_json
        )
        
        logger.info(f"[BroadcastEnqueue] Queued {queued} messages for bot @{bot_username}")
        
    except Exception as e:
        logger.error(f"[BroadcastEnqueue] Error enqueueing local broadcast: {e}", exc_info=True)
        # Don't raise - we want the broadcast forwarder to continue


def create_enqueue_local_broadcast_callback(db: Database):
    """
    Create a callback function for enqueue_local_broadcast that has the database instance bound.
    
    This is used to create a callback that can be passed to the broadcast forwarder
    while providing access to the database instance.
    
    Args:
        db: Database instance to use for the callback
        
    Returns:
        An async callback function with signature (context: dict, message_text: str) -> None
        that can be passed to broadcast_forwarder.start_listener_in_background()
    
    Example:
        >>> callback = create_enqueue_local_broadcast_callback(db)
        >>> listener_task = broadcast_forwarder.start_listener_in_background(db, callback)
    """
    async def callback(context: dict, message_text: str) -> None:
        """
        Process a broadcast event and enqueue messages for local delivery.
        
        Args:
            context: Dictionary with optional payload data (target_user_id, product_id, etc.)
            message_text: The broadcast message text to send
        """
        await enqueue_local_broadcast(db, context, message_text)
    
    return callback



# ===========================
# Background Delivery Processor
# ===========================

class BotContext:
    """
    Minimal context wrapper for NotificationService methods.
    
    This wrapper provides the required .bot attribute with username and send methods
    that NotificationService needs to process custom messages.
    """
    
    def __init__(self, application):
        """
        Initialize context wrapper.
        
        Args:
            application: The Telegram Application instance
        """
        self.bot = application.bot
        self.bot_data = application.bot_data


async def _custom_message_processor_loop(application, db: Database, interval_seconds: int):
    """
    Internal background loop that periodically processes pending custom messages.
    
    This loop:
    1. Creates a BotContext wrapper for the application
    2. Calls NotificationService.process_custom_message_queue periodically
    3. Logs results and handles errors gracefully
    4. Continues running with backoff on errors
    
    Args:
        application: Telegram Application instance
        db: Database instance
        interval_seconds: Poll interval in seconds
    """
    from utils.bot_key import get_bot_env_key, get_bot_key_display
    
    bot_key = get_bot_env_key()
    bot_username = application.bot.username if hasattr(application.bot, 'username') else None
    bot_display = get_bot_key_display(bot_key, bot_username)
    
    logger.info(f"[CustomMessageProcessor] Starting background processor for {bot_display} with interval={interval_seconds}s")
    
    notification_service = NotificationService(db)
    context = BotContext(application)
    
    consecutive_errors = 0
    max_consecutive_errors = 10
    
    while True:
        try:
            # Process pending custom messages for this bot
            result = await notification_service.process_custom_message_queue(context, process_all_bots=False)
            
            # Log summary if any messages were processed
            sent = result.get("sent_count", 0)
            failed = result.get("failed_count", 0)
            invalidated = result.get("invalidated_count", 0)
            
            if sent > 0 or failed > 0 or invalidated > 0:
                logger.info(
                    f"[CustomMessageProcessor] {bot_display} - Processed: "
                    f"sent={sent}, failed={failed}, invalidated={invalidated}"
                )
                
                # Log failure reasons if there were failures
                failure_reasons = result.get("failure_reasons", {})
                if failure_reasons:
                    logger.info(f"[CustomMessageProcessor] {bot_display} - Failure breakdown: {failure_reasons}")
            
            # Reset error counter on success
            consecutive_errors = 0
            
        except Exception as e:
            consecutive_errors += 1
            logger.error(
                f"[CustomMessageProcessor] {bot_display} - Error processing queue "
                f"(consecutive errors: {consecutive_errors}): {e}",
                exc_info=True
            )
            
            # If too many consecutive errors, increase backoff
            if consecutive_errors >= max_consecutive_errors:
                logger.warning(
                    f"[CustomMessageProcessor] {bot_display} - "
                    f"{consecutive_errors} consecutive errors, extending backoff to 60s"
                )
                await asyncio.sleep(60)
                continue
        
        # Wait before next poll
        await asyncio.sleep(interval_seconds)


def start_custom_message_processor_in_background(
    application,
    db: Database,
    interval_seconds: int = 3
) -> asyncio.Task:
    """
    Start a background task that periodically processes pending custom messages.
    
    This processor ensures that queued messages (from broadcasts, product notifications,
    or custom sends) are delivered by the bot instance responsible for each user.
    
    In multi-bot setups:
    - Each bot instance runs its own processor
    - Messages are filtered by target_bot_key to avoid duplicates
    - The processor respects rate limits and handles errors gracefully
    
    Args:
        application: Telegram Application instance (must have .bot with username and .bot_data)
        db: Database instance
        interval_seconds: Poll interval in seconds (default: 3s)
    
    Returns:
        asyncio.Task: The background task running the processor loop
    
    Example:
        >>> task = start_custom_message_processor_in_background(application, db, interval_seconds=3)
        >>> # Store task to prevent garbage collection
        >>> application.bot_data['custom_message_processor_task'] = task
    """
    task = asyncio.create_task(_custom_message_processor_loop(application, db, interval_seconds))
    return task
