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
    """Handle /users command - view all bot users (admin only)."""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ This command is only available for administrators."
        )
        return
    
    try:
        users = await db.get_all_users()
        total_users = len(users)
        
        if total_users == 0:
            await update.message.reply_text(
                "📭 No users have interacted with the bot yet."
            )
            return
        
        # Create user list text (show first 50)
        user_list_parts = []
        for i, user in enumerate(users[:50], 1):
            # Escape Markdown characters in user data to prevent parsing errors
            username_raw = user.get('username', '')
            username = f"@{escape_markdown(username_raw)}" if username_raw else "No username"
            first_name = escape_markdown(user.get('first_name', 'N/A'))
            last_name = escape_markdown(user.get('last_name', ''))
            full_name = f"{first_name} {last_name}".strip() if last_name else first_name
            
            last_seen = user.get('last_seen', 'Unknown')
            if last_seen != 'Unknown':
                # Format datetime
                from datetime import datetime
                try:
                    dt = datetime.fromisoformat(last_seen)
                    last_seen = dt.strftime("%Y-%m-%d %H:%M")
                except:
                    pass
            
            user_text = (
                f"{i}. **{full_name}** ({username})\n"
                f"   🆔 ID: `{user['user_id']}`\n"
                f"   📊 Interactions: {user.get('interaction_count', 0)}\n"
                f"   🕐 Last seen: {last_seen}"
            )
            user_list_parts.append(user_text)
        
        # Split into multiple messages if needed (Telegram message limit is 4096 chars)
        message_text = f"👥 **Bot Users** ({total_users} total)\n\n"
        
        current_message = message_text
        for user_text in user_list_parts:
            if len(current_message) + len(user_text) + 2 > 4000:
                # Send current message and start new one
                await update.message.reply_text(current_message, parse_mode="Markdown")
                current_message = user_text + "\n\n"
            else:
                current_message += user_text + "\n\n"
        
        # Send remaining text
        if current_message != message_text:
            if total_users > 50:
                current_message += f"\n_Showing first 50 of {total_users} users_"
            await update.message.reply_text(current_message, parse_mode="Markdown")
        
        logger.info(f"Admin {user_id} viewed user list ({total_users} users)")
        
    except Exception as e:
        logger.error(f"Error in users command: {e}")
        await update.message.reply_text(
            "❌ An error occurred while retrieving user list."
        )


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
        
        # Delete from channel if possible
        if product.get("message_id") and product.get("chat_id"):
            try:
                # Ensure chat_id is an integer (SQLite might return it as string)
                chat_id = int(product["chat_id"])
                message_id = int(product["message_id"])
                
                # Use the stored chat_id from the product
                await context.bot.delete_message(
                    chat_id=chat_id,
                    message_id=message_id
                )
                logger.info(f"✅ Deleted message {message_id} from channel {chat_id}")
            except Exception as e:
                error_msg = str(e)
                logger.error(f"❌ Could not delete message from channel: {error_msg}")
                logger.error(f"   chat_id: {product.get('chat_id')}, message_id: {product.get('message_id')}")
                # Show user a more informative error
                await update.callback_query.answer(
                    f"⚠️ Product deleted from catalog, but couldn't delete from channel: {error_msg[:50]}",
                    show_alert=True
                )
                # Continue with database deletion even if channel deletion fails
        
        # Delete from database
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

