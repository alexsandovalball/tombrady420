"""
Admin handlers.
"""
import logging
import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
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
    """Handle /nuke command - deletes ALL products (admin only)."""
    user_id = update.effective_user.id
    
    if not is_admin(user_id):
        await update.message.reply_text(
            "❌ This command is only available for administrators."
        )
        return
    
    try:
        # Get all products before deletion to add them to ignore list
        all_products = await db.get_all_products_for_search()
        total_count = len(all_products)
        
        if total_count == 0:
            await update.message.reply_text(
                "📭 The catalog is already empty. Nothing to delete."
            )
            return
        
        # Add all products to ignore list
        for product in all_products:
            if product.get("message_id") and product.get("chat_id"):
                await db.add_ignored_message(
                    int(product["message_id"]),
                    int(product["chat_id"])
                )
        
        # Delete all products from database
        async with aiosqlite.connect(db.db_path) as conn:
            cursor = await conn.execute("DELETE FROM products")
            await conn.commit()
            deleted_count = cursor.rowcount
        
        text = (
            f"💥 **NUKE COMPLETE**\n\n"
            f"🗑️ Deleted {deleted_count} product(s)\n"
            f"🚫 Added to ignore list to prevent re-addition"
        )
        
        await update.message.reply_text(text, parse_mode="Markdown")
        logger.warning(f"Admin {user_id} executed /nuke command - deleted {deleted_count} products")
        
    except Exception as e:
        logger.error(f"Error executing nuke command: {e}")
        await update.message.reply_text(
            "❌ An error occurred while deleting products."
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
        
        # Add to ignore list so it won't be re-added if the post still exists in channel
        if deleted and product.get("message_id") and product.get("chat_id"):
            await db.add_ignored_message(
                int(product["message_id"]),
                int(product["chat_id"])
            )
        
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

