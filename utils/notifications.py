"""
Notification service for sending product notifications to users.
Implements rate limiting to respect Telegram's anti-spam policies.
"""
import logging
import asyncio
from typing import List
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError, Forbidden

from database import Database
from utils.categories import get_category_display_name, get_subcategory_display_name, NOTIFICATION_EXCLUDED_CATEGORIES
from translations.translator import translate_text

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


class NotificationService:
    """Service for managing and sending product notifications."""
    
    def __init__(self, db: Database):
        self.db = db
    
    async def notify_users_about_product(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        product_id: int
    ):
        """
        Notify subscribed users about a new categorized product.
        Implements rate limiting to prevent spam.
        """
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
            
            # Skip notifications for excluded categories (DATEDPROOFS, CLIENTTOUCHDOWNS)
            # Note: ANNOUNCEMENTS is NOT excluded from notifications
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
            
            # Queue notifications for all subscribed users
            for user_id in subscribed_users:
                await self.db.queue_notification(user_id, product_id)
            
            # Process the notification queue asynchronously (non-blocking)
            # Schedule as a background task so it doesn't block other operations
            # Store task reference to prevent it from being garbage collected
            task = asyncio.create_task(self.process_notification_queue(context))
            
            # Add exception handling callback to log any errors
            def log_task_exception(task_obj):
                try:
                    task_obj.result()
                except Exception as e:
                    logger.error(f"Error in background notification task: {e}", exc_info=True)
            
            task.add_done_callback(log_task_exception)
            
        except Exception as e:
            logger.error(f"Error notifying users about product {product_id}: {e}", exc_info=True)
    
    async def process_notification_queue(self, context: ContextTypes.DEFAULT_TYPE):
        """
        Process pending notifications from the queue with rate limiting and staggered delivery.
        Groups users and sends notifications at staggered intervals to avoid spam flags.
        """
        try:
            pending = await self.db.get_pending_notifications(limit=100)
            
            if not pending:
                return
            
            logger.info(f"Processing {len(pending)} pending notifications")
            
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
                    logger.debug(f"User {user_id} reached rate limit, skipping")
                    continue
                
                # Calculate how many more notifications we can send to this user
                remaining = MAX_NOTIFICATIONS_PER_HOUR - recent_count
                
                # Add user with their limited notifications
                eligible_users.append((user_id, notifications[:remaining]))
            
            if not eligible_users:
                logger.info("No eligible users after rate limiting")
                return
            
            # Implement staggered delivery by groups
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
                        sent = await self._send_notification_batch(context, batch)
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
                        sent = await self._send_notification_batch(context, batch)
                        sent_count += sent
                        batch = []
                        await asyncio.sleep(BATCH_DELAY_SECONDS)
                
                # Send remaining batch
                if batch:
                    sent = await self._send_notification_batch(context, batch)
                    sent_count += sent
            
            logger.info(f"Sent {sent_count} notifications successfully")
            
        except Exception as e:
            logger.error(f"Error processing notification queue: {e}", exc_info=True)
    
    async def _send_notification_batch(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        notifications: List[dict]
    ) -> int:
        """
        Send a batch of notifications.
        Returns the number of successfully sent notifications.
        """
        sent_count = 0
        
        for notification in notifications:
            try:
                user_id = notification["user_id"]
                product_id = notification["product_id"]
                notification_id = notification["id"]
                
                # Get product details
                product = await self.db.get_product(product_id)
                
                if not product:
                    # Product deleted, mark notification as sent
                    await self.db.mark_notification_sent(notification_id)
                    continue
                
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
                        caption = translate_text(caption, user_lang)
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
        message_text: str
    ):
        """
        Send a custom message to a specific user with rate limiting.
        """
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
        exclude_blocked: bool = True
    ):
        """
        Broadcast a custom message to all users with rate limiting.
        """
        try:
            # Get all users
            all_users = await self.db.get_all_users()
            
            if not all_users:
                logger.debug("No users to broadcast to")
                return 0
            
            logger.info(f"Broadcasting custom message to {len(all_users)} users")
            
            # Queue messages for all non-blocked users
            queued = 0
            for user in all_users:
                user_id = user["user_id"]
                
                # Skip blocked users if requested
                if exclude_blocked and user.get("is_blocked", 0) == 1:
                    continue
                
                # Skip admin users
                from utils.helpers import get_admin_ids
                if user_id in get_admin_ids():
                    continue
                
                await self.db.queue_custom_message(user_id, message_text)
                queued += 1
            
            logger.info(f"Queued {queued} custom messages")
            
            # Process the queue
            await self.process_custom_message_queue(context)
            
            return queued
            
        except Exception as e:
            logger.error(f"Error broadcasting custom message: {e}", exc_info=True)
            return 0
    
    async def process_custom_message_queue(self, context: ContextTypes.DEFAULT_TYPE):
        """
        Process pending custom messages from the queue with rate limiting.
        """
        try:
            pending = await self.db.get_pending_custom_messages(limit=100)
            
            if not pending:
                return
            
            logger.info(f"Processing {len(pending)} pending custom messages")
            
            # Group messages by user for rate limiting
            user_messages = {}
            for message in pending:
                user_id = message["user_id"]
                if user_id not in user_messages:
                    user_messages[user_id] = []
                user_messages[user_id].append(message)
            
            # Process messages in batches
            sent_count = 0
            batch = []
            
            for user_id, messages in user_messages.items():
                # Check if user is blocked
                is_blocked = await self.db.is_user_blocked(user_id)
                if is_blocked:
                    # Mark as sent to remove from queue
                    for msg in messages:
                        await self.db.mark_custom_message_sent(msg["id"])
                    continue
                
                # Check rate limit for this user
                recent_count = await self.db.get_recent_custom_messages_count(user_id, minutes=60)
                
                if recent_count >= MAX_CUSTOM_MESSAGES_PER_HOUR:
                    logger.debug(f"User {user_id} reached custom message rate limit, skipping")
                    continue
                
                # Calculate how many more messages we can send to this user
                remaining = MAX_CUSTOM_MESSAGES_PER_HOUR - recent_count
                
                # Send only the allowed number of messages
                for message in messages[:remaining]:
                    batch.append(message)
                    
                    # Send in batches to avoid overwhelming Telegram
                    if len(batch) >= CUSTOM_MESSAGE_BATCH_SIZE:
                        sent = await self._send_custom_message_batch(context, batch)
                        sent_count += sent
                        batch = []
                        await asyncio.sleep(CUSTOM_MESSAGE_DELAY_SECONDS)
            
            # Send remaining batch
            if batch:
                sent = await self._send_custom_message_batch(context, batch)
                sent_count += sent
            
            logger.info(f"Sent {sent_count} custom messages successfully")
            
        except Exception as e:
            logger.error(f"Error processing custom message queue: {e}", exc_info=True)
    
    async def _send_custom_message_batch(
        self,
        context: ContextTypes.DEFAULT_TYPE,
        messages: List[dict]
    ) -> int:
        """
        Send a batch of custom messages.
        Returns the number of successfully sent messages.
        """
        sent_count = 0
        
        for message in messages:
            try:
                user_id = message["user_id"]
                message_text = message["message_text"]
                message_id = message["id"]
                
                # Check if user is blocked
                is_blocked = await self.db.is_user_blocked(user_id)
                if is_blocked:
                    # Mark as sent to remove from queue
                    await self.db.mark_custom_message_sent(message_id)
                    continue
                
                # Send custom message
                await context.bot.send_message(
                    chat_id=user_id,
                    text=message_text,
                    parse_mode="Markdown"
                )
                
                # Mark as sent
                await self.db.mark_custom_message_sent(message_id)
                sent_count += 1
                
                logger.debug(f"Sent custom message to user {user_id}")
                
            except Forbidden:
                # User blocked the bot, block them in our system
                logger.info(f"User {user_id} blocked the bot, marking as blocked")
                await self.db.block_user(user_id)
                await self.db.mark_custom_message_sent(message_id)
                
            except TelegramError as e:
                logger.error(f"Telegram error sending custom message to {user_id}: {e}")
                # Don't mark as sent, will retry later
                
            except Exception as e:
                logger.error(f"Error sending custom message to {user_id}: {e}")
                # Mark as sent to avoid infinite retries
                await self.db.mark_custom_message_sent(message_id)
        
        return sent_count

