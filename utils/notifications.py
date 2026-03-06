"""
Notification service for sending product notifications to users.
Implements rate limiting to respect Telegram's anti-spam policies.
Supports multi-bot delivery - sends messages through the bot each user started.
"""
import logging
import asyncio
import uuid
from typing import List, Dict, Optional
from datetime import datetime
import aiosqlite
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, Application
from telegram.error import TelegramError, Forbidden, BadRequest

from database import Database
from utils.categories import get_category_display_name, get_subcategory_display_name, NOTIFICATION_EXCLUDED_CATEGORIES
from utils.helpers import get_admin_ids, get_bot_specific_file_id
from utils.safe_send import safe_send_message, safe_send_photo, safe_send_video, safe_send_animation, safe_send_document, handle_send_exception
from utils.rate_limiter import RateLimiter
from translations.translator import translate_text_async
from configs.config import Config
import emojis

logger = logging.getLogger(__name__)

# Rate limiting configuration
MAX_NOTIFICATIONS_PER_HOUR = 5  # Maximum notifications per user per hour
NOTIFICATION_BATCH_SIZE = 25  # Send notifications in batches (increased from 10)
BATCH_DELAY_SECONDS = 1  # Delay between batches within a group (reduced from 3)

# Queue processing configuration
PENDING_NOTIFICATIONS_BATCH_LIMIT = 100  # Number of pending notifications to fetch per batch

# Staggered delivery configuration
ENABLE_STAGGERED_DELIVERY = True  # Enable staggered delivery to avoid spam flags
STAGGER_GROUP_SIZE = 25  # Number of users per group for staggered delivery (increased from 10)
STAGGER_INTERVAL_SECONDS = 2  # 2s between groups = ~12.5 msg/s average per bot (42% of Telegram's 30/s hard limit)

# Telegram API rate limit reference (per bot, independent across bots):
# Hard limit: 30 msg/s. Target utilization: ~42% (12.5 msg/s).
# Formula: STAGGER_GROUP_SIZE / STAGGER_INTERVAL_SECONDS = msg/s throughput per bot.
# At 5,000 users/bot: ceil(5000/25) = 200 groups × 2s = ~6.7 min total delivery time.
# At 45,000 users across 9 bots (parallel): same ~6.7 min (bots run concurrently).
TELEGRAM_MSGS_PER_SECOND_HARD_LIMIT = 30   # Telegram's documented hard limit per bot
RATE_LIMIT_SAFETY_FACTOR = 0.42            # Current utilization ratio (12.5/30)

# Custom message rate limiting
MAX_CUSTOM_MESSAGES_PER_HOUR = 3  # Maximum custom messages per user per hour
CUSTOM_MESSAGE_BATCH_SIZE = 20  # Send custom messages in batches
CUSTOM_MESSAGE_DELAY_SECONDS = 1  # Delay between batches for custom messages

# Per-bot token-bucket rate limiter configuration
BOT_SEND_RATE_PER_SEC = 10  # Steady-state message rate per bot (tokens/second)
BOT_SEND_BURST = 20          # Maximum burst size per bot (tokens)

# Global concurrency cap: maximum number of bot workers running simultaneously
MAX_CONCURRENT_BOT_WORKERS = 5

# Logging configuration
FILE_ID_PREVIEW_LENGTH = 20  # Number of characters to show when logging file IDs


def get_file_id_preview(file_id: str, max_length: int = FILE_ID_PREVIEW_LENGTH) -> str:
    """
    Get a preview of a file ID for logging purposes.
    
    Args:
        file_id: The file ID to preview
        max_length: Maximum length of the preview
    
    Returns:
        Preview string with ellipsis if truncated
    """
    if len(file_id) > max_length:
        return f"{file_id[:max_length]}..."
    return file_id


def get_bot_applications() -> List[Application]:
    """
    Get all bot application instances from the webhook server.
    This allows multi-bot delivery.
    """
    try:
        from webhook_server import get_bot_applications as get_apps
        return get_apps()
    except ImportError:
        logger.debug("webhook_server not available, returning empty list")
        return []


def get_bot_username(bot_app: Application) -> str:
    """
    Get the username of a bot application.
    Returns 'unknown' if username is not available.
    """
    if hasattr(bot_app, 'bot') and hasattr(bot_app.bot, 'username'):
        return bot_app.bot.username
    return 'unknown'


def get_bot_by_username(username: str) -> Optional[Application]:
    """
    Get a bot application by username.
    Returns None if bot not found.
    """
    if not username:
        return None
    
    # Normalize username (remove @ if present, lowercase)
    username = username.lstrip('@').lower()
    
    bot_apps = get_bot_applications()
    for bot_app in bot_apps:
        if get_bot_username(bot_app).lower() == username:
            return bot_app
    return None


