"""
Notification service for sending product notifications to users.
Implements rate limiting to respect Telegram's anti-spam policies.
"""
import logging
import asyncio
import json
import re
from typing import List, Optional
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError, Forbidden

from database import Database
from utils.categories import get_category_display_name, get_subcategory_display_name, NOTIFICATION_EXCLUDED_CATEGORIES
from translations.translator import translate_text_async
from configs.config import Config
from utils.bot_key import get_bot_env_key, get_bot_key_display

logger = logging.getLogger(__name__)

# Rate limiting configuration
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
MAX_RETRIES = 3  # Maximum retry attempts for failed sends
RETRY_BASE_DELAY = 1.0  # Base delay for exponential backoff (seconds)


class NotificationService:
    """Service for managing and sending product notifications."""
    
    # Compiled regex pattern for matching t.me deep-link URLs
    # Matches: https://t.me/{valid_telegram_username}?start={anything}
    # Telegram usernames start with a letter and contain only letters, digits, and underscores
    _TELEGRAM_DEEPLINK_PATTERN = re.compile(r'https://t\.me/[a-zA-Z][a-zA-Z0-9_]*(\?start=[^"\s]*)')
    
    def __init__(self, db: Database):
        self.db = db
    
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
                if 'notification_tasks' not in bot_data:
                    bot_data['notification_tasks'] = []
                logger.debug("[TaskStorage] Using application.bot_data for task storage (resilient)")
                return bot_data['notification_tasks']
        except Exception as e:
            logger.warning(f"[TaskStorage] Could not access application.bot_data: {e}")
        
        # Fallback to context.notification_tasks (less resilient but backwards compatible)
        if not hasattr(context, 'notification_tasks'):
            context.notification_tasks = []
        logger.warning("[TaskStorage] Falling back to context.notification_tasks (ephemeral - may be lost in webhook mode)")
        return context.notification_tasks
    
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
            
            # Check if this is the primary instance (only primary queues notifications)
            is_primary = self._is_primary_instance(context)
            logger.info(f"[Notifications] Primary instance check result: {is_primary}")
            
            if not is_primary:
                logger.info(f"[Notifications] Skipping notification queueing - not primary instance (bot @{bot_username})")
                return
            
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
            
            # Queue notifications for all subscribed users in batch
            # Database-level deduplication prevents duplicate notifications
            queued_count = await self.db.queue_notifications_batch(subscribed_users, product_id)
            
            logger.info(f"[Notifications] Queued {queued_count} notifications for product {product_id} (bot @{bot_username})")
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
                logger.info(f"User {user_id} blocked the bot, disabling notifications")
                await self.db.set_user_notifications(user_id, False)
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
        Send a custom text message to a specific user with rate limiting.
        Text-only - media is not supported for queued/broadcast messages.
        Queues the message and spawns background processing task.
        
        Returns:
            bool: True if message was queued successfully, False otherwise
        """
        try:
            logger.info(f"[Notifications] send_custom_message_to_user: Queueing text message for user {user_id}")
            
            # Check if user is blocked
            is_blocked = await self.db.is_user_blocked(user_id)
            if is_blocked:
                logger.warning(f"[Notifications] Cannot send message to blocked user {user_id}")
                return False
            
            # Queue the message (text-only, media fields are None)
            await self.db.queue_custom_message(
                user_id, 
                message_text,
                media_type=None,
                media_file_id=None
            )
            logger.info(f"[Notifications] Queued custom message for user {user_id}")
            
            # Spawn background task to process the queue
            # Store task in application.bot_data for resilience in webhook deployments
            task = asyncio.create_task(self.process_custom_message_queue(context))
            self._store_task(context, task, f"custom_msg_user_{user_id}")
            
            logger.info(f"[Notifications] Background task created for user {user_id} (task_id={id(task)})")
            
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
            
            # Spawn background task to process the queue across all bots
            # Store task in application.bot_data for resilience in webhook deployments
            task = asyncio.create_task(self.process_custom_message_queue(context, process_all_bots=True))
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
            dict: Summary with {"sent_count": int, "failed_count": int}
        """
        sent_count = 0
        failed_count = 0
        
        try:
            # Get bot username for filtering (skip primary check for processing)
            bot_username = context.bot.username if hasattr(context.bot, 'username') else None
            current_bot_key = get_bot_env_key()
            bot_display = get_bot_key_display(current_bot_key, bot_username)
            
            pending = await self.db.get_pending_custom_messages(limit=100)
            
            if not pending:
                return {"sent_count": 0, "failed_count": 0}
            
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
                return {"sent_count": 0, "failed_count": 0}
            
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
                    # Mark as sent to remove from queue
                    for msg in messages:
                        await self.db.mark_custom_message_sent(msg["id"])
                    failed_count += len(messages)
                    continue
                
                # Check rate limit for this user
                recent_count = await self.db.get_recent_custom_messages_count(user_id, minutes=60)
                
                if recent_count >= MAX_CUSTOM_MESSAGES_PER_HOUR:
                    logger.debug(f"User {user_id} reached custom message rate limit, skipping")
                    failed_count += len(messages)
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
                        batch = []
                        await asyncio.sleep(CUSTOM_MESSAGE_DELAY_SECONDS)
                
                # Mark messages that exceeded quota as sent (failed due to rate limit)
                if len(messages) > remaining:
                    excess_messages = messages[remaining:]
                    for msg in excess_messages:
                        await self.db.mark_custom_message_sent(msg["id"])
                    failed_count += len(excess_messages)
                    logger.debug(f"User {user_id}: {len(excess_messages)} messages skipped due to rate limit")
            
            # Send remaining batch
            if batch:
                result = await self._send_custom_message_batch(context, batch)
                sent_count += result["sent"]
                failed_count += result["failed"]
            
            logger.info(f"[{mode_desc}] Sent {sent_count} custom messages successfully, {failed_count} failed")
            
            return {"sent_count": sent_count, "failed_count": failed_count}
            
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
        Send a single custom message with retry logic.
        
        Args:
            context: Telegram context
            message: Message data dict
            reply_markup: Pre-parsed reply markup (to avoid re-parsing)
            retry_count: Current retry attempt (default: 0, incremented internally during retries)
            
        Returns:
            dict with {"success": bool, "user_id": int, "message_id": int}
        """
        user_id = message["user_id"]
        message_id = message["id"]
        message_text = message.get("message_text", "")
        
        try:
            # Check if user is blocked
            is_blocked = await self.db.is_user_blocked(user_id)
            if is_blocked:
                # Mark as sent to remove from queue
                await self.db.mark_custom_message_sent(message_id)
                return {"success": False, "user_id": user_id, "message_id": message_id, "reason": "blocked"}
            
            # Send text-only message
            await context.bot.send_message(
                chat_id=user_id,
                text=message_text,
                parse_mode="Markdown",
                reply_markup=reply_markup
            )
            
            # Mark as sent
            await self.db.mark_custom_message_sent(message_id)
            logger.debug(f"Sent custom message to user {user_id}")
            
            return {"success": True, "user_id": user_id, "message_id": message_id}
            
        except Forbidden:
            # User blocked the bot, block them in our system
            logger.debug(f"User {user_id} blocked the bot, marking as blocked")
            await self.db.block_user(user_id)
            await self.db.mark_custom_message_sent(message_id)
            return {"success": False, "user_id": user_id, "message_id": message_id, "reason": "forbidden"}
            
        except TelegramError as e:
            error_msg = str(e)
            
            # Check for rate limit errors
            if "Too Many Requests" in error_msg or "retry after" in error_msg.lower():
                if retry_count < MAX_RETRIES:
                    # Exponential backoff with maximum cap to prevent excessive delays
                    delay = min(RETRY_BASE_DELAY * (2 ** retry_count), 30.0)
                    logger.warning(f"Rate limit hit for user {user_id}, retrying in {delay}s (attempt {retry_count + 1}/{MAX_RETRIES})")
                    await asyncio.sleep(delay)
                    return await self._send_single_custom_message(context, message, reply_markup, retry_count + 1)
                else:
                    logger.error(f"Max retries reached for user {user_id}, giving up")
                    # Don't mark as sent, will retry in next batch
                    return {"success": False, "user_id": user_id, "message_id": message_id, "reason": "rate_limit_max_retries"}
            
            # Other Telegram errors
            logger.error(f"Telegram error sending to user {user_id}: {error_msg}")
            # Don't mark as sent, will retry later
            return {"success": False, "user_id": user_id, "message_id": message_id, "reason": "telegram_error"}
            
        except Exception as e:
            logger.exception(f"Error sending custom message to user {user_id}: {str(e)}")
            # Mark as sent to avoid infinite retries for non-recoverable errors
            await self.db.mark_custom_message_sent(message_id)
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
        
        # Count successes and failures
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Exception in concurrent send: {result}")
                failed_count += 1
            elif isinstance(result, dict):
                if result.get("success"):
                    sent_count += 1
                else:
                    failed_count += 1
            else:
                logger.warning(f"Unexpected result type: {type(result)}")
                failed_count += 1
        
        return {"sent": sent_count, "failed": failed_count}
    
    async def notify_subscribers_about_product(self, context: ContextTypes.DEFAULT_TYPE, product_id: int) -> int:
        """
        When a product is categorized, queue subscriber notifications (media + text) via queue_custom_messages_batch
        and spawn a resilient background processing task. Returns the number of queued messages.
        
        This function can be called from any bot instance. All bots may queue and send notifications.
        
        Args:
            context: Telegram context
            product_id: ID of the product to notify about
            
        Returns:
            Number of messages queued
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
            # Row 1: View Product (callback_data to trigger product view handler)
            # Row 2: View Catalog (callback_data to trigger categories/menu)
            # Row 3: Toggle subscription button
            
            # Use callback_data instead of URL deep links so buttons work in the same bot instance
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("👁️ View Product", callback_data=f"product|{product_id}")],
                [InlineKeyboardButton("📋 View Catalog", callback_data="categories")],
                [InlineKeyboardButton("🔕 Unsubscribe", callback_data=f"toggle_notifications|{product_id}")]
            ])
            
            # Serialize keyboard to JSON
            reply_markup_json = json.dumps(keyboard.to_dict())
            
            # Get subscribed users
            subscribed_users = await self.db.get_subscribed_users()
            if not subscribed_users:
                logger.info(f"[NotifySubscribers] No subscribed users for product {product_id}")
                return 0
            
            logger.info(f"[NotifySubscribers] Found {len(subscribed_users)} subscribed users")
            
            # Filter out blocked users and admins
            from utils.helpers import get_admin_ids
            admin_ids = get_admin_ids()
            
            eligible_users = []
            for user_id in subscribed_users:
                # Skip admins
                if user_id in admin_ids:
                    logger.debug(f"[NotifySubscribers] Skipping admin user {user_id}")
                    continue
                
                # Skip blocked users
                is_blocked = await self.db.is_user_blocked(user_id)
                if is_blocked:
                    logger.debug(f"[NotifySubscribers] Skipping blocked user {user_id}")
                    continue
                
                eligible_users.append(user_id)
            
            if not eligible_users:
                logger.info(f"[NotifySubscribers] No eligible users after filtering for product {product_id}")
                return 0
            
            logger.info(f"[NotifySubscribers] {len(eligible_users)} eligible users after filtering")
            
            # Get all users with their last_bot_key to route messages properly
            all_users_data = await self.db.get_all_users()
            user_last_bot_keys = {user["user_id"]: user.get("last_bot_key") for user in all_users_data}
            
            # Build list of (user_id, target_bot_key) for routing
            current_bot_key = get_bot_env_key()
            users_with_targets = []
            for user_id in eligible_users:
                # Use user's preferred bot, fallback to current process bot key
                last_bot_key = user_last_bot_keys.get(user_id)
                target_bot_key = last_bot_key if last_bot_key is not None else current_bot_key
                users_with_targets.append((user_id, target_bot_key))
            
            # Queue text-only messages in batch with inline keyboard and target routing
            # Media is not supported for queued/broadcast messages
            queued_count = await self.db.queue_custom_messages_batch_with_targets(
                users_with_targets=users_with_targets,
                message_text=message_text,
                media_type=None,
                media_file_id=None,
                reply_markup=reply_markup_json
            )
            
            # Log target_bot_key distribution for visibility
            target_distribution = {}
            for _, target_key in users_with_targets:
                target_distribution[target_key] = target_distribution.get(target_key, 0) + 1
            logger.info(f"[NotifySubscribers] Queued {queued_count} messages for product {product_id}. Target distribution: {target_distribution}")
            
            # Spawn background task for processing
            task = asyncio.create_task(self.process_custom_message_queue(context))
            self._store_task(context, task, f"notify_product_{product_id}")
            
            logger.info(f"[NotifySubscribers] Background task spawned for product {product_id}")
            
            return queued_count
            
        except Exception as e:
            logger.error(f"[NotifySubscribers] Error notifying subscribers about product {product_id}: {e}", exc_info=True)
            return 0

