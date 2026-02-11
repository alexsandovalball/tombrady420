"""
Admin handlers.
"""
import logging
import asyncio
import aiosqlite
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from database import Database
from utils.helpers import is_admin, get_channel_id, get_channel_username, escape_markdown_v1

logger = logging.getLogger(__name__)
db = Database()


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
        # Pagination settings
        per_page = 10
        offset = (page - 1) * per_page
        
        # Get paginated users and total count in single query
        total_users, page_users = await db.get_users_paginated(limit=per_page, offset=offset)
        
        if total_users == 0:
            text = "📭 No users have interacted with the bot yet."
            if update.callback_query:
                await update.callback_query.edit_message_text(text)
            else:
                await update.message.reply_text(text)
            return
        
        total_pages = (total_users + per_page - 1) // per_page
        page = max(1, min(page, total_pages))
        
        # Build message text
        message_lines = [f"👥 **Bot Users** (Page {page}/{total_pages}, {total_users} total)\n"]
        
        for i, user in enumerate(page_users, start=offset + 1):
            # Escape Markdown v1 characters
            username_raw = user.get('username')
            if username_raw:
                username = escape_markdown_v1(f"@{str(username_raw)}")
            else:
                username = "No username"
            first_name = escape_markdown_v1(str(user.get('first_name', 'N/A')))
            last_name = escape_markdown_v1(str(user.get('last_name', '')))
            full_name = f"{first_name} {last_name}".strip()
            
            # Notification status
            notif_enabled = user.get('notifications_enabled', 1) == 1
            notif_status = "🔔 ON" if notif_enabled else "🔕 OFF"
            
            # Blocked status
            is_blocked = user.get('is_blocked', 0) == 1
            blocked_status = "🚫 BLOCKED" if is_blocked else ""
            
            last_seen = user.get('last_seen', 'Unknown')
            if last_seen != 'Unknown':
                try:
                    dt = datetime.fromisoformat(last_seen)
                    last_seen = dt.strftime("%Y-%m-%d %H:%M")
                except (ValueError, TypeError):
                    pass
            
            user_text = (
                f"\n{i}. **{full_name}** ({username})\n"
                f"   🆔 ID: `{user['user_id']}`\n"
                f"   📊 Interactions: {user.get('interaction_count', 0)}\n"
                f"   🕐 Last seen: {last_seen}\n"
                f"   📬 Notifications: {notif_status}"
            )
            
            # Add bot username if available
            bot_username = user.get('bot_username')
            if bot_username:
                escaped_bot_username = escape_markdown_v1(f"@{bot_username}")
                user_text += f"\n   🤖 Bot: {escaped_bot_username}"
            
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
                except Exception:
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
    """Handle /send command - initiate broadcast to single user (admin only).
    
    New workflow:
    1. Ask for user ID
    2. Wait for user ID input
    3. Ask for message
    4. Wait for message input
    5. Show confirmation
    6. Send message
    """
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ This command is only available for administrators."
        )
        return
    
    # Start the workflow - ask for user ID
    context.user_data['broadcast_mode'] = 'single_user'
    context.user_data['broadcast_step'] = 'awaiting_user_id'
    
    await update.message.reply_text(
        "📝 **Broadcast to Single User - Step 1 of 3**\n\n"
        "Please enter the user ID:",
        parse_mode="Markdown"
    )
    logger.info(f"Admin {user_id} started single user broadcast workflow")


