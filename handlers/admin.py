"""
Admin handlers.
"""
import logging
import asyncio
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown
from database import Database
from utils.helpers import is_admin, get_channel_id, get_channel_username

logger = logging.getLogger(__name__)
db = Database()


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /stats command (admin only)."""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ This command is only available for administrators."
        )
        return
    
    try:
        stats = await db.get_stats()
        
        text = (
            "📊 **Catalog Statistics**\n\n"
            f"📦 Total Products: {stats['total']}\n"
            f"📅 Added Today: {stats['today']}"
        )
        
        await update.message.reply_text(text, parse_mode="Markdown")
        logger.info(f"Admin {user_id} requested stats")
        
    except Exception as e:
        logger.error(f"Error getting stats: {e}")
        await update.message.reply_text(
            "❌ An error occurred while retrieving statistics."
        )


async def nuke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /nuke command - deletes ALL products (admin only) with double confirmation."""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ This command is only available for administrators."
        )
        return
    
    # First confirmation
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚠️ Yes, I want to nuke", callback_data="nuke_confirm1")],
        [InlineKeyboardButton("❌ Cancel", callback_data="nuke_cancel")]
    ])
    
    total_count = await db.count_products()
    
    await update.message.reply_text(
        f"⚠️ **NUKE WARNING**\n\n"
        f"You are about to delete **{total_count}** product(s) from the catalog.\n\n"
        f"This action cannot be undone!\n\n"
        f"Are you sure you want to continue?",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )


