"""
Notification service for sending product notifications to users.
Implements rate limiting to respect Telegram's anti-spam policies.
Supports multi-bot delivery - sends messages through the bot each user started.
"""
import logging
import asyncio
from typing import List, Dict, Optional
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, Application
from telegram.error import TelegramError, Forbidden, BadRequest

from database import Database
from utils.categories import get_category_display_name, get_subcategory_display_name, NOTIFICATION_EXCLUDED_CATEGORIES
from utils.helpers import get_admin_ids
from translations.translator import translate_text_async
from configs.config import Config

logger = logging.getLogger(__name__)

# Rate limiting configuration
MAX_NOTIFICATIONS_PER_HOUR = 5  # Maximum notifications per user per hour
NOTIFICATION_BATCH_SIZE = 10  # Send notifications in batches
BATCH_DELAY_SECONDS = 3  # Delay between batches to avoid hitting rate limits

# Queue processing configuration
PENDING_NOTIFICATIONS_BATCH_LIMIT = 100  # Number of pending notifications to fetch per batch

# Staggered delivery configuration
ENABLE_STAGGERED_DELIVERY = True  # Enable staggered delivery to avoid spam flags
STAGGER_GROUP_SIZE = 10  # Number of users per group for staggered delivery
STAGGER_INTERVAL_SECONDS = 10  # Delay in seconds between groups (reduced from 30 to improve responsiveness)

# Custom message rate limiting
MAX_CUSTOM_MESSAGES_PER_HOUR = 3  # Maximum custom messages per user per hour
CUSTOM_MESSAGE_BATCH_SIZE = 5  # Send custom messages in smaller batches
CUSTOM_MESSAGE_DELAY_SECONDS = 5  # Longer delay for custom messages