async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /broadcast command - initiate broadcast to all users (admin only).
    
    New workflow:
    1. Ask for message
    2. Wait for message input
    3. Show confirmation
    4. Send broadcast
    """
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ This command is only available for administrators."
        )
        return
    
    # Start the workflow - ask for message
    context.user_data['broadcast_mode'] = 'all_users'
    context.user_data['broadcast_step'] = 'awaiting_message'
    
    await update.message.reply_text(
        "📝 **Broadcast to All Users - Step 1 of 2**\n\n"
        "Please enter the message you want to broadcast:",
        parse_mode="Markdown"
    )
    logger.info(f"Admin {user_id} started broadcast to all users workflow")


async def handle_broadcast_workflow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the multi-step broadcast workflow for admins."""
    user_id = update.effective_user.id
    message_text = update.message.text
    
    if not is_admin(user_id):
        return
    
    broadcast_mode = context.user_data.get('broadcast_mode')
    broadcast_step = context.user_data.get('broadcast_step')
    
    if broadcast_mode == 'single_user':
        if broadcast_step == 'awaiting_user_id':
            # Step 1: Received user ID, now ask for message
            try:
                target_user_id = int(message_text.strip())
                
                # Check if user exists and is not blocked
                is_blocked = await db.is_user_blocked(target_user_id)
                if is_blocked:
                    await update.message.reply_text(
                        f"❌ Cannot send message to blocked user `{target_user_id}`.\n\n"
                        "Broadcast cancelled. Use /send to start again.",
                        parse_mode="Markdown"
                    )
                    context.user_data.clear()
                    return
                
                # Store user ID and move to next step
                context.user_data['target_user_id'] = target_user_id
                context.user_data['broadcast_step'] = 'awaiting_message'
                
                await update.message.reply_text(
                    f"📝 **Broadcast to Single User - Step 2 of 3**\n\n"
                    f"🆔 User ID: `{target_user_id}`\n\n"
                    f"Please enter the message you want to send:",
                    parse_mode="Markdown"
                )
                logger.info(f"Admin {user_id} entered user ID: {target_user_id}")
                
            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid user ID. Please enter a valid number.\n\n"
                    "Broadcast cancelled. Use /send to start again."
                )
                context.user_data.clear()
                
        elif broadcast_step == 'awaiting_message':
            # Step 2: Received message, show confirmation
            target_user_id = context.user_data.get('target_user_id')
            context.user_data['broadcast_message'] = message_text
            context.user_data['broadcast_step'] = 'awaiting_confirmation'
            
            # Show confirmation with inline buttons
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm & Send", callback_data=f"broadcast_confirm_single|{target_user_id}")],
                [InlineKeyboardButton("❌ Cancel", callback_data="broadcast_cancel")]
            ])
            
            await update.message.reply_text(
                f"✅ **Broadcast to Single User - Confirmation**\n\n"
                f"🆔 User ID: `{target_user_id}`\n"
                f"📝 Message: {message_text[:200]}{'...' if len(message_text) > 200 else ''}\n\n"
                f"Do you want to send this message?",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            logger.info(f"Admin {user_id} awaiting confirmation for single user broadcast")
            
    elif broadcast_mode == 'all_users':
        if broadcast_step == 'awaiting_message':
            # Step 1: Received message, show confirmation
            context.user_data['broadcast_message'] = message_text
            context.user_data['broadcast_step'] = 'awaiting_confirmation'
            
            # Get user count
            users = await db.get_all_users()
            user_count = len([u for u in users if not u.get('is_blocked', 0) and not is_admin(u['user_id'])])
            
            # Show confirmation with inline buttons
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("✅ Confirm & Broadcast", callback_data="broadcast_confirm_all")],
                [InlineKeyboardButton("❌ Cancel", callback_data="broadcast_cancel")]
            ])
            
            await update.message.reply_text(
                f"✅ **Broadcast to All Users - Confirmation**\n\n"
                f"📝 Message: {message_text[:200]}{'...' if len(message_text) > 200 else ''}\n"
                f"📊 Recipients: {user_count} users\n\n"
                f"Do you want to send this broadcast?",
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            logger.info(f"Admin {user_id} awaiting confirmation for broadcast to all users")


async def setcontact_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /setcontact command - set the order contact username (admin only)."""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ This command is only available for administrators."
        )
        return
    
    # Get current contact
    current_contact = await db.get_order_contact()
    
    # Escape the current contact for markdown v1
    escaped_contact = escape_markdown_v1(current_contact) if current_contact else "Not set"
    
    await update.message.reply_text(
        f"📝 **Set Order Contact**\n\n"
        f"Current contact: {escaped_contact}\n\n"
        f"Please enter the new contact username (e.g., @username):",
        parse_mode="Markdown"
    )
    
    # Store state for next message
    context.user_data['awaiting_contact'] = True
    logger.info(f"Admin {user_id} initiated setcontact command")


async def handle_setcontact_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the contact username input from admin."""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        return
    
    if not context.user_data.get('awaiting_contact'):
        return
    
    contact = update.message.text.strip()
    
    # Validate format
    if not contact.startswith('@'):
        await update.message.reply_text(
            "❌ Invalid format. Contact must start with '@' (e.g., @username)\n\n"
            "Please try again or use /setcontact to restart."
        )
        return
    
    # Save to database
    await db.set_order_contact(contact)
    
    # Clear state
    context.user_data['awaiting_contact'] = False
    
    # Escape contact for markdown v1
    escaped_contact = escape_markdown_v1(contact)
    
    await update.message.reply_text(
        f"✅ Order contact updated successfully!\n\n"
        f"New contact: {escaped_contact}\n\n"
        f"All users will now be directed to DM {escaped_contact} to place orders.",
        parse_mode="Markdown"
    )
    logger.info(f"Admin {user_id} updated order contact to: {contact}")


async def clearcache_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /clearcache command - clear the file ID cache (admin only)."""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ This command is only available for administrators."
        )
        return
    
    try:
        # Import the cache clearing function
        from utils.helpers import clear_file_id_cache
        
        # Clear the cache
        deleted_count = await clear_file_id_cache()
        
        await update.message.reply_text(
            f"✅ **File ID cache cleared successfully!**\n\n"
            f"Removed {deleted_count} cached file ID entries.\n"
            f"Old product entries will now refresh their media on next view.\n"
            f"This is useful after adding secondary bots as channel admins.",
            parse_mode="Markdown"
        )
        logger.info(f"Admin {user_id} cleared the file ID cache ({deleted_count} entries)")
        
    except Exception as e:
        logger.error(f"Error in clearcache command: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ An error occurred while clearing the cache."
        )


async def botusers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /botusers command - view users grouped by bot username (admin only)."""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ This command is only available for administrators."
        )
        return
    
    try:
        bot_usernames = await db.get_bot_usernames()
        
        if not bot_usernames:
            await update.message.reply_text(
                "📊 **Bot Users Summary**\n\n"
                "No bot usernames found. Users may not have bot_username tracked yet.",
                parse_mode="Markdown"
            )
            return
        
        # Create keyboard with bot instances
        keyboard_buttons = []
        
        for bot_username in bot_usernames:
            count = await db.get_users_count_by_bot(bot_username)
            display_text = f"@{bot_username} ({count} users)"
            keyboard_buttons.append([
                InlineKeyboardButton(display_text, callback_data=f"viewbotusers|{bot_username}|1")
            ])
        
        # Add option to view users without bot_username
        untracked_count = await db.get_users_count_by_bot("_untracked_")
        if untracked_count > 0:
            keyboard_buttons.append([
                InlineKeyboardButton(f"Untracked Users ({untracked_count})", callback_data="viewbotusers|_untracked_|1")
            ])
        
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        total_users = await db.get_users_count_by_bot()
        
        await update.message.reply_text(
            f"📊 **Bot Users by Instance**\n\n"
            f"Total Users: {total_users}\n"
            f"Bot Instances: {len(bot_usernames)}\n\n"
            f"Select a bot to view its users:",
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Error in botusers command: {e}", exc_info=True)
        await update.message.reply_text(
            "❌ An error occurred while retrieving bot users."
        )


async def show_bot_users_page(update: Update, context: ContextTypes.DEFAULT_TYPE, bot_username: str, page: int = 1):
    """Display users for a specific bot with delete options."""
    try:
        query = update.callback_query
        await query.answer()
        
        # Pagination settings
        per_page = 10
        offset = (page - 1) * per_page
        
        # Get users for this bot using the new paginated method
        total_count, users = await db.get_users_by_bot_paginated(bot_username, limit=per_page, offset=offset)
        
        if bot_username == "_untracked_":
            display_name = "Untracked Users"
        else:
            display_name = f"@{bot_username}"
        
        if not users:
            await query.edit_message_text(
                f"📊 **{display_name}**\n\n"
                f"No users found.",
                parse_mode="Markdown"
            )
            return
        
        # Calculate total pages
        total_pages = (total_count + per_page - 1) // per_page
        
        # Build user list message
        message = f"📊 **{display_name}** (Page {page}/{total_pages})\n"
        message += f"Total: {total_count} users\n\n"
        
        keyboard_buttons = []
        
        for user in users:
            user_id = user["user_id"]
            username = user.get("username", "N/A")
            first_name = user.get("first_name", "Unknown")
            last_name = user.get("last_name", "")
            name = f"{first_name} {last_name}".strip()
            
            notif_status = "🔔" if user.get("notifications_enabled", 0) else "🔕"
            block_status = " [BLOCKED]" if user.get("is_blocked", 0) else ""
            
            message += f"• ID: `{user_id}`\n"
            message += f"  User: @{username if username != 'N/A' else 'none'}\n"
            message += f"  Name: {name}\n"
            message += f"  Status: {notif_status}{block_status}\n"
            
            # Add delete button for each user
            keyboard_buttons.append([
                InlineKeyboardButton(
                    f"🗑️ Delete User {user_id}",
                    callback_data=f"deleteuser|{user_id}|{bot_username}|{page}"
                )
            ])
            
            message += "\n"
        
        # Add bulk delete button
        keyboard_buttons.append([
            InlineKeyboardButton(
                f"⚠️ Delete All {total_count} Users from {display_name}",
                callback_data=f"deleteallbot|{bot_username}"
            )
        ])
        
        # Add pagination buttons
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"viewbotusers|{bot_username}|{page-1}"))
        nav_buttons.append(InlineKeyboardButton("🔙 Back to Bots", callback_data="backto_botlist"))
        if page < total_pages:
            nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"viewbotusers|{bot_username}|{page+1}"))
        
        keyboard_buttons.append(nav_buttons)
        
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        await query.edit_message_text(
            message,
            reply_markup=keyboard,
            parse_mode="Markdown"
        )
        
    except Exception as e:
        logger.error(f"Error showing bot users page: {e}", exc_info=True)
        await query.answer("An error occurred", show_alert=True)


