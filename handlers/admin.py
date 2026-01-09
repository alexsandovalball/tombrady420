# handlers/admin.py
import logging
from typing import List, Dict, Any, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from database import db  # shared module-level instance
from utils.helpers import is_admin

logger = logging.getLogger(__name__)


# -------------------------
# /stats command
# -------------------------
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show catalog statistics: total products and those added today."""
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    try:
        stats = await db.get_stats()
        total = stats.get("total", 0)
        today = stats.get("today", 0)
        await update.message.reply_text(f"📊 Total products: {total}\n📅 Added today: {today}")
    except Exception as e:
        logger.exception("Failed to fetch stats: %s", e)
        await update.message.reply_text("❌ Failed to fetch stats.")


# -------------------------
# /delete command
# -------------------------
async def delete_product(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Delete a single product by ID.
    Usage: /delete <product_id>
    """
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    if not context.args:
        await update.message.reply_text("Usage: /delete <product_id>")
        return

    try:
        product_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Product ID must be an integer.")
        return

    try:
        deleted = await db.delete_product(product_id)
        if deleted:
            await update.message.reply_text(f"✅ Deleted product {product_id}.")
            logger.info("Admin %s deleted product %s", user.id, product_id)
        else:
            await update.message.reply_text(f"ℹ️ Product {product_id} not found or already deleted.")
    except Exception as e:
        logger.exception("Failed to delete product %s: %s", product_id, e)
        await update.message.reply_text("❌ Failed to delete product due to an internal error.")


# -------------------------
# /nuke command with inline buttons
# -------------------------
async def nuke_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Two-step inline-button confirmation for nuking all products.
    Step 1: /nuke -> prompt with Yes/No buttons.
    Step 2: If Yes -> second, irreversible confirmation with Yes/Cancel.
    """
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    keyboard = [
        [
            InlineKeyboardButton("✅ Yes", callback_data="nuke_step1_yes"),
            InlineKeyboardButton("❌ No", callback_data="nuke_cancel"),
        ]
    ]
    await update.message.reply_text(
        "⚠️ Are you sure you want to delete ALL products?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_nuke_step1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle first confirmation click for nuke."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user or not is_admin(user.id):
        await query.edit_message_text("❌ You are not authorized to use this command.")
        return

    keyboard = [
        [
            InlineKeyboardButton("✅ Yes, delete all", callback_data="nuke_final_yes"),
            InlineKeyboardButton("❌ Cancel", callback_data="nuke_cancel"),
        ]
    ]
    await query.edit_message_text(
        "⚠️ This action is irreversible. Do you really want to nuke everything?",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_nuke_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle final confirmation click and perform nuke."""
    query = update.callback_query
    await query.answer()

    user = update.effective_user
    if not user or not is_admin(user.id):
        await query.edit_message_text("❌ You are not authorized to use this command.")
        return

    try:
        products: List[Dict[str, Any]] = await db.get_all_products()
        total = len(products)
        if total == 0:
            await query.edit_message_text("✅ Nuke complete. No products to delete.")
            return

        deleted_count = 0
        for p in products:
            try:
                if await db.delete_product(p["id"]):
                    deleted_count += 1
            except Exception:
                logger.exception("Error deleting product id=%s during nuke", p.get("id"))

        await query.edit_message_text(f"✅ Nuke complete. Deleted {deleted_count} products.")
        logger.warning("NUKE executed by user_id=%s; deleted=%s out of %s", user.id, deleted_count, total)
    except Exception as e:
        logger.exception("Nuke failed: %s", e)
        await query.edit_message_text("❌ Nuke failed due to an internal error.")


async def handle_nuke_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cancel click for nuke."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("❌ Nuke cancelled.")


# -------------------------
# /users command
# -------------------------
async def list_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    List users who interacted with the bot, ordered by last_seen.
    Usage: /users
    """
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    try:
        users = await db.get_all_users()
        if not users:
            await update.message.reply_text("ℹ️ No users found.")
            return

        max_rows = 50
        lines: List[str] = []
        for i, u in enumerate(users[:max_rows], start=1):
            uid = u.get("user_id")
            uname = u.get("username") or "—"
            first = u.get("first_name") or ""
            last = u.get("last_name") or ""
            name = (first + " " + last).strip() or "—"
            last_seen = u.get("last_seen") or "—"
            lines.append(f"{i}. ID: {uid} | @{uname} | {name} | last_seen: {last_seen}")

        suffix = "" if len(users) <= max_rows else f"\n…and {len(users) - max_rows} more"
        await update.message.reply_text("👥 Users:\n" + "\n".join(lines) + suffix)
    except Exception as e:
        logger.exception("Failed to list users: %s", e)
        await update.message.reply_text("❌ Failed to list users.")


# -------------------------
# /setcategory command
# -------------------------
async def set_category_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin command to set or override category/subcategory for a product.
    Usage: /setcategory <product_id> <category> [subcategory]
    """
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.message.reply_text("❌ You are not authorized to use this command.")
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

    category = args[1].upper().strip()
    subcategory: Optional[str] = " ".join(args[2:]).upper().strip() if len(args) > 2 else None
    if subcategory == "":
        subcategory = None

    try:
        success = await db.set_product_category(product_id, category, subcategory)
        if success:
            await update.message.reply_text(
                f"✅ Product {product_id} categorized as '{category}/{subcategory or '—'}'."
            )
            logger.info("Admin %s set category for product %s: %s/%s", user.id, product_id, category, subcategory)
        else:
            await update.message.reply_text("❌ Failed to set category.")
    except Exception as e:
        logger.exception("Error setting category for product %s: %s", product_id, e)
        await update.message.reply_text("❌ Failed to set category due to an internal error.")