# Queue processing safety limits
MAX_QUEUE_PROCESSING_ITERATIONS = 50  # Maximum iterations to prevent infinite loops (50 batches * 100 = 5000 messages max per run)


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
    """Service for managing and sending product notifications."""
    
    def __init__(self, db: Database):
        self.db = db
        self._bot_apps_cache = None  # Cache for bot applications
        self._bot_apps_cache_time = None  # Timestamp of last cache
        self._cache_ttl = 60  # Cache TTL in seconds
        self._current_notification_task = None  # Track background notification task
    
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
        Implements rate limiting to prevent spam.
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
            # (Currently all categories trigger notifications)
            category = product.get("category")
            if category in NOTIFICATION_EXCLUDED_CATEGORIES:
                logger.info(f"Product {product_id} is in excluded category '{category}', skipping notification")
                return
            
            # Get all subscribed users
            subscribed_users = await self.db.get_subscribed_users()
            
            if not subscribed_users:
                logger.debug("No subscribed users to notify")
                return
            
            logger.info(f"Notifying {len(subscribed_users)} users about product {product_id}")
            
            # Check if a notification task is already running
            if self._current_notification_task is not None and not self._current_notification_task.done():
                logger.warning(f"Notification task already running for product {product_id}, skipping duplicate task creation")
                return
            
            # Queue notifications for all subscribed users
            for user_id in subscribed_users:
                await self.db.queue_notification(user_id, product_id)
            
            # Process the notification queue asynchronously (non-blocking)
            # Schedule as a background task so it doesn't block other operations
            # Store task reference to prevent it from being garbage collected
            logger.info(f"Creating background notification task for product {product_id}")
            task = asyncio.create_task(self.process_notification_queue(context))
            self._current_notification_task = task
            
            # Add completion callback to send admin summary and clear task reference
            def on_task_complete(task_obj):
                try:
                    # Get the result (total_sent_count)
                    try:
                        total_sent = task_obj.result()
                        logger.info(f"Notification task completed. Total sent: {total_sent}")
                    except Exception as task_error:
                        # Task execution failed - log it but continue with cleanup
                        # Note: process_notification_queue returns total_sent_count even in exception handler,
                        # so we'll get the count of messages sent before the failure occurred
                        logger.error(f"Notification task failed with error: {task_error}", exc_info=True)
                        total_sent = 0  # Default to 0 if result cannot be retrieved
                    
                    # Send admin summary to first admin
                    # Note: Using first admin to avoid spamming all admins with every notification run.
                    # Future enhancement: Could implement rotation or configuration for which admin(s) to notify.
                    admin_ids = get_admin_ids()
                    if admin_ids and total_sent > 0:  # Only send if admins exist and messages were sent
                        admin_id = admin_ids[0]
                        stats = {"sent": total_sent}
                        
                        # Schedule admin summary in the event loop (callback must be synchronous)
                        try:
                            loop = asyncio.get_running_loop()
                            loop.create_task(self._send_admin_summary(
                                context.bot,
                                admin_id,
                                stats,
                                operation="product notification"
                            ))
                        except RuntimeError:
                            # No running event loop - this shouldn't happen in a task callback
                            # but handle gracefully by logging and skipping admin summary
                            logger.error("Cannot send admin summary: no running event loop in callback")
                except Exception as e:
                    logger.error(f"Error in notification task completion callback: {e}", exc_info=True)
                finally:
                    # Always clear task reference
                    self._current_notification_task = None
            
            task.add_done_callback(on_task_complete)
            
        except Exception as e:
            logger.error(f"Error notifying users about product {product_id}: {e}", exc_info=True)
    
    async def process_notification_queue(self, context: ContextTypes.DEFAULT_TYPE):
        """
        Process pending notifications from the queue with rate limiting and staggered delivery.
        Groups users and sends notifications at staggered intervals to avoid spam flags.
        Uses multi-bot delivery - sends via the bot each user started.
        Processes all pending notifications in batches until queue is empty.
        """
        try:
            total_sent_count = 0
            batch_number = 0
            rate_limited_users = set()  # Track users who hit rate limits in this run
            
            # Process notifications in batches until queue is empty
            while True:
                batch_number += 1
                pending = await self.db.get_pending_notifications(limit=PENDING_NOTIFICATIONS_BATCH_LIMIT)
                
                if not pending:
                    if batch_number == 1:
                        logger.debug("No pending notifications to process")
                    else:
                        logger.info(f"Finished processing all notification batches. Total sent: {total_sent_count}")
                    return total_sent_count
                
                logger.info(f"Processing batch {batch_number}: {len(pending)} pending notifications")
                
                # Group notifications by user for rate limiting
                user_notifications = {}
                for notification in pending:
                    user_id = notification["user_id"]
                    if user_id not in user_notifications:
                        user_notifications[user_id] = []
                    user_notifications[user_id].append(notification)
                
                # Check if all users in this batch were already rate-limited
                # Check if user_notifications is not empty before checking rate limits
                # to avoid incorrectly breaking when there are no users in the batch
                if user_notifications and all(uid in rate_limited_users for uid in user_notifications.keys()):
                    logger.info(f"All {len(user_notifications)} users in batch {batch_number} were previously rate-limited, stopping")
                    return total_sent_count
                
                # Filter users who haven't exceeded rate limits
                eligible_users = []
                for user_id, notifications in user_notifications.items():
                    # Skip if already known to be rate-limited
                    if user_id in rate_limited_users:
                        continue
                    
                    # Check rate limit for this user
                    recent_count = await self.db.get_recent_notifications_count(user_id, minutes=60)
                    
                    if recent_count >= MAX_NOTIFICATIONS_PER_HOUR:
                        logger.debug(f"User {user_id} reached rate limit, skipping")
                        rate_limited_users.add(user_id)
                        continue
                    
                    # Calculate how many more notifications we can send to this user
                    remaining = MAX_NOTIFICATIONS_PER_HOUR - recent_count
                    
                    # Add user with their limited notifications
                    eligible_users.append((user_id, notifications[:remaining]))
                
                if not eligible_users:
                    logger.info(f"No eligible users after rate limiting in batch {batch_number}")
                    # All users in this batch are rate-limited
                    # If we continue, we'll fetch the same notifications again (infinite loop)
                    # So we break here - these will be retried in the next scheduled run
                    return total_sent_count
                
                # Implement staggered delivery by groups with multi-bot support
                sent_count = 0
                
                if ENABLE_STAGGERED_DELIVERY and len(eligible_users) >= STAGGER_GROUP_SIZE:
                    logger.info(f"Using staggered delivery: {len(eligible_users)} users in groups of {STAGGER_GROUP_SIZE}")
                    
                    # Split users into groups
                    user_groups = []
                    for i in range(0, len(eligible_users), STAGGER_GROUP_SIZE):
                        user_groups.append(eligible_users[i:i + STAGGER_GROUP_SIZE])
                    
                    # Process each group with staggered delay
                    for group_idx, user_group in enumerate(user_groups):
                        logger.info(f"Processing group {group_idx + 1}/{len(user_groups)} with {len(user_group)} users")
                        
                        # Collect all notifications for this group
                        group_batch = []
                        for user_id, notifications in user_group:
                            group_batch.extend(notifications)
                        
                        # Send notifications in smaller batches within the group
                        for i in range(0, len(group_batch), NOTIFICATION_BATCH_SIZE):
                            batch = group_batch[i:i + NOTIFICATION_BATCH_SIZE]
                            sent = await self._send_notification_batch_multibot(batch)
                            sent_count += sent
                            
                            # Small delay between batches within a group
                            if i + NOTIFICATION_BATCH_SIZE < len(group_batch):
                                await asyncio.sleep(BATCH_DELAY_SECONDS)
                        
                        # Stagger delay before next group (except for last group)
                        if group_idx < len(user_groups) - 1:
                            logger.info(f"Waiting {STAGGER_INTERVAL_SECONDS}s before next group...")
                            await asyncio.sleep(STAGGER_INTERVAL_SECONDS)
                else:
                    # Standard batch processing without staggering
                    logger.info("Using standard batch processing (staggering disabled or small user count)")
                    batch = []
                    
                    for user_id, notifications in eligible_users:
                        batch.extend(notifications)
                        
                        # Send in batches to avoid overwhelming Telegram
                        if len(batch) >= NOTIFICATION_BATCH_SIZE:
                            sent = await self._send_notification_batch_multibot(batch)
                            sent_count += sent
                            batch = []
                            await asyncio.sleep(BATCH_DELAY_SECONDS)
                    
                    # Send remaining batch
                    if batch:
                        sent = await self._send_notification_batch_multibot(batch)
                        sent_count += sent
                
                total_sent_count += sent_count
                logger.info(f"Batch {batch_number} complete: Sent {sent_count} notifications successfully (total: {total_sent_count})")
                
                # Brief pause between batches to avoid overwhelming the system
                if len(pending) == PENDING_NOTIFICATIONS_BATCH_LIMIT:  # If we got a full batch, there might be more
                    await asyncio.sleep(2)
            
        except Exception as e:
            logger.error(f"Error processing notification queue: {e}", exc_info=True)
            return total_sent_count
    
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
            
            # Get user's bot_username from database
            user = await self.db.get_user_by_id(user_id)
            if not user:
                # User not found, mark notification as sent and skip
                # This will be handled by _send_single_notification, but we skip grouping
                logger.debug(f"User {user_id} not found, will mark notification as sent")
                await self.db.mark_notification_sent(notification["id"])
                continue
            
            bot_username = user.get("bot_username")
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
            
            # Send notification using the bot application
            try:
                await bot_app.bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
            except BadRequest as e:
                # Handle markdown parse errors by retrying without parse_mode
                if self._is_markdown_parse_error(e):
                    logger.warning(f"Markdown parse error for user {user_id}, retrying without parse_mode: {e}")
                    await bot_app.bot.send_message(
                        chat_id=user_id,
                        text=message_text,
                        reply_markup=keyboard
                    )
                else:
                    # Re-raise other BadRequest errors to be handled below
                    raise
            
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
            
            for user in all_users:
                user_id = user["user_id"]
                
                # Skip blocked users if requested
                if exclude_blocked and user.get("is_blocked", 0) == 1:
                    skipped_blocked += 1
                    continue
                
                # Skip admin users
                from utils.helpers import get_admin_ids
                if user_id in get_admin_ids():
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
        Uses multi-bot delivery - sends via the bot each user started.
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
            iteration_count = 0
            
            # Loop until all pending messages are processed or no progress can be made
            while iteration_count < MAX_QUEUE_PROCESSING_ITERATIONS:
                iteration_count += 1
                
                pending = await self.db.get_pending_custom_messages(limit=100)
                
                if not pending:
                    break
                
                total_fetched += len(pending)
                logger.info(f"Processing batch {iteration_count} of {len(pending)} pending custom messages (total fetched: {total_fetched})")
                
                # Group messages by user for rate limiting
                user_messages = {}
                for message in pending:
                    user_id = message["user_id"]
                    if user_id not in user_messages:
                        user_messages[user_id] = []
                    user_messages[user_id].append(message)
                
                # Process messages in batches
                batch = []
                batch_has_messages = False
                
                for user_id, messages in user_messages.items():
                    # Check if user is blocked
                    is_blocked = await self.db.is_user_blocked(user_id)
                    if is_blocked:
                        # Mark as sent to remove from queue (blocked users won't receive messages)
                        for msg in messages:
                            await self.db.mark_custom_message_sent(msg["id"])
                        stats["blocked"] += len(messages)
                        continue
                    
                    # Check if user was previously rate-limited in this run
                    # We skip to avoid double-counting stats and wasting rate limit checks
                    # Messages remain in queue for next scheduled run when rate limits may have reset
                    if user_id in rate_limited_users:
                        continue
                    
                    # Check rate limit for this user
                    recent_count = await self.db.get_recent_custom_messages_count(user_id, minutes=60)
                    
                    if recent_count >= MAX_CUSTOM_MESSAGES_PER_HOUR:
                        # User at rate limit - count their messages and skip them
                        # Messages stay in queue for next scheduled run
                        logger.debug(f"User {user_id} reached custom message rate limit, skipping")
                        stats["rate_limited"] += len(messages)
                        rate_limited_users.add(user_id)
                        continue
                    
                    # Calculate how many more messages we can send to this user
                    remaining = MAX_CUSTOM_MESSAGES_PER_HOUR - recent_count
                    
                    # Send only the allowed number of messages
                    # Messages beyond 'remaining' stay in queue and will be picked up in next iteration
                    # or next scheduled run (if user then hits rate limit)
                    for message in messages[:remaining]:
                        batch.append(message)
                        batch_has_messages = True
                        
                        # Send in batches to avoid overwhelming Telegram
                        if len(batch) >= CUSTOM_MESSAGE_BATCH_SIZE:
                            batch_stats = await self._send_custom_message_batch_multibot(batch, stats)
                            batch = []
                            await asyncio.sleep(CUSTOM_MESSAGE_DELAY_SECONDS)
                
                # Send remaining batch
                if batch:
                    batch_stats = await self._send_custom_message_batch_multibot(batch, stats)
                
                # If no messages were actually sent in this batch (all blocked or rate-limited),
                # break to avoid infinite loop within this run
                # Unsent messages remain in queue for next scheduled run
                if not batch_has_messages:
                    logger.info(f"No more messages can be sent in this run (all blocked or rate-limited)")
                    break
            
            # Log warning if we hit the iteration limit
            if iteration_count >= MAX_QUEUE_PROCESSING_ITERATIONS:
                logger.warning(f"Reached maximum queue processing iterations ({MAX_QUEUE_PROCESSING_ITERATIONS}). "
                              f"Remaining messages will be processed in next run.")
            
            logger.info(f"Custom message queue processed: {stats['sent']} sent, {stats['failed']} failed, "
                       f"{stats['blocked']} blocked, {stats['not_found']} not found, "
                       f"{stats['rate_limited']} rate limited, {stats['markdown_errors']} markdown errors")
            
            return stats
            
        except Exception as e:
            logger.error(f"Error processing custom message queue: {e}", exc_info=True)
            stats["unexpected_errors"] += 1
            return stats
    
    async def _send_custom_message_batch_multibot(
        self,
        messages: List[dict],
        stats: Optional[Dict[str, int]] = None
    ) -> int:
        """
        Send a batch of custom messages using multi-bot delivery.
        Each message is sent via the bot that the user started.
        Updates the provided stats dictionary with delivery results.
        Returns the number of successfully sent messages.
        """
        if stats is None:
            stats = {
                "sent": 0, 
                "blocked": 0, 
                "not_found": 0, 
                "rate_limited": 0,
                "markdown_errors": 0, 
                "unexpected_errors": 0
            }
        
        # Get all available bot applications (cached)
        bot_apps = self._get_bot_applications()
        
        if not bot_apps:
            logger.warning("No bot applications available for multi-bot delivery")
            stats["failed"] += len(messages)
            return 0
        
        # Group messages by user's bot_username for efficient delivery
        users_by_bot: Dict[str, List[dict]] = {}
        
        for message in messages:
            user_id = message["user_id"]
            
            # Get user's bot_username from database
            user = await self.db.get_user_by_id(user_id)
            if not user:
                # User not found, mark message as sent and skip
                logger.debug(f"User {user_id} not found, will mark custom message as sent")
                await self.db.mark_custom_message_sent(message["id"])
                stats["not_found"] += 1
                continue
            
            bot_username = user.get("bot_username")
            if bot_username not in users_by_bot:
                users_by_bot[bot_username] = []
            users_by_bot[bot_username].append(message)
        
        # Send messages through each bot
        for bot_username, bot_messages in users_by_bot.items():
            # Get the appropriate bot application
            bot_app = get_bot_by_username(bot_username) if bot_username else bot_apps[0]
            
            if not bot_app:
                logger.warning(f"Bot @{bot_username} not found, using primary bot")
                bot_app = bot_apps[0]
            
            logger.info(f"Sending {len(bot_messages)} custom messages via bot @{get_bot_username(bot_app)}")
            
            # Send each message
            for message in bot_messages:
                markdown_error_occurred = False
                try:
                    user_id = message["user_id"]
                    message_text = message["message_text"]
                    message_id = message["id"]
                    
                    # Check if user is blocked
                    is_blocked = await self.db.is_user_blocked(user_id)
                    if is_blocked:
                        # Mark as sent to remove from queue
                        await self.db.mark_custom_message_sent(message_id)
                        stats["blocked"] += 1
                        continue
                    
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
                    
                    logger.debug(f"Sent custom message to user {user_id} via bot @{get_bot_username(bot_app)}")
                    
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
        
        return stats["sent"]
    
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
            # Total attempted = sent (including those with markdown errors) + permanent failures
            total_permanent_failures = blocked + not_found
            total_attempted = sent + total_permanent_failures
            
            # Build summary message
            summary = f"📊 **{operation.title()} Complete**\n\n"
            summary += f"✅ **Successfully Sent:** {sent}\n"
            
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
            
            # Calculate success rate based on delivery attempts (excluding rate-limited and unexpected)
            if total_attempted > 0:
                success_rate = (sent / total_attempted) * 100
                summary += f"\n📈 **Success Rate:** {success_rate:.1f}%"
            
            await bot.send_message(
                chat_id=admin_user_id,
                text=summary,
                parse_mode="Markdown"
            )
            logger.info(f"Sent delivery summary to admin {admin_user_id}")
            
        except Exception as e:
            logger.error(f"Failed to send admin summary: {e}", exc_info=True)


