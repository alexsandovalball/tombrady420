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
            message_lines.append(user_text)
        
        message_text = "\n".join(message_lines)
        
        # Build keyboard with individual toggle buttons
        keyboard_buttons = []
        for user in page_users:
            uid = user['user_id']
            notif_enabled = user.get('notifications_enabled', 1) == 1
            
            # Get user display name
            username = user.get('username') or user.get('first_name', 'Unknown')
            if len(username) > 15:
                username = username[:12] + "..."
            
            button_text = f"{'🔕' if notif_enabled else '🔔'} {username}"
            keyboard_buttons.append([
                InlineKeyboardButton(button_text, callback_data=f"toggle_notif|{uid}|{page}")
            ])
        
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


async def add_product_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /add_product command - manually add a product with category (admin only).
    
    Usage: /add_product <category> <caption>
    Example: /add_product CARTRIDGES Premium Vape Cartridge - Blue Dream
    """
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ This command is only available for administrators."
        )
        return
    
    # Check if message has media
    message = update.message
    if not (message.photo or message.video or message.document or message.animation or message.audio):
        await message.reply_text(
            "❌ Please send /add_product as a caption to a photo, video, or document.\n\n"
            "**Usage:**\n"
            "1. Send a photo/video with caption: `/add_product <category> <description>`\n\n"
            "**Example:**\n"
            "Send a photo with caption:\n"
            "`/add_product CARTRIDGES Premium Vape Cartridge`\n\n"
            "**Available Categories:**\n"
            "CARTRIDGES, EDIBLES, CONCENTRATES, PREROLLS, SHROOMS, FLOWER, DATEDPROOFS, CLIENTTOUCHDOWNS",
            parse_mode="Markdown"
        )
        return
    
    # Parse command arguments from caption
    if not message.caption:
        await message.reply_text(
            "❌ Missing caption. Use format: `/add_product <category> <description>`",
            parse_mode="Markdown"
        )
        return
    
    # Extract category and description from caption
    parts = message.caption.strip().split(None, 2)  # Split into max 3 parts: /add_product, category, description
    
    if len(parts) < 3:
        await message.reply_text(
            "❌ Invalid format. Use: `/add_product <category> <description>`\n\n"
            "**Example:**\n"
            "`/add_product CARTRIDGES Premium Vape Cartridge - Blue Dream`",
            parse_mode="Markdown"
        )
        return
    
    category = parts[1].upper()
    description = parts[2]
    
    # Validate category
    from utils.categories import get_all_categories, get_category_display_name, get_subcategories
    
    if category not in get_all_categories():
        await message.reply_text(
            f"❌ Invalid category: {category}\n\n"
            f"**Available categories:**\n" +
            "\n".join([f"• {cat}" for cat in get_all_categories()]),
            parse_mode="Markdown"
        )
        return
    
    try:
        # Extract file_id and file_type
        from utils.helpers import get_file_id_and_type
        file_id, file_type = get_file_id_and_type(update)
        
        if not file_id:
            await message.reply_text("❌ Could not extract media from message.")
            return
        
        # Check if category has subcategories
        subcategories = get_subcategories(category)
        
        if subcategories:
            # Need to ask for subcategory
            # Save temporary data in context
            context.user_data['pending_product'] = {
                'file_id': file_id,
                'file_type': file_type,
                'caption': description,
                'category': category,
                'message_id': message.message_id,
                'chat_id': message.chat.id
            }
            
            # Show subcategory selection
            keyboard_buttons = []
            for subcat in subcategories:
                keyboard_buttons.append([
                    InlineKeyboardButton(subcat.title(), callback_data=f"addprod_subcat|{subcat}")
                ])
            keyboard_buttons.append([
                InlineKeyboardButton("✅ No Subcategory", callback_data="addprod_subcat|")
            ])
            
            keyboard = InlineKeyboardMarkup(keyboard_buttons)
            
            await message.reply_text(
                f"📂 Category: {get_category_display_name(category)}\n\n"
                f"Please select a subcategory:",
                reply_markup=keyboard
            )
            return
        
        # Add product without subcategory
        product_id = await db.add_product(
            file_id=file_id,
            file_type=file_type,
            caption=description,
            message_id=message.message_id,
            chat_id=message.chat.id,
            category=category,
            subcategory=None
        )
        
        if product_id > 0:
            await message.reply_text(
                f"✅ **Product Added Successfully!**\n\n"
                f"🆔 Product ID: {product_id}\n"
                f"📂 Category: {get_category_display_name(category)}\n"
                f"📝 Description: {description[:100]}...\n\n"
                f"Notifying subscribed users...",
                parse_mode="Markdown"
            )
            
            # Notify users about the new product
            from utils.notifications import NotificationService
            notification_service = NotificationService(db)
            await notification_service.notify_users_about_product(context, product_id)
            
            logger.info(f"Admin {user_id} manually added product {product_id} in category {category}")
        else:
            await message.reply_text("❌ Product already exists or could not be added.")
            
    except Exception as e:
        logger.error(f"Error in add_product command: {e}", exc_info=True)
        await message.reply_text(
            "❌ An error occurred while adding the product."
        )