async def deleteuser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /deleteuser command - delete a specific user from database (admin only)."""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ This command is only available for administrators."
        )
        return
    
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text(
            "❌ Please provide a user ID.\n\n"
            "Usage: `/deleteuser <user_id>`",
            parse_mode="Markdown"
        )
        return
    
    target_user_id = int(context.args[0])
    
    # Get user info before deletion
    user = await db.get_user_by_id(target_user_id)
    
    if not user:
        await update.message.reply_text(
            f"❌ User with ID `{target_user_id}` not found.",
            parse_mode="Markdown"
        )
        return
    
    username = user.get("username", "N/A")
    first_name = user.get("first_name", "Unknown")
    
    # Create confirmation keyboard
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, Delete", callback_data=f"confirm_deleteuser|{target_user_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel_deleteuser")]
    ])
    
    await update.message.reply_text(
        f"⚠️ **Delete User Confirmation**\n\n"
        f"User ID: `{target_user_id}`\n"
        f"Username: @{username if username != 'N/A' else 'none'}\n"
        f"Name: {first_name}\n\n"
        f"This will completely remove the user from the database.\n"
        f"The user can restart the bot to be re-added as a fresh user.\n\n"
        f"Are you sure?",
        reply_markup=keyboard,
        parse_mode="Markdown"
    )
    logger.info(f"Admin {user_id} requested deletion of user {target_user_id}")




