# handlers/admin.py
import logging
from telegram import Update
from telegram.ext import ContextTypes
from database import Database
from utils.helpers import is_admin

logger = logging.getLogger(__name__)
db = Database()


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.message.reply_text("You are not authorized to use this command.")
        return
    stats = await db.get_stats()
    await update.message.reply_text(f"📊 Total products: {stats.get('total', 0)}\nAdded today: {stats.get('today', 0)}")


async def delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int = None):
    """
    Delete a product by ID. Can be invoked as a callback (update.callback_query) or command.
    Only admins can delete. Deletion removes DB entry and attempts to delete the original channel message.
    If channel message deletion fails (e.g., message already removed), DB entry is still removed and the message
    is added to the ignored list so it won't be reprocessed.
    """
    caller = update.effective_user
    if not caller or not is_admin(caller.id):
        if update.callback_query:
            await update.callback_query.answer("You are not authorized to delete products.", show_alert=True)
        else:
            await update.message.reply_text("You are not authorized to delete products.")
        return

    # Parse product_id from message text if not provided
    if product_id is None:
        if update.message and update.message.text:
            parts = update.message.text.strip().split()
            if len(parts) >= 2 and parts[1].isdigit():
                product_id = int(parts[1])

    if product_id is None:
        if update.callback_query:
            await update.callback_query.answer("Invalid product id.", show_alert=True)
        else:
            await update.message.reply_text("Invalid product id.")
        return

    product = await db.get_product(product_id)
    if not product:
        if update.callback_query:
            await update.callback_query.answer("Product not found.", show_alert=True)
        else:
            await update.message.reply_text("Product not found.")
        return

    chat_id = product.get("chat_id")
    message_id = product.get("message_id")

    # Attempt to delete message from channel (if present)
    deletion_ok = False
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
        deletion_ok = True
    except Exception as e:
        logger.warning("Could not delete message from channel: %s (chat_id=%s, message_id=%s)", e, chat_id, message_id)

    # Remove from DB (always)
    removed = await db.delete_product(product_id)

    # Add to ignored_messages so channel handler won't re-add it if reposted
    try:
        await db.add_ignored_message(message_id, chat_id)
    except Exception as e:
        logger.warning("Failed to add ignored message (chat_id=%s, message_id=%s): %s", chat_id, message_id, e)

    # Notify admin with clear result
    result_text = f"🗑️ Product {product_id} deleted. Channel deletion: {'success' if deletion_ok else 'failed (message not found)'}."
    if update.callback_query:
        try:
            await update.callback_query.answer()
        except Exception:
            pass
        try:
            await update.callback_query.message.reply_text(result_text)
        except Exception:
            try:
                await context.bot.send_message(chat_id=caller.id, text=result_text)
            except Exception:
                pass
    else:
        try:
            await update.message.reply_text(result_text)
        except Exception:
            try:
                await context.bot.send_message(chat_id=caller.id, text=result_text)
            except Exception:
                pass

    logger.info("Admin %s deleted product %s (channel deletion: %s)", caller.id, product_id, deletion_ok)


async def nuke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin-only command to delete all products (requires /nuke CONFIRM).
    """
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.message.reply_text("You are not authorized to use this command.")
        return

    args = context.args or []
    if not args or args[0].upper() != "CONFIRM":
        await update.message.reply_text("To nuke the catalog, run: /nuke CONFIRM")
        return

    products = await db.get_all_products()
    for p in products:
        try:
            await context.bot.delete_message(chat_id=p["chat_id"], message_id=p["message_id"])
        except Exception:
            pass
        await db.add_ignored_message(p["message_id"], p["chat_id"])
        await db.delete_product(p["id"])

    await update.message.reply_text("🧨 Catalog nuked.")
    logger.info("Admin %s nuked the catalog", user.id)