async def users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /users command - view all bot users with notification controls (admin only)."""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ This command is only available for administrators."
        )
        return
    
    try:
        # Show users list with pagination (10 per page)
        page = int(context.args[0]) if context.args and context.args[0].isdigit() else 1
        await show_users_page(update, context, page)
        
    except Exception as e:
        logger.error(f"Error in users command: {e}")
        await update.message.reply_text(
            "❌ An error occurred while retrieving user list."
        )


async def show_users_page(update: Update, context: ContextTypes.DEFAULT_TYPE, page: int = 1):
    """Display a page of users with notification toggle controls."""
    try:
        users = await db.get_all_users()
        total_users = len(users)
        
        if total_users == 0:
            text = "📭 No users have interacted with the bot yet."
            if update.callback_query:
                await update.callback_query.edit_message_text(text)
            else:
                await update.message.reply_text(text)
            return
        
        # Pagination settings
        per_page = 10
        total_pages = (total_users + per_page - 1) // per_page
        page = max(1, min(page, total_pages))
        
        start_idx = (page - 1) * per_page
        end_idx = min(start_idx + per_page, total_users)
        page_users = users[start_idx:end_idx]
        
        # Build message text
        message_lines = [f"👥 **Bot Users** (Page {page}/{total_pages}, {total_users} total)\n"]
        
        for i, user in enumerate(page_users, start=start_idx + 1):
            # Escape Markdown characters
            username_raw = user.get('username')
            if username_raw:
                username = escape_markdown(f"@{str(username_raw)}")
            else:
                username = "No username"
            first_name = escape_markdown(str(user.get('first_name', 'N/A')))
            last_name = escape_markdown(str(user.get('last_name', '')))
            full_name = f"{first_name} {last_name}".strip()
            
            # Notification status
            notif_enabled = user.get('notifications_enabled', 1) == 1
            notif_status = "🔔 ON" if notif_enabled else "🔕 OFF"
            
            # Blocked status
            is_blocked = user.get('is_blocked', 0) == 1
            blocked_status = "🚫 BLOCKED" if is_blocked else ""
            
            last_seen = user.get('last_seen', 'Unknown')
            if last_seen != 'Unknown':
                from datetime import datetime
                try:
                    dt = datetime.fromisoformat(last_seen)
                    last_seen = dt.strftime("%Y-%m-%d %H:%M")
                except:
                    pass
            
            user_text = (
                f"\n{i}. **{full_name}** ({username})\n"
                f"   🆔 ID: `{user['user_id']}`\n"
                f"   📊 Interactions: {user.get('interaction_count', 0)}\n"
                f"   🕐 Last seen: {last_seen}\n"
                f"   📬 Notifications: {notif_status}"
            )
            
            # Add blocked status if applicable
            if blocked_status:
                user_text += f"\n   ⚠️ Status: {blocked_status}"
            
            message_lines.append(user_text)
        
        message_text = "\n".join(message_lines)
        
        # Build keyboard with individual toggle buttons
        keyboard_buttons = []
        for user in page_users:
            uid = user['user_id']
            notif_enabled = user.get('notifications_enabled', 1) == 1
            is_blocked = user.get('is_blocked', 0) == 1
            
            # Get user display name
            username = user.get('username') or user.get('first_name', 'Unknown')
            if len(username) > 12:
                username = username[:9] + "..."
            
            # Create row with notification toggle and block/unblock button
            row_buttons = [
                InlineKeyboardButton(
                    f"{'🔕' if notif_enabled else '🔔'} {username}",
                    callback_data=f"toggle_notif|{uid}|{page}"
                )
            ]
            
            # Add block/unblock button
            if is_blocked:
                row_buttons.append(
                    InlineKeyboardButton("✅ Unblock", callback_data=f"unblock_user|{uid}|{page}")
                )
            else:
                row_buttons.append(
                    InlineKeyboardButton("🚫 Block", callback_data=f"block_user|{uid}|{page}")
                )
            
            keyboard_buttons.append(row_buttons)
        
        # Add pagination buttons
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"users_page|{page-1}"))
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton("➡️ Next", callback_data=f"users_page|{page+1}"))
        
        if nav_buttons:
            keyboard_buttons.append(nav_buttons)
        
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        # Send or edit message
        if update.callback_query:
            await update.callback_query.edit_message_text(
                message_text,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        else:
            await update.message.reply_text(
                message_text,
                parse_mode="Markdown",
                reply_markup=keyboard
            )
        
        logger.info(f"Displayed users page {page}/{total_pages}")
        
    except Exception as e:
        logger.error(f"Error showing users page: {e}", exc_info=True)
        error_text = "❌ An error occurred while displaying users."
        if update.callback_query:
            await update.callback_query.edit_message_text(error_text)
        else:
            await update.message.reply_text(error_text)


async def delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
    """Delete a product (admin only)."""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.callback_query.answer(
            "❌ Only administrators can delete products.",
            show_alert=True
        )
        return
    
    try:
        # Get product info before deletion
        product = await db.get_product(product_id)
        
        if not product:
            await update.callback_query.answer(
                "❌ Product not found.",
                show_alert=True
            )
            return
        
        # Delete from database (channel message will remain)
        deleted = await db.delete_product(product_id)
        
        if deleted:
            # Get user's previous page to return to
            catalog_page = await db.get_pagination_state(user_id, "catalog", None)
            back_page = catalog_page or 1
            
            # Check if the message is a media message (can't edit media messages)
            message = update.callback_query.message
            if message.photo or message.video or message.document or message.animation or message.audio:
                # Delete the media message and send a new text message
                try:
                    await message.delete()
                except:
                    pass  # If deletion fails, continue anyway
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="✅ Product deleted successfully!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back to catalog", callback_data=f"menu|{back_page}")]
                    ])
                )
            else:
                # Regular text message, can be edited
                await update.callback_query.edit_message_text(
                    "✅ Product deleted successfully!",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back to catalog", callback_data=f"menu|{back_page}")]
                    ])
                )
            logger.info(f"Admin {user_id} deleted product {product_id}")
        else:
            await update.callback_query.answer(
                "❌ Failed to delete product.",
                show_alert=True
            )
            
    except Exception as e:
        logger.error(f"Error deleting product: {e}")
        await update.callback_query.answer(
            "❌ An error occurred while deleting the product.",
            show_alert=True
        )


async def recategorize_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /recategorize command - send categorization notifications for uncategorized products (admin only)."""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ This command is only available for administrators."
        )
        return
    
    try:
        # Send initial message
        status_msg = await update.message.reply_text(
            "🔄 Checking for uncategorized products...\n"
            "This may take a moment."
        )
        
        # Get all products
        all_products = await db.get_all_products_for_search()
        
        if not all_products:
            await status_msg.edit_text("📭 No products found.")
            return
        
        # Find uncategorized products
        uncategorized = [p for p in all_products if not p.get("category")]
        
        if not uncategorized:
            await status_msg.edit_text(
                f"✅ All {len(all_products)} products are already categorized!"
            )
            return
        
        # Send categorization notifications for uncategorized products
        # Import the notification function
        from main import notify_admins_for_categorization
        
        notified = 0
        for product in uncategorized[:10]:  # Limit to 10 at a time to avoid spam
            try:
                await notify_admins_for_categorization(context, product["id"])
                notified += 1
                await asyncio.sleep(0.5)  # Small delay between notifications
            except Exception as e:
                logger.error(f"Failed to notify for product {product['id']}: {e}")
        
        # Send completion message
        categorized_count = len(all_products) - len(uncategorized)
        text = (
            f"📊 **Categorization Status**\n\n"
            f"📦 Total products: {len(all_products)}\n"
            f"✅ Categorized: {categorized_count}\n"
            f"❓ Uncategorized: {len(uncategorized)}\n"
            f"📤 Notifications sent: {notified}"
        )
        
        if len(uncategorized) > 10:
            text += f"\n\n⚠️ Only sent notifications for first 10 uncategorized products.\nRun command again to process more."
        
        await status_msg.edit_text(text, parse_mode="Markdown")
        logger.info(f"Admin {user_id} triggered recategorization - sent {notified} notifications")
        
    except Exception as e:
        logger.error(f"Error in recategorize command: {e}")
        await update.message.reply_text(
            "❌ An error occurred during recategorization."
        )


