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
    await update.message.reply_text(
        f"📊 Total products: {stats.get('total', 0)}\nAdded today: {stats.get('today', 0)}"
    )


async def delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int = None):
    # ... keep your existing delete_product code unchanged ...
    pass


async def nuke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... keep your existing nuke_command code unchanged ...
    pass


async def list_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... keep your existing list_users_command code unchanged ...
    pass


# -------------------------
# NEW: /setcategory command
# -------------------------
async def set_category_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin command to set or override category/subcategory for a product.
    Usage: /setcategory <product_id> <category> [subcategory]
    """
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.message.reply_text("You are not authorized to use this command.")
        return

    args = context.args
    if len(args) < 2:
        await update.message.reply_text("Usage: /setcategory <product_id> <category> [subcategory]")
        return

    try:
        product_id = int(args[0])
    except ValueError:
        await update.message.reply_text("Product ID must be an integer.")
        return

    category = args[1].upper()
    subcategory = " ".join(args[2:]).upper() if len(args) > 2 else None

    success = await db.set_product_category(product_id, category, subcategory)
    if success:
        await update.message.reply_text(
            f"✅ Product {product_id} categorized as '{category}/{subcategory or '—'}'."
        )
        logger.info("Admin %s set category for product %s: %s/%s", user.id, product_id, category, subcategory)
    else:
        await update.message.reply_text("❌ Failed to set category.")