class NotificationService:
    """Service for managing and sending product notifications with sequential FIFO queue."""

    def __init__(self, db: Database):
        self.db = db
        self._bot_apps_cache = None  # Cache for bot applications
        self._bot_apps_cache_time = None  # Timestamp of last cache
        self._cache_ttl = 60  # Cache TTL in seconds
        self._notification_queue = asyncio.Queue()  # FIFO queue for notification requests
        self._queue_worker_task = None  # Background worker task
        self._is_processing = False  # Track if worker is currently processing
        # Per-bot token-bucket rate limiters keyed by normalised bot username
        self._bot_rate_limiters: Dict[str, RateLimiter] = {}
        # Global semaphore to cap concurrent bot worker coroutines
        self._bot_worker_semaphore = asyncio.Semaphore(MAX_CONCURRENT_BOT_WORKERS)
    
    @staticmethod
    def _is_markdown_parse_error(error: BadRequest) -> bool:
        """
        Check if a BadRequest error is caused by markdown parsing.
        Returns True if the error is related to markdown parsing, False otherwise.
        """
        error_str = str(error).lower()
        # Check for common markdown parse error indicators
        # Note: We check specific error patterns to avoid false positives
        return any(keyword in error_str for keyword in [
            "can't parse",
            "parse entities",
            "markdown"
        ])

    def _get_bot_rate_limiter(self, bot_username: Optional[str]) -> RateLimiter:
        """
        Return (creating if needed) the RateLimiter for the given bot.

        Args:
            bot_username: Normalised bot username or None for the primary bot.

        Returns:
            Per-bot RateLimiter instance.
        """
        key = bot_username.lower().lstrip('@') if bot_username else 'primary'
        if key not in self._bot_rate_limiters:
            self._bot_rate_limiters[key] = RateLimiter(
                rate_per_sec=BOT_SEND_RATE_PER_SEC,
                burst=BOT_SEND_BURST,
            )
        return self._bot_rate_limiters[key]

    async def _start_queue_worker(self):
        """Start the background queue worker if not already running."""
        if self._queue_worker_task is None or self._queue_worker_task.done():
            self._queue_worker_task = asyncio.create_task(self._queue_worker())
            logger.info("Notification queue worker started")

    async def _queue_worker(self):
        """Background worker that processes notification requests sequentially from the FIFO queue."""
        logger.info("Notification queue worker loop started")
        while True:
            try:
                # Wait for next notification request (product_id, context)
                product_id, context = await self._notification_queue.get()
                logger.info(f"Queue worker processing notification for product {product_id}")

                try:
                    self._is_processing = True
                    # Process the notification batch (returns stats dict)
                    stats = await self.process_notification_queue(context)

                    # Send admin summary after completing this batch
                    admin_ids = get_admin_ids()
                    if admin_ids and stats["sent"] > 0:
                        admin_id = admin_ids[0]
                        bot_app_for_admin = None
                        try:
                            bot_apps = self._get_bot_applications()
                            if bot_apps:
                                bot_app_for_admin = bot_apps[0]
                                bot_for_summary = bot_app_for_admin.bot
                                logger.info(f"Sending admin summary via primary bot @{get_bot_username(bot_app_for_admin)}")
                            else:
                                logger.info("No bot applications available, falling back to context.bot for admin summary")
                                bot_for_summary = context.bot

                            await self._send_admin_summary(
                                bot_for_summary,
                                admin_id,
                                stats,
                                operation="product notification"
                            )
                            logger.info(
                                f"Admin summary sent successfully: {stats['sent']} sent, "
                                f"{stats['attempted']} attempted, "
                                f"{stats['rate_limited']} rate-limited, "
                                f"{stats['duplicates_removed']} duplicates removed"
                            )
                        except Exception as e:
                            logger.error(
                                f"Failed to send admin summary via "
                                f"@{get_bot_username(bot_app_for_admin) if bot_app_for_admin else 'context.bot'}: {e}",
                                exc_info=True
                            )
                except Exception as e:
                    logger.error(f"Error processing notification for product {product_id}: {e}", exc_info=True)
                finally:
                    self._is_processing = False
                    # Mark task as done in the queue
                    self._notification_queue.task_done()
                    logger.info(f"Completed processing notification for product {product_id}")

            except Exception as e:
                logger.error(f"Error in notification queue worker: {e}", exc_info=True)
                await asyncio.sleep(1)  # Brief pause before continuing

    
    async def _remove_duplicate_notification(self, notification_id: int):
        """
        Remove a duplicate notification from the queue with retry logic.
        Falls back to marking it as sent if deletion fails after retries.
        """
        max_retries = 3
        retry_delay = 0.5  # seconds
        
        for attempt in range(max_retries):
            try:
                await self.db.delete_notification(notification_id)
                if attempt > 0:
                    logger.info(f"Successfully deleted duplicate notification {notification_id} on attempt {attempt + 1}")
                return  # Success
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Failed to delete duplicate notification {notification_id} (attempt {attempt + 1}/{max_retries}): {e}. Retrying..."
                    )
                    await asyncio.sleep(retry_delay * (attempt + 1))  # Linear backoff
                else:
                    logger.error(
                        f"Failed to delete duplicate notification {notification_id} after {max_retries} attempts: {e}"
                    )
        
        # All deletion attempts failed - fallback to marking as sent
        logger.warning(f"Falling back to marking duplicate notification {notification_id} as sent")
        try:
            await self.db.mark_notification_sent(notification_id)
            logger.info(f"Successfully marked duplicate notification {notification_id} as sent")
        except Exception as mark_error:
            # Last resort failed - log critical error
            logger.error(
                f"CRITICAL: Fallback mark-as-sent failed for duplicate notification {notification_id}: {mark_error}. "
                f"Notification may remain stuck in queue.",
                exc_info=True
            )
    
    async def _send_media_notification(
        self,
        bot_app: Application,
        user_id: int,
        file_type: str,
        file_id: str,
        caption: str,
        keyboard: InlineKeyboardMarkup,
        parse_mode: Optional[str] = "Markdown"
    ) -> bool:
        """
        Helper method to send media notification for a specific file type.
        Returns True if sent successfully, False otherwise.
        Raises exceptions for caller to handle.
        """
        result = None
        if file_type == "photo":
            result = await safe_send_photo(
                bot=bot_app.bot,
                chat_id=user_id,
                photo=file_id,
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=keyboard
            )
        elif file_type == "video":
            result = await safe_send_video(
                bot=bot_app.bot,
                chat_id=user_id,
                video=file_id,
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=keyboard
            )
        elif file_type == "animation":
            result = await safe_send_animation(
                bot_app.bot,
                user_id,
                animation=file_id,
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=keyboard
            )
        elif file_type == "document":
            result = await safe_send_document(
                bot_app.bot,
                user_id,
                document=file_id,
                caption=caption,
                parse_mode=parse_mode,
                reply_markup=keyboard
            )
        else:
            # Unsupported media type
            return False
        return result is not None
    
    def _get_bot_applications(self) -> List[Application]:
        """
        Get bot applications with caching to avoid repeated imports.
        Cache is refreshed every 60 seconds.
        """
        # Check if cache is valid
        if (self._bot_apps_cache is not None and 
            self._bot_apps_cache_time is not None and
            (datetime.now() - self._bot_apps_cache_time).total_seconds() < self._cache_ttl):
            return self._bot_apps_cache
        
        # Refresh cache
        self._bot_apps_cache = get_bot_applications()
        self._bot_apps_cache_time = datetime.now()
        return self._bot_apps_cache
    
    def _is_primary_instance(self, context: ContextTypes.DEFAULT_TYPE) -> bool:
        """
        Check if this is the primary bot instance.
        Only the primary bot (first token in BOT_TOKENS) should create notification queues.
        
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
            return True
        
        # Primary bot is the first token in the list (index 0)
        is_primary = current_token == bot_tokens[0]
        
        if not is_primary:
            logger.debug(f"Skipping operation - not primary instance (bot token: ...{current_token[-5:]})")
        
        return is_primary
    
    async def notify_users_about_product(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        product_id: int
    ):
        """
        Notify subscribed users about a new categorized product.
        Uses a FIFO queue to ensure notifications are processed sequentially.
        New notification requests will wait in line until the current batch finishes.
        Only runs on the primary bot instance to avoid duplicate notifications.
        """
        # Check if this is the primary bot instance
        if not self._is_primary_instance(context):
            logger.info(f"Skipping notification for product {product_id} - not primary instance")
            return

        try:
            # Get product details
            product = await self.db.get_product(product_id)
            if not product:
                logger.warning(f"Product {product_id} not found for notification")
                return

            # Only notify if product is categorized
            if not product.get("category"):
                logger.debug(f"Product {product_id} not categorized, skipping notification")
                return

            # Check if category should be excluded from notifications
            category = product.get("category")
            if category in NOTIFICATION_EXCLUDED_CATEGORIES:
                logger.info(f"Product {product_id} is in excluded category '{category}', skipping notification")
                return

            # Get all subscribed users
            subscribed_users = await self.db.get_subscribed_users()

            if not subscribed_users:
                logger.debug("No subscribed users to notify")
                return

            logger.info(f"Queueing notifications for {len(subscribed_users)} users about product {product_id}")

            # Queue notifications for all subscribed users in the database
            for user_id in subscribed_users:
                await self.db.queue_notification(user_id, product_id)

            # Add this notification request to the FIFO queue
            # It will be processed sequentially by the worker
            await self._notification_queue.put((product_id, context))
            queue_size = self._notification_queue.qsize()
            logger.info(f"Product {product_id} added to notification queue (queue size: {queue_size})")

            # Start the queue worker if not already running
            await self._start_queue_worker()

        except Exception as e:
            logger.error(f"Error notifying users about product {product_id}: {e}", exc_info=True)
    
    async def process_notification_queue(self, context: ContextTypes.DEFAULT_TYPE):
        """
        Process pending notifications from the queue with rate limiting and staggered delivery.
        Uses bot-first grouping: notifications are grouped by bot_username BEFORE processing,
        then each bot's notifications are processed simultaneously via asyncio.gather.

        Uses atomic claim/lease semantics to prevent duplicate processing across concurrent workers.
        Each batch is claimed with a unique token and lease timeout, ensuring only one worker
        processes each notification at a time.

        Processes all pending notifications in batches until queue is empty.

        Returns:
            Dictionary with statistics: {
                "sent": int,
                "attempted": int,
                "rate_limited": int,
                "duplicates_removed": int
            }
        """
        try:
            # Generate unique claim token for this processing run
            claim_token = str(uuid.uuid4())
            claimed_by = f"process_{claim_token[:8]}"

            total_sent_count = 0
            total_notifications_attempted = 0
            total_duplicates_removed = 0
            batch_number = 0
            rate_limited_users = set()  # Track users who hit rate limits in this run
            per_bot_sent: Dict[str, int] = {}  # Track successful deliveries per bot username
            # Tracks duplicates across batches for this processing run
            seen_user_product_global = set()
            # Queue processing runs as a single task; DB unique constraint prevents new duplicates

            logger.info(f"Starting notification queue processing with claim_token={claim_token[:8]}... claimed_by={claimed_by}")

            # Process notifications in batches until queue is empty
            while True:
                batch_number += 1

                # Atomically claim pending notifications
                pending = await self.db.claim_pending_notifications(
                    limit=PENDING_NOTIFICATIONS_BATCH_LIMIT,
                    claim_token=claim_token,
                    lease_seconds=300,  # 5 minute lease
                    claimed_by=claimed_by
                )

                if not pending:
                    if batch_number == 1:
                        logger.debug("No pending notifications to process")
                    else:
                        # Calculate unique users processed
                        unique_users = len(set(key[0] for key in seen_user_product_global))
                        logger.info(
                            f"Finished processing all notification batches. "
                            f"Unique users: {unique_users}, "
                            f"Notifications sent: {total_sent_count}, "
                            f"Notifications attempted: {total_notifications_attempted}"
                        )
                    return {
                        "sent": total_sent_count,
                        "attempted": total_notifications_attempted,
                        "rate_limited": len(rate_limited_users),
                        "duplicates_removed": total_duplicates_removed,
                        "per_bot_sent": per_bot_sent
                    }

                total_notifications_attempted += len(pending)
                logger.info(
                    f"Processing batch {batch_number}: claimed {len(pending)} pending notifications "
                    f"(token: {claim_token[:8]}...)"
                )

                # Deduplicate by (user_id, product_id) across all batches
                deduplicated = []
                duplicate_skip_count = 0
                duplicates_to_remove = []

                for notification in pending:
                    user_id = notification["user_id"]
                    product_id = notification["product_id"]
                    key = (user_id, product_id)

                    if key in seen_user_product_global:
                        duplicate_skip_count += 1
                        duplicates_to_remove.append(notification["id"])
                        logger.debug(f"Duplicate detected: user={user_id}, product={product_id}, notification_id={notification['id']}")
                        continue

                    seen_user_product_global.add(key)
                    deduplicated.append(notification)

                if duplicate_skip_count:
                    total_duplicates_removed += duplicate_skip_count
                    logger.warning(f"Found {duplicate_skip_count} duplicate notifications in batch {batch_number} - removing from queue")
                    removal_failures = 0
                    for duplicate_id in duplicates_to_remove:
                        try:
                            await self._remove_duplicate_notification(duplicate_id)
                        except Exception as e:
                            removal_failures += 1
                            logger.error(f"Failed to remove duplicate notification {duplicate_id}: {e}")

                    if removal_failures > 0:
                        logger.error(f"Failed to remove {removal_failures} out of {duplicate_skip_count} duplicate notifications")
                    else:
                        logger.info(f"Successfully removed all {duplicate_skip_count} duplicate notifications")

                if not deduplicated:
                    # All notifications in this batch were duplicates
                    if len(pending) == PENDING_NOTIFICATIONS_BATCH_LIMIT:
                        await asyncio.sleep(2)
                        continue
                    return {
                        "sent": total_sent_count,
                        "attempted": total_notifications_attempted,
                        "rate_limited": len(rate_limited_users),
                        "duplicates_removed": total_duplicates_removed,
                        "per_bot_sent": per_bot_sent
                    }

                # Bot-first grouping: group ALL notifications by bot_username BEFORE processing
                # Bulk fetch bot usernames — single DB query instead of N sequential queries
                all_user_ids = list({n["user_id"] for n in deduplicated})
                bot_usernames_map = await self.db.get_bot_usernames_for_users(all_user_ids)

                notifications_by_bot = {}
                for notification in deduplicated:
                    user_id = notification["user_id"]
                    bot_username = bot_usernames_map.get(user_id)
                    # Normalize: None means primary bot
                    if bot_username not in notifications_by_bot:
                        notifications_by_bot[bot_username] = []
                    notifications_by_bot[bot_username].append(notification)

                # Calculate unique users in this batch
                unique_users_in_batch = len(set(n["user_id"] for n in deduplicated))
                bot_summary = ', '.join(
                    f'@{b or "primary"} ({len(n)})' for b, n in notifications_by_bot.items()
                )
                logger.info(
                    f"Batch {batch_number}: grouped into {len(notifications_by_bot)} bot(s): {bot_summary}. "
                    f"Unique users in batch: {unique_users_in_batch}, notifications: {len(deduplicated)}"
                )

                # Process bots concurrently but cap parallelism via semaphore for fair scheduling
                async def _run_bot_notifications(bot_username, notifications):
                    async with self._bot_worker_semaphore:
                        return await self._process_bot_notifications(bot_username, notifications, rate_limited_users)

                tasks = [
                    _run_bot_notifications(bot_username, bot_notifications)
                    for bot_username, bot_notifications in notifications_by_bot.items()
                ]

                results = await asyncio.gather(*tasks, return_exceptions=True)

                # Aggregate results
                sent_count = 0
                bot_keys = list(notifications_by_bot.keys())
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        bot_name = bot_keys[i]
                        logger.error(f"Error processing notifications for bot @{bot_name or 'primary'}: {result}")
                    else:
                        sent_count += result
                        if result > 0:
                            bot_label = bot_keys[i] or "primary"
                            per_bot_sent[bot_label] = per_bot_sent.get(bot_label, 0) + result

                total_sent_count += sent_count
                logger.info(
                    f"Batch {batch_number} complete: Sent {sent_count} notifications successfully "
                    f"(total sent: {total_sent_count}, total attempted: {total_notifications_attempted})"
                )

                # Checkpoint WAL after every 5 batches to prevent WAL file growth
                if batch_number % 5 == 0:
                    await self.db.checkpoint_wal()

                # Brief pause between batches to avoid overwhelming the system
                if len(pending) == PENDING_NOTIFICATIONS_BATCH_LIMIT:  # If we got a full batch, there might be more
                    await asyncio.sleep(2)

        except Exception as e:
            logger.error(f"Error processing notification queue: {e}", exc_info=True)
            # Release all claims on error so notifications can be retried
            try:
                if 'claim_token' in locals():
                    released = await self.db.release_notification_claim(claim_token=claim_token)
                    logger.info(f"Released {released} notifications after error (token: {claim_token[:8]}...)")
            except Exception as release_error:
                logger.error(f"Failed to release claims after error: {release_error}")
            # Return stats even on error
            return {
                "sent": total_sent_count if 'total_sent_count' in locals() else 0,
                "attempted": total_notifications_attempted if 'total_notifications_attempted' in locals() else 0,
                "rate_limited": len(rate_limited_users) if 'rate_limited_users' in locals() else 0,
                "duplicates_removed": total_duplicates_removed if 'total_duplicates_removed' in locals() else 0,
                "per_bot_sent": per_bot_sent if 'per_bot_sent' in locals() else {}
            }

    async def _process_bot_notifications(
        self,
        bot_username: Optional[str],
        notifications: List[dict],
        rate_limited_users: set
    ) -> int:
        """
        Process notifications for a specific bot. Handles deduplication within the bot's scope,
        rate limiting per user, and staggered delivery for anti-spam.
        
        Args:
            bot_username: The bot username to send via (None for primary bot)
            notifications: List of notification dicts assigned to this bot
            rate_limited_users: Shared set tracking rate-limited users (updated in place)
            
        Returns:
            Number of successfully sent notifications
        """
        # Get the appropriate bot application
        bot_apps = self._get_bot_applications()
        if not bot_apps:
            logger.warning("No bot applications available for delivery")
            return 0
        
        bot_app = get_bot_by_username(bot_username) if bot_username else bot_apps[0]
        if not bot_app:
            logger.warning(f"Bot @{bot_username} not found, using primary bot")
            bot_app = bot_apps[0]
        
        actual_bot_name = get_bot_username(bot_app)
        logger.info(f"[Bot @{actual_bot_name}] Processing {len(notifications)} notifications")
        
        # Group notifications by user for rate limiting
        user_notifications = {}
        for notification in notifications:
            user_id = notification["user_id"]
            if user_id not in user_notifications:
                user_notifications[user_id] = []
            user_notifications[user_id].append(notification)
        
        # Check if all users in this bot's batch were already rate-limited
        if user_notifications and all(uid in rate_limited_users for uid in user_notifications.keys()):
            logger.info(f"[Bot @{actual_bot_name}] All {len(user_notifications)} users were previously rate-limited, skipping")
            return 0
        
        # Filter users who haven't exceeded rate limits
        # Bulk fetch rate limit counts — single DB query instead of N sequential queries
        non_rate_limited_ids = [uid for uid in user_notifications if uid not in rate_limited_users]
        recent_counts = await self.db.get_recent_notifications_count_bulk(non_rate_limited_ids, minutes=60)

        eligible_users = []
        for user_id, user_notifs in user_notifications.items():
            # Skip if already known to be rate-limited
            if user_id in rate_limited_users:
                continue
            
            recent_count = recent_counts.get(user_id, 0)
            
            if recent_count >= MAX_NOTIFICATIONS_PER_HOUR:
                logger.debug(f"[Bot @{actual_bot_name}] User {user_id} reached rate limit, skipping")
                rate_limited_users.add(user_id)
                continue
            
            # Calculate how many more notifications we can send to this user
            remaining = MAX_NOTIFICATIONS_PER_HOUR - recent_count
            
            # Add user with their limited notifications
            eligible_users.append((user_id, user_notifs[:remaining]))
        
        if not eligible_users:
            logger.info(f"[Bot @{actual_bot_name}] No eligible users after rate limiting")
            return 0
        
        # Implement staggered delivery by groups
        sent_count = 0
        
        if ENABLE_STAGGERED_DELIVERY and len(eligible_users) >= STAGGER_GROUP_SIZE:
            estimated_seconds = (len(eligible_users) / STAGGER_GROUP_SIZE) * STAGGER_INTERVAL_SECONDS
            logger.info(
                f"[Bot @{actual_bot_name}] Using staggered delivery: {len(eligible_users)} users "
                f"in groups of {STAGGER_GROUP_SIZE} with {STAGGER_INTERVAL_SECONDS}s interval "
                f"(est. {estimated_seconds:.0f}s / {estimated_seconds/60:.1f}min)"
            )
            
            # Split users into groups
            user_groups = []
            for i in range(0, len(eligible_users), STAGGER_GROUP_SIZE):
                user_groups.append(eligible_users[i:i + STAGGER_GROUP_SIZE])
            
            # Process each group with staggered delay
            for group_idx, user_group in enumerate(user_groups):
                logger.info(f"[Bot @{actual_bot_name}] Processing group {group_idx + 1}/{len(user_groups)} with {len(user_group)} users")
                
                # Collect all notifications for this group
                group_batch = []
                for user_id, user_notifs in user_group:
                    group_batch.extend(user_notifs)
                
                # Send notifications in smaller batches within the group
                for i in range(0, len(group_batch), NOTIFICATION_BATCH_SIZE):
                    batch = group_batch[i:i + NOTIFICATION_BATCH_SIZE]
                    sent = await self._send_bot_notification_batch(bot_app, batch)
                    sent_count += sent
                    
                    # Small delay between batches within a group
                    if i + NOTIFICATION_BATCH_SIZE < len(group_batch):
                        await asyncio.sleep(BATCH_DELAY_SECONDS)
                
                # Stagger delay before next group (except for last group)
                if group_idx < len(user_groups) - 1:
                    logger.info(f"[Bot @{actual_bot_name}] Waiting {STAGGER_INTERVAL_SECONDS}s before next group...")
                    await asyncio.sleep(STAGGER_INTERVAL_SECONDS)
        else:
            # Standard batch processing without staggering
            logger.info(f"[Bot @{actual_bot_name}] Using standard batch processing ({len(eligible_users)} users)")
            batch = []
            
            for user_id, user_notifs in eligible_users:
                batch.extend(user_notifs)
                
                # Send in batches to avoid overwhelming Telegram
                if len(batch) >= NOTIFICATION_BATCH_SIZE:
                    sent = await self._send_bot_notification_batch(bot_app, batch)
                    sent_count += sent
                    batch = []
                    await asyncio.sleep(BATCH_DELAY_SECONDS)
            
            # Send remaining batch
            if batch:
                sent = await self._send_bot_notification_batch(bot_app, batch)
                sent_count += sent
        
        logger.info(
            f"[Bot @{actual_bot_name}] Completed: sent {sent_count} notifications "
            f"to {len(user_notifications)} unique users"
        )
        return sent_count
    
    async def _send_bot_notification_batch(
        self,
        bot_app: Application,
        notifications: List[dict]
    ) -> int:
        """
        Send a batch of notifications through a specific pre-determined bot.
        No cross-bot grouping needed since the bot is already predetermined.
        
        Args:
            bot_app: The bot application to send via
            notifications: List of notification dicts to send
            
        Returns:
            Number of successfully sent notifications
        """
        sent_count = 0
        bot_name = get_bot_username(bot_app)
        limiter = self._get_bot_rate_limiter(bot_name)
        
        logger.info(f"Sending {len(notifications)} notifications via bot @{bot_name}")
        
        for notification in notifications:
            try:
                await limiter.acquire()
                success = await self._send_single_notification(bot_app, notification)
                if success:
                    sent_count += 1
            except Exception as e:
                logger.error(f"Error sending notification {notification['id']}: {e}")
            await asyncio.sleep(0)
        
        return sent_count
    
    async def _send_notification_batch_multibot(
        self,
        notifications: List[dict]
    ) -> int:
        """
        Send a batch of notifications using multi-bot delivery.
        Each notification is sent via the bot that the user started.
        Returns the number of successfully sent notifications.
        """
        sent_count = 0
        
        # Get all available bot applications (cached)
        bot_apps = self._get_bot_applications()
        
        if not bot_apps:
            logger.warning("No bot applications available for multi-bot delivery, falling back to single bot")
            # This shouldn't happen in production, but handle gracefully
            return 0
        
        # Group notifications by user's bot_username for efficient delivery
        users_by_bot: Dict[str, List[dict]] = {}
        
        for notification in notifications:
            user_id = notification["user_id"]
            
            # Get user's bot_username from database using the dedicated method
            # This is more efficient than fetching the entire user object
            bot_username = await self.db.get_bot_username_for_user(user_id)
            
            # If bot_username is None, it means either:
            # 1. User doesn't exist (deleted) - send will fail with user not found error (handled at line 522)
            # 2. User exists but has no bot assigned (NULL bot_username) - send via primary bot
            # In both cases, we group them together and send via primary bot
            if bot_username not in users_by_bot:
                users_by_bot[bot_username] = []
            users_by_bot[bot_username].append(notification)
        
        # Send notifications through each bot
        for bot_username, bot_notifications in users_by_bot.items():
            # Get the appropriate bot application
            bot_app = get_bot_by_username(bot_username) if bot_username else bot_apps[0]
            
            if not bot_app:
                logger.warning(f"Bot @{bot_username} not found, using primary bot")
                bot_app = bot_apps[0]
            
            logger.info(f"Sending {len(bot_notifications)} notifications via bot @{get_bot_username(bot_app)}")
            
            # Send each notification
            for notification in bot_notifications:
                try:
                    success = await self._send_single_notification(bot_app, notification)
                    if success:
                        sent_count += 1
                except Exception as e:
                    logger.error(f"Error sending notification {notification['id']}: {e}")
        
        return sent_count
    
    async def _send_single_notification(
        self,
        bot_app: Application,
        notification: dict
    ) -> bool:
        """
        Send a single notification through the specified bot.
        Returns True if sent successfully, False otherwise.
        """
        try:
            user_id = notification["user_id"]
            product_id = notification["product_id"]
            notification_id = notification["id"]
            
            # Get product details
            product = await self.db.get_product(product_id)
            
            if not product:
                # Product deleted, mark notification as sent
                await self.db.mark_notification_sent(notification_id)
                return False
            
            # Get user's language preference
            user_lang = await self.db.get_user_language(user_id)
            
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
            
            # Prepare notification text (will be used as caption or fallback)
            message_text = (
                f"{emojis.BELL} **New Product Available!**\n\n"
                f"{emojis.MENU} Category: {category_text}\n"
                f"{emojis.INFO} {caption_preview}\n\n"
                f"Use /menu to browse the catalog!"
            )
            
            # Create inline keyboard with view product button
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton(f"{emojis.VIEW} View Product", callback_data=f"product|{product_id}")],
                [InlineKeyboardButton(f"{emojis.MENU} View Catalog", callback_data="categories")],
                [InlineKeyboardButton(f"{emojis.MUTED_BELL} Unsubscribe", callback_data="unsubscribe_notifications")]
            ])
            
            # Try to send with media preview (first available media file)
            media_sent = False
            file_id = product.get("file_id")
            file_type = product.get("file_type")
            
            # Track media sending metrics
            bot_specific_id_attempted = False
            bot_specific_id_success = False
            used_original_file_id = False
            
            if file_id and file_type:
                # Handle multi-bot scenarios - get bot-specific file ID if needed
                product_bot = product.get("bot_username")
                current_bot = bot_app.bot.username
                file_id_to_use = file_id
                
                # Check if we need bot-specific file ID
                # When product_bot and current_bot are both non-None and match (case-insensitive),
                # we can use the original file_id. Otherwise, we need to get a bot-specific file ID.
                need_bot_specific_id = (
                    product_bot is None or 
                    current_bot is None or 
                    (product_bot.lower() != current_bot.lower())
                )
                
                if need_bot_specific_id:
                    bot_specific_id_attempted = True
                    logger.debug(
                        f"[Media Notification] Attempting bot-specific file ID: user={user_id}, "
                        f"product_bot={product_bot}, current_bot={current_bot}"
                    )
                    
                    # get_bot_specific_file_id expects a context with a .bot attribute
                    # bot_app is an Application which has a .bot attribute
                    bot_specific_id = await get_bot_specific_file_id(
                        bot_app,
                        product["chat_id"],
                        product["message_id"],
                        file_type,
                        file_index=0
                    )
                    if bot_specific_id:
                        file_id_to_use = bot_specific_id
                        bot_specific_id_success = True
                        logger.debug(
                            f"[Media Notification] Using bot-specific file ID: user={user_id}, "
                            f"product={product_id}, type={file_type}"
                        )
                    else:
                        logger.warning(
                            f"[Media Notification] Bot-specific file ID failed, will try original file_id: "
                            f"user={user_id}, product={product_id}, type={file_type}, "
                            f"chat_id={product['chat_id']}, msg_id={product['message_id']}"
                        )
                        # Will fall through to try original file_id
                        # Note: used_original_file_id will be set to True only if media is sent successfully
                else:
                    logger.debug(
                        f"[Media Notification] Using original file ID (same bot): user={user_id}, "
                        f"product={product_id}, bot={current_bot}"
                    )
                
                # Try to send media with the notification text as caption
                try:
                    media_sent = await self._send_media_notification(
                        bot_app, user_id, file_type, file_id_to_use,
                        message_text, keyboard, parse_mode="Markdown"
                    )
                    if media_sent:
                        # Set used_original_file_id if we successfully sent with original file_id
                        if bot_specific_id_attempted and not bot_specific_id_success and file_id_to_use == file_id:
                            used_original_file_id = True
                        logger.debug(
                            f"[Media Notification] Successfully sent media: user={user_id}, "
                            f"product={product_id}, type={file_type}, "
                            f"bot_specific_attempted={bot_specific_id_attempted}, "
                            f"bot_specific_success={bot_specific_id_success}, "
                            f"used_original={used_original_file_id}"
                        )
                except BadRequest as e:
                    error_msg = str(e)
                    # Handle markdown parse errors by retrying without parse_mode
                    if self._is_markdown_parse_error(e):
                        logger.warning(
                            f"[Media Notification] Markdown parse error, retrying without parse_mode: "
                            f"user={user_id}, error={e}"
                        )
                        try:
                            media_sent = await self._send_media_notification(
                                bot_app, user_id, file_type, file_id_to_use,
                                message_text, keyboard, parse_mode=None
                            )
                        except Exception as retry_error:
                            logger.warning(
                                f"[Media Notification] Failed even without parse_mode: "
                                f"user={user_id}, error={retry_error}"
                            )
                            media_sent = False
                    elif "wrong file identifier" in error_msg.lower() or "file_id" in error_msg.lower():
                        # File ID is invalid/expired
                        logger.error(
                            f"[Media Notification] Invalid/expired file_id: user={user_id}, "
                            f"product={product_id}, type={file_type}, file_id={get_file_id_preview(file_id_to_use)}, "
                            f"bot_specific_attempted={bot_specific_id_attempted}, "
                            f"bot_specific_success={bot_specific_id_success}, error={e}"
                        )
                        media_sent = False
                    else:
                        # Other BadRequest errors, log and fallback to text
                        logger.warning(
                            f"[Media Notification] BadRequest error: user={user_id}, "
                            f"product={product_id}, type={file_type}, error={e}"
                        )
                        media_sent = False
                except Exception as e:
                    # Any other error sending media, log and fallback to text
                    logger.warning(
                        f"[Media Notification] Unexpected error: user={user_id}, "
                        f"product={product_id}, type={file_type}, error={e}"
                    )
                    media_sent = False
            
            # Fallback to text-only notification if media failed or not available
            if not media_sent:
                if file_id and file_type:
                    logger.info(
                        f"[Media Notification] Falling back to text-only notification: user={user_id}, "
                        f"product={product_id}, had_media=True, "
                        f"bot_specific_attempted={bot_specific_id_attempted}, "
                        f"bot_specific_success={bot_specific_id_success}"
                    )
                else:
                    logger.debug(
                        f"[Media Notification] Sending text-only (no media available): user={user_id}, product={product_id}"
                    )
                
                # Use safe_send_message which handles parse errors and user cleanup
                result = await safe_send_message(
                    bot=bot_app.bot,
                    chat_id=user_id,
                    text=message_text,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
                
                # If safe_send_message returns None, there was a failure
                # The safe wrapper already handles user deletion for permanent failures
                # We only mark as sent to prevent retries if user no longer exists
                if result is None:
                    # Check if user still exists - if not, they were deleted (permanent failure)
                    user_exists = await self.db.user_exists(user_id)
                    if not user_exists:
                        # User was deleted, mark as sent to prevent retries
                        await self.db.mark_notification_sent(notification_id)
                    # Otherwise, don't mark as sent - allow retry for temporary failures
                    return False
            
            # Mark as sent
            await self.db.mark_notification_sent(notification_id)
            
            logger.debug(f"Sent notification to user {user_id} for product {product_id} via bot @{get_bot_username(bot_app)}")
            return True
            
        except Forbidden as e:
            # User blocked the bot, disable notifications
            error_msg = str(e).lower()
            if "chat not found" in error_msg or "user not found" in error_msg:
                logger.warning(f"User {user_id} chat/account not found (deleted or never started bot), disabling notifications and deleting user")
                # User's account was deleted or never started the bot - remove completely
                await self.db.delete_user(user_id)
            else:
                logger.info(f"User {user_id} blocked the bot, disabling notifications")
                await self.db.set_user_notifications(user_id, False)
            await self.db.mark_notification_sent(notification_id)
            return False
            
        except TelegramError as e:
            error_msg = str(e).lower()
            # Check for permanent failure conditions
            if "chat not found" in error_msg or "user not found" in error_msg or "user is deactivated" in error_msg:
                logger.warning(f"User {user_id} permanently unreachable (error: {e}), deleting user")
                await self.db.delete_user(user_id)
                await self.db.mark_notification_sent(notification_id)
                return False
            elif "bot was blocked by the user" in error_msg:
                logger.info(f"User {user_id} blocked the bot, disabling notifications")
                await self.db.set_user_notifications(user_id, False)
                await self.db.mark_notification_sent(notification_id)
                return False
            else:
                logger.error(f"Telegram error sending notification to {user_id}: {e}")
                # Don't mark as sent, will retry later for temporary errors
                return False
            
        except Exception as e:
            logger.error(f"Unexpected error sending notification to {user_id}: {e}", exc_info=True)
            # Don't mark as sent for unexpected errors - they should be investigated
            # Note: Messages remain in queue and will be retried when:
            #  - Product categorization triggers notification queue processing
            #  - Admin manually triggers notification processing (future feature)
            # This ensures we don't lose messages due to transient issues
            return False
    
    async def send_custom_message_to_user(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        user_id: int,
        message_text: str
    ):
        """
        Send a custom message to a specific user with rate limiting.
        Only runs on the primary bot instance to avoid duplicate sends.
        """
        # Check if this is the primary bot instance
        if not self._is_primary_instance(context):
            logger.info(f"Skipping custom message to user {user_id} - not primary instance")
            return False
        
        try:
            # Check if user is blocked
            is_blocked = await self.db.is_user_blocked(user_id)
            if is_blocked:
                logger.warning(f"Cannot send message to blocked user {user_id}")
                return False
            
            # Queue the message
            await self.db.queue_custom_message(user_id, message_text)
            
            # Process the queue
            await self.process_custom_message_queue(context)
            
            return True
            
        except Exception as e:
            logger.error(f"Error sending custom message to user {user_id}: {e}", exc_info=True)
            return False
    
    async def broadcast_custom_message(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        message_text: str,
        exclude_blocked: bool = True,
        admin_user_id: Optional[int] = None
    ):
        """
        Broadcast a custom message to all users with rate limiting.
        Only runs on the primary bot instance to avoid duplicate queuing.
        Returns a dictionary with delivery statistics.
        
        Args:
            context: Bot context
            message_text: Message to broadcast
            exclude_blocked: Whether to exclude blocked users
            admin_user_id: Admin user ID to send completion summary to
            
        Returns:
            dict with keys: queued, sent, failed, blocked, not_found, rate_limited, markdown_errors
        """
        # Check if this is the primary bot instance
        if not self._is_primary_instance(context):
            logger.info(f"Skipping broadcast - not primary instance")
            return {"queued": 0, "sent": 0, "failed": 0}
        
        try:
            # Get all users
            all_users = await self.db.get_all_users()
            
            if not all_users:
                logger.debug("No users to broadcast to")
                return {"queued": 0, "sent": 0, "failed": 0}
            
            logger.info(f"Broadcasting custom message to {len(all_users)} users")
            
            # Queue messages for all non-blocked users
            queued = 0
            skipped_blocked = 0
            skipped_admin = 0
            
            from utils.helpers import get_admin_ids
            admin_ids = get_admin_ids()
            
            for user in all_users:
                user_id = user["user_id"]
                
                # Skip blocked users if requested
                if exclude_blocked and user.get("is_blocked", 0) == 1:
                    skipped_blocked += 1
                    continue
                
                # Skip admin users
                if user_id in admin_ids:
                    skipped_admin += 1
                    continue
                
                await self.db.queue_custom_message(user_id, message_text)
                queued += 1
            
            logger.info(f"Queued {queued} custom messages (skipped {skipped_blocked} blocked, {skipped_admin} admins)")
            
            # Process the queue and get statistics
            stats = await self.process_custom_message_queue(context)
            stats["queued"] = queued
            stats["skipped_blocked"] = skipped_blocked
            stats["skipped_admin"] = skipped_admin
            
            # Send summary to admin if requested
            if admin_user_id and context.bot:
                await self._send_admin_summary(context.bot, admin_user_id, stats, "broadcast")
            
            return stats
            
        except Exception as e:
            logger.error(f"Error broadcasting custom message: {e}", exc_info=True)
            return {"queued": 0, "sent": 0, "failed": 0, "error": str(e)}
    
    async def process_custom_message_queue(self, context: ContextTypes.DEFAULT_TYPE):
        """
        Process pending custom messages from the queue with rate limiting.
        Uses bot-first grouping: messages are grouped by bot_username BEFORE processing,
        then each bot's messages are processed simultaneously via asyncio.gather.
        Only runs on the primary bot instance to avoid duplicate processing.
        Returns statistics about the delivery.
        Processes all pending messages in batches until the queue is empty.
        """
        # Check if this is the primary bot instance
        if not self._is_primary_instance(context):
            logger.info(f"Skipping custom message queue processing - not primary instance")
            return {"sent": 0, "failed": 0}
        
        # Initialize statistics
        stats = {
            "sent": 0,
            "failed": 0,
            "blocked": 0,
            "not_found": 0,
            "rate_limited": 0,
            "markdown_errors": 0,
            "unexpected_errors": 0
        }
        
        try:
            # Track rate-limited users to prevent double-counting stats within this run
            # Note: Rate-limited messages stay in queue and will be processed in next scheduled run
            rate_limited_users = set()
            total_fetched = 0
            batch_number = 0
            
            # Loop until all pending messages are processed or no progress can be made
            while True:
                batch_number += 1
                
                pending = await self.db.get_pending_custom_messages(limit=100)
                
                if not pending:
                    break
                
                total_fetched += len(pending)
                logger.info(f"Processing batch {batch_number} of {len(pending)} pending custom messages (total fetched: {total_fetched})")
                
                # Bot-first grouping: bulk-fetch all bot_usernames then group BEFORE processing
                messages_by_bot = {}
                user_ids = [message["user_id"] for message in pending]
                bot_usernames_map = await self.db.get_bot_usernames_for_users(user_ids)
                for message in pending:
                    user_id = message["user_id"]
                    bot_username = bot_usernames_map.get(user_id)
                    if bot_username not in messages_by_bot:
                        messages_by_bot[bot_username] = []
                    messages_by_bot[bot_username].append(message)
                
                logger.info(f"Batch {batch_number}: grouped into {len(messages_by_bot)} bot(s)")
                
                # Process bots concurrently but cap parallelism via semaphore for fair scheduling
                async def _run_bot_custom(bot_username, messages):
                    async with self._bot_worker_semaphore:
                        return await self._process_bot_custom_messages(bot_username, messages, stats, rate_limited_users)

                tasks = [
                    _run_bot_custom(bot_username, bot_messages)
                    for bot_username, bot_messages in messages_by_bot.items()
                ]
                
                results = await asyncio.gather(*tasks, return_exceptions=True)
                
                # Check results for errors
                batch_has_messages = False
                for i, result in enumerate(results):
                    if isinstance(result, Exception):
                        bot_name = list(messages_by_bot.keys())[i]
                        logger.error(f"Error processing custom messages for bot @{bot_name or 'primary'}: {result}")
                    elif result:  # result is True if any messages were processable
                        batch_has_messages = True
                
                # If no messages were actually processable (all blocked or rate-limited),
                # break to avoid infinite loop within this run
                if not batch_has_messages:
                    logger.info(f"No more messages can be sent in this run (all blocked or rate-limited)")
                    stats["remaining_unprocessed"] = await self.db.count_pending_custom_messages()
                    break
            
            logger.info(f"Custom message queue processed: {stats['sent']} sent, {stats['failed']} failed, "
                       f"{stats['blocked']} blocked, {stats['not_found']} not found, "
                       f"{stats['rate_limited']} rate limited, {stats['markdown_errors']} markdown errors")
            
            return stats
            
        except Exception as e:
            logger.error(f"Error processing custom message queue: {e}", exc_info=True)
            stats["unexpected_errors"] += 1
            return stats
    
    async def _process_bot_custom_messages(
        self,
        bot_username: Optional[str],
        messages: List[dict],
        stats: Dict[str, int],
        rate_limited_users: set
    ) -> bool:
        """
        Process custom messages for a specific bot. Handles blocked user checks,
        rate limiting per user, and batched delivery.
        
        Args:
            bot_username: The bot username to send via (None for primary bot)
            messages: List of message dicts assigned to this bot
            stats: Shared stats dictionary (updated in place)
            rate_limited_users: Shared set tracking rate-limited users (updated in place)
            
        Returns:
            True if any messages were processable, False if all blocked/rate-limited
        """
        # Get the appropriate bot application
        bot_apps = self._get_bot_applications()
        if not bot_apps:
            logger.warning("No bot applications available for delivery")
            stats["failed"] += len(messages)
            return False
        
        bot_app = get_bot_by_username(bot_username) if bot_username else bot_apps[0]
        if not bot_app:
            logger.warning(f"Bot @{bot_username} not found, using primary bot")
            bot_app = bot_apps[0]
        
        actual_bot_name = get_bot_username(bot_app)
        
        # Group messages by user for rate limiting
        user_messages = {}
        for message in messages:
            user_id = message["user_id"]
            if user_id not in user_messages:
                user_messages[user_id] = []
            user_messages[user_id].append(message)
        
        # Build batch of eligible messages
        batch = []
        batch_has_messages = False
        
        for user_id, user_msgs in user_messages.items():
            # Check if user is blocked
            is_blocked = await self.db.is_user_blocked(user_id)
            if is_blocked:
                # Mark as sent to remove from queue (blocked users won't receive messages)
                for msg in user_msgs:
                    await self.db.mark_custom_message_sent(msg["id"])
                stats["blocked"] += len(user_msgs)
                continue
            
            # Check if user was previously rate-limited in this run
            if user_id in rate_limited_users:
                continue
            
            # Check rate limit for this user
            recent_count = await self.db.get_recent_custom_messages_count(user_id, minutes=60)
            
            if recent_count >= MAX_CUSTOM_MESSAGES_PER_HOUR:
                logger.debug(f"[Bot @{actual_bot_name}] User {user_id} reached custom message rate limit, skipping")
                stats["rate_limited"] += len(user_msgs)
                rate_limited_users.add(user_id)
                continue
            
            # Calculate how many more messages we can send to this user
            remaining = MAX_CUSTOM_MESSAGES_PER_HOUR - recent_count
            
            for message in user_msgs[:remaining]:
                batch.append(message)
                batch_has_messages = True
                
                # Send in batches to avoid overwhelming Telegram
                if len(batch) >= CUSTOM_MESSAGE_BATCH_SIZE:
                    await self._send_bot_custom_message_batch(bot_app, batch, stats)
                    batch = []
                    await asyncio.sleep(CUSTOM_MESSAGE_DELAY_SECONDS)
        
        # Send remaining batch
        if batch:
            await self._send_bot_custom_message_batch(bot_app, batch, stats)
        
        return batch_has_messages
    
    async def _send_bot_custom_message_batch(
        self,
        bot_app: Application,
        messages: List[dict],
        stats: Dict[str, int]
    ) -> None:
        """
        Send a batch of custom messages through a specific pre-determined bot.
        No cross-bot grouping needed since the bot is already predetermined.
        
        Args:
            bot_app: The bot application to send via
            messages: List of message dicts to send
            stats: Shared stats dictionary (updated in place)
        """
        bot_name = get_bot_username(bot_app)
        limiter = self._get_bot_rate_limiter(bot_name)
        logger.info(f"Sending {len(messages)} custom messages via bot @{bot_name}")
        
        for message in messages:
            await limiter.acquire()
            markdown_error_occurred = False
            try:
                user_id = message["user_id"]
                message_text = message["message_text"]
                message_id = message["id"]
                
                # Send custom message using the bot application
                try:
                    await bot_app.bot.send_message(
                        chat_id=user_id,
                        text=message_text,
                        parse_mode="Markdown"
                    )
                except BadRequest as e:
                    # Handle markdown parse errors by retrying without parse_mode
                    if self._is_markdown_parse_error(e):
                        logger.warning(f"Markdown parse error for custom message to user {user_id}, retrying without parse_mode: {e}")
                        markdown_error_occurred = True
                        stats["markdown_errors"] += 1
                        await bot_app.bot.send_message(
                            chat_id=user_id,
                            text=message_text
                        )
                    else:
                        # Re-raise other BadRequest errors to be handled below
                        raise
                
                # Mark as sent
                await self.db.mark_custom_message_sent(message_id)
                stats["sent"] += 1
                
                logger.debug(f"Sent custom message to user {user_id} via bot @{bot_name}")
                
            except Forbidden as e:
                # User blocked the bot or chat not found
                error_msg = str(e).lower()
                if "chat not found" in error_msg or "user not found" in error_msg:
                    logger.warning(f"User {user_id} chat/account not found (deleted or never started bot), deleting user")
                    await self.db.delete_user(user_id)
                    stats["not_found"] += 1
                else:
                    logger.info(f"User {user_id} blocked the bot, marking as blocked")
                    await self.db.block_user(user_id)
                    stats["blocked"] += 1
                await self.db.mark_custom_message_sent(message_id)
                
            except TelegramError as e:
                error_msg = str(e).lower()
                # Check for permanent failure conditions
                if "chat not found" in error_msg or "user not found" in error_msg or "user is deactivated" in error_msg:
                    logger.warning(f"User {user_id} permanently unreachable (error: {e}), deleting user")
                    await self.db.delete_user(user_id)
                    await self.db.mark_custom_message_sent(message_id)
                    stats["not_found"] += 1
                elif "bot was blocked by the user" in error_msg:
                    logger.info(f"User {user_id} blocked the bot, marking as blocked")
                    await self.db.block_user(user_id)
                    await self.db.mark_custom_message_sent(message_id)
                    stats["blocked"] += 1
                else:
                    logger.error(f"Telegram error sending custom message to {user_id}: {e}")
                    # Don't mark as sent - temporary error, will be retried
                
            except Exception as e:
                logger.error(f"Unexpected error sending custom message to {user_id}: {e}", exc_info=True)
                # Don't mark as sent for unexpected errors - they should be investigated
                # Messages remain in queue and will be retried on next broadcast/send operation
                stats["unexpected_errors"] += 1
    
    async def _send_admin_summary(self, bot, admin_user_id: int, stats: Dict[str, int], operation: str = "broadcast"):
        """Send a summary message to the admin with delivery statistics."""
        try:
            total_queued = stats.get("queued", 0)
            sent = stats.get("sent", 0)
            failed = stats.get("failed", 0)
            blocked = stats.get("blocked", 0)
            not_found = stats.get("not_found", 0)
            rate_limited = stats.get("rate_limited", 0)
            markdown_errors = stats.get("markdown_errors", 0)
            unexpected_errors = stats.get("unexpected_errors", 0)
            skipped_blocked = stats.get("skipped_blocked", 0)
            skipped_admin = stats.get("skipped_admin", 0)
            
            # Calculate totals
            # Note: 'sent' count includes messages sent successfully with markdown AND
            # messages that had markdown errors but were sent as plain text.
            # markdown_errors is tracked separately to show how many needed the fallback.
            total_permanent_failures = blocked + not_found
            actual_attempted = stats.get("attempted", 0)
            if actual_attempted == 0:
                actual_attempted = sent + total_permanent_failures + unexpected_errors + rate_limited
            remaining_unprocessed = stats.get("remaining_unprocessed", 0)
            per_bot_sent = stats.get("per_bot_sent", {})
            
            # Fetch live pending counts from the database
            try:
                pending_notifications = await self.db.count_pending_notifications()
                pending_custom = await self.db.count_pending_custom_messages()
            except Exception as e:
                logger.warning(f"Failed to fetch pending queue counts for admin summary: {e}")
                pending_notifications = 0
                pending_custom = 0

            # Build summary message
            summary = f"📊 **{operation.title()} Complete**\n\n"
            summary += f"✅ **Successfully Sent:** {sent}\n"
            summary += f"📈 **Total Attempted:** {actual_attempted}\n"
            
            # Show total permanent failures
            if total_permanent_failures > 0:
                summary += f"❌ **Permanent Failures:** {total_permanent_failures}\n"
            
            if total_queued > 0:
                summary += f"\n📝 **Queued:** {total_queued}\n"
            
            if skipped_blocked > 0:
                summary += f"⛔ **Skipped (blocked):** {skipped_blocked}\n"
            
            if skipped_admin > 0:
                summary += f"👨‍💼 **Skipped (admins):** {skipped_admin}\n"
            
            if blocked > 0 or not_found > 0 or rate_limited > 0 or markdown_errors > 0 or unexpected_errors > 0:
                summary += f"\n**Delivery Details:**\n"
                if blocked > 0:
                    summary += f"• Blocked by user: {blocked}\n"
                if not_found > 0:
                    summary += f"• Chat/user not found: {not_found}\n"
                if rate_limited > 0:
                    summary += f"• Rate limited (queued): {rate_limited}\n"
                if markdown_errors > 0:
                    summary += f"• Markdown errors (sent as plain text): {markdown_errors}\n"
                if unexpected_errors > 0:
                    summary += f"• Unexpected errors (will retry): {unexpected_errors}\n"
            
            # Calculate success rate based on actual attempts (including rate-limited and unexpected errors)
            if actual_attempted > 0:
                success_rate = (sent / actual_attempted) * 100
                summary += f"\n**Success Rate:** {success_rate:.1f}%"
            
            # Show delivery rate using queued count as denominator
            if total_queued > 0:
                delivery_rate = (sent / total_queued) * 100
                summary += f"\n📬 **Delivery Rate:** {delivery_rate:.1f}% of {total_queued} queued"
            
            # Warn admin if messages remain pending due to all being blocked/rate-limited
            if remaining_unprocessed > 0:
                summary += f"\n\n⚠️ **{remaining_unprocessed} messages still pending in queue.** Run /broadcast again to continue delivery."

            # Show overall queue depths
            summary += f"\n\n📋 **Queue Remaining:**\n"
            summary += f"• Notifications: {pending_notifications}\n"
            summary += f"• Custom messages: {pending_custom}\n"

            # Per-bot breakdown (top 5 bots by pending count)
            try:
                if operation == "broadcast":
                    bot_pending = await self.db.count_pending_custom_messages_by_bot(top_n=5)
                else:
                    bot_pending = await self.db.count_pending_notifications_by_bot(top_n=5)
                if bot_pending:
                    summary += "\n**Pending by Bot:**\n"
                    for entry in bot_pending:
                        label = entry["bot_username"] or "primary"
                        summary += f"• @{label}: {entry['pending']}\n"
            except Exception as e:
                logger.warning(f"Failed to fetch per-bot pending breakdown for admin summary: {e}")

            # Per-bot delivered breakdown from this run's stats
            if per_bot_sent:
                summary += "\n🤖 **Deliveries per Bot:**\n"
                for bot_username, count in per_bot_sent.items():
                    summary += f"• @{bot_username}: {count}\n"
            
            await bot.send_message(
                chat_id=admin_user_id,
                text=summary,
                parse_mode="Markdown"
            )
            logger.info(f"Sent delivery summary to admin {admin_user_id}")
            
        except Exception as e:
            logger.error(f"Failed to send admin summary: {e}", exc_info=True)