async def block_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /block command - block a user from using the bot (admin only).
    
    Usage: /block <user_id>
    Example: /block 123456789
    """
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ This command is only available for administrators."
        )
        return
    
    # Check arguments
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "❌ **Invalid format**\n\n"
            "**Usage:** `/block <user_id>`\n\n"
            "**Example:** `/block 123456789`\n\n"
            "💡 Tip: Use `/users` to find user IDs",
            parse_mode="Markdown"
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        
        # Prevent blocking admins
        if is_admin(target_user_id):
            await update.message.reply_text(
                "❌ Cannot block an admin user."
            )
            return
        
        # Block the user
        await db.block_user(target_user_id)
        
        await update.message.reply_text(
            f"✅ **User Blocked**\n\n"
            f"🆔 User ID: `{target_user_id}`\n\n"
            f"This user can no longer use the bot.\n"
            f"Use `/unblock {target_user_id}` to unblock.",
            parse_mode="Markdown"
        )
        logger.info(f"Admin {user_id} blocked user {target_user_id}")
        
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid user ID. Please provide a valid number."
        )
    except Exception as e:
        logger.error(f"Error in block command: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ An error occurred while blocking the user."
        )


async def unblock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /unblock command - unblock a user (admin only).
    
    Usage: /unblock <user_id>
    Example: /unblock 123456789
    """
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ This command is only available for administrators."
        )
        return
    
    # Check arguments
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "❌ **Invalid format**\n\n"
            "**Usage:** `/unblock <user_id>`\n\n"
            "**Example:** `/unblock 123456789`",
            parse_mode="Markdown"
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        
        # Unblock the user
        await db.unblock_user(target_user_id)
        
        await update.message.reply_text(
            f"✅ **User Unblocked**\n\n"
            f"🆔 User ID: `{target_user_id}`\n\n"
            f"This user can now use the bot again.",
            parse_mode="Markdown"
        )
        logger.info(f"Admin {user_id} unblocked user {target_user_id}")
        
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid user ID. Please provide a valid number."
        )
    except Exception as e:
        logger.error(f"Error in unblock command: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ An error occurred while unblocking the user."
        )


async def send_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /send command - send a custom message to a specific user (admin only).
    
    Usage: /send <user_id> <message>
    Example: /send 123456789 Hello! This is a custom message.
    """
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ This command is only available for administrators."
        )
        return
    
    # Check arguments
    if not context.args or len(context.args) < 2:
        await update.message.reply_text(
            "❌ **Invalid format**\n\n"
            "**Usage:** `/send <user_id> <message>`\n\n"
            "**Example:** `/send 123456789 Hello! This is a custom message.`\n\n"
            "💡 Tip: Use `/users` to find user IDs\n"
            "⚠️ Rate limit: 3 messages per user per hour",
            parse_mode="Markdown"
        )
        return
    
    try:
        target_user_id = int(context.args[0])
        message_text = " ".join(context.args[1:])
        
        # Check if user is blocked
        is_blocked = await db.is_user_blocked(target_user_id)
        if is_blocked:
            await update.message.reply_text(
                f"❌ Cannot send message to blocked user `{target_user_id}`.",
                parse_mode="Markdown"
            )
            return
        
        # Send the message using notification service
        from utils.notifications import NotificationService
        notification_service = NotificationService(db)
        
        success = await notification_service.send_custom_message_to_user(
            context,
            target_user_id,
            message_text
        )
        
        if success:
            await update.message.reply_text(
                f"✅ **Message Queued**\n\n"
                f"🆔 User ID: `{target_user_id}`\n"
                f"📝 Message: {message_text[:100]}{'...' if len(message_text) > 100 else ''}\n\n"
                f"Message will be delivered shortly.",
                parse_mode="Markdown"
            )
            logger.info(f"Admin {user_id} sent custom message to user {target_user_id}")
        else:
            await update.message.reply_text(
                "❌ Failed to queue message. Check logs for details."
            )
        
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid user ID. Please provide a valid number."
        )
    except Exception as e:
        logger.error(f"Error in send command: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ An error occurred while sending the message."
        )


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /broadcast command - send a message to all users (admin only).
    
    Usage: /broadcast <message>
    Example: /broadcast New products added! Check them out with /menu
    """
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ This command is only available for administrators."
        )
        return
    
    # Check arguments
    if not context.args or len(context.args) < 1:
        await update.message.reply_text(
            "❌ **Invalid format**\n\n"
            "**Usage:** `/broadcast <message>`\n\n"
            "**Example:** `/broadcast New products added! Check them out with /menu`\n\n"
            "⚠️ **Important:**\n"
            "• Rate limit: 3 messages per user per hour\n"
            "• Blocked users are automatically excluded\n"
            "• Admins are excluded from broadcasts\n"
            "• Messages are queued and delivered with delays",
            parse_mode="Markdown"
        )
        return
    
    try:
        message_text = " ".join(context.args)
        
        # Send status message
        status_msg = await update.message.reply_text(
            "📡 **Broadcasting message...**\n\n"
            "Please wait while the message is queued for all users.",
            parse_mode="Markdown"
        )
        
        # Broadcast using notification service
        from utils.notifications import NotificationService
        notification_service = NotificationService(db)
        
        queued_count = await notification_service.broadcast_custom_message(
            context,
            message_text,
            exclude_blocked=True
        )
        
        await status_msg.edit_text(
            f"✅ **Broadcast Complete**\n\n"
            f"📨 Messages queued: {queued_count}\n"
            f"📝 Message: {message_text[:100]}{'...' if len(message_text) > 100 else ''}\n\n"
            f"Messages will be delivered with rate-limiting intervals.",
            parse_mode="Markdown"
        )
        logger.info(f"Admin {user_id} broadcast message to {queued_count} users")
        
    except Exception as e:
        logger.error(f"Error in broadcast command: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ An error occurred while broadcasting the message."
        )


