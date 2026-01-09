# handlers/admin.py
import logging
from typing import List, Dict, Any, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from database import db  # shared module-level instance
from utils.helpers import is_admin

logger = logging.getLogger(__name__)


# -------------------------
# Helpers
# -------------------------
def _keyboard_step1() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Yes", callback_data="nuke_step1_yes"),
                InlineKeyboardButton("❌ No", callback_data="nuke_cancel"),
            ]
        ]
    )


def _keyboard_final() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ Yes, delete all", callback_data="nuke_final_yes"),
                InlineKeyboardButton("❌ Cancel", callback_data="nuke_cancel"),
            ]
        ]
    )


async def _safe_reply_text(update: Update, text: str):
    """Reply to a message if present, otherwise send to chat."""
    try:
        if update.message:
            await update.message.reply_text(text)
        elif update.effective_chat:
            await update.effective_chat.send_message(text)
    except Exception:
        logger.exception("Failed to send reply text: %s", text)


async def _safe_edit_message(query, text: str, reply_markup: Optional[InlineKeyboardMarkup] = None):
    """Edit message text safely, catching common Telegram errors."""
    try:
        if reply_markup is not None:
            await query.edit_message_text(text, reply_markup=reply_markup)
        else:
            await query.edit_message_text(text)
    except BadRequest as e:
        # MessageNotModified or other BadRequest; log and try to answer user
        logger.debug("BadRequest while editing message: %s", e)
        try:
            await query.answer()  # ensure callback acknowledged
        except Exception:
            logger.debug("Failed to answer callback after BadRequest")
    except Exception:
        logger.exception("Unexpected error while editing message")


# -------------------------
# /stats command
# -------------------------
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show catalog statistics: total products and those added today."""
    user = update.effective_user
    if not user or not is_admin(user.id):
        await _safe_reply_text(update, "❌ You are not authorized to use this command.")
        return

    logger.info("stats_command invoked by admin=%s", user.id)
    try:
        stats = await db.get_stats()
        total = stats.get("total", 0)
        today = stats.get("today", 0)
        await _safe_reply_text(update, f"📊 Total products: {total}\n📅 Added today: {today}")
    except Exception:
        logger.exception("Failed to fetch stats")
        await _safe_reply_text(update, "❌ Failed to fetch stats.")


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
        await _safe_reply_text(update, "❌ You are not authorized to use this command.")
        return

    if not context.args:
        await _safe_reply_text(update, "Usage: /delete <product_id>")
        return

    try:
        product_id = int(context.args[0])
    except ValueError:
        await _safe_reply_text(update, "Product ID must be an integer.")
        return

    logger.info("Admin %s requested delete for product %s", user.id, product_id)
    try:
        deleted = await db.delete_product(product_id)
        if deleted:
            await _safe_reply_text(update, f"✅ Deleted product {product_id}.")
            logger.info("Admin %s deleted product %s", user.id, product_id)
        else:
            await _safe_reply_text(update, f"ℹ️ Product {product_id} not found or already deleted.")
    except Exception:
        logger.exception("Failed to delete product %s", product_id)
        await _safe_reply_text(update, "❌ Failed to delete product due to an internal error.")


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
        await _safe_reply_text(update, "❌ You are not authorized to use this command.")
        return

    logger.warning("Admin %s initiated nuke command (step1)", user.id)
    try:
        if update.message:
            await update.message.reply_text(
                "⚠️ Are you sure you want to delete ALL products?",
                reply_markup=_keyboard_step1(),
            )
        else:
            await _safe_reply_text(update, "⚠️ Are you sure you want to delete ALL products?")
    except Exception:
        logger.exception("Failed to send nuke step1 prompt")
        await _safe_reply_text(update, "❌ Failed to initiate nuke due to an internal error.")


async def handle_nuke_step1(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle first confirmation click for nuke."""
    query = update.callback_query
    if not query:
        logger.warning("handle_nuke_step1 called without callback_query")
        return

    try:
        await query.answer()
    except Exception:
        logger.debug("Failed to answer callback in handle_nuke_step1")

    user = update.effective_user
    if not user or not is_admin(user.id):
        logger.warning("Unauthorized nuke_step1 attempt by user=%s", user.id if user else None)
        await _safe_edit_message(query, "❌ You are not authorized to use this command.")
        return

    logger.info("handle_nuke_step1 invoked by admin=%s", user.id)
    await _safe_edit_message(
        query,
        "⚠️ This action is irreversible. Do you really want to nuke everything?",
        reply_markup=_keyboard_final(),
    )


async def handle_nuke_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle final confirmation click and perform nuke."""
    query = update.callback_query
    if not query:
        logger.warning("handle_nuke_final called without callback_query")
        return

    try:
        await query.answer()
    except Exception:
        logger.debug("Failed to answer callback in handle_nuke_final")

    user = update.effective_user
    if not user or not is_admin(user.id):
        logger.warning("Unauthorized nuke_final attempt by user=%s", user.id if user else None)
        await _safe_edit_message(query, "❌ You are not authorized to use this command.")
        return

    logger.warning("NUKE final confirmed by admin=%s", user.id)
    try:
        products: List[Dict[str, Any]] = await db.get_all_products()
        total = len(products)
        if total == 0:
            await _safe_edit_message(query, "✅ Nuke complete. No products to delete.")
            return

        deleted_count = 0
        for p in products:
            pid = p.get("id")
            try:
                if pid is None:
                    continue
                if await db.delete_product(pid):
                    deleted_count += 1
            except Exception:
                logger.exception("Error deleting product id=%s during nuke", pid)

        await _safe_edit_message(query, f"✅ Nuke complete. Deleted {deleted_count} products.")
        logger.warning("NUKE executed by user_id=%s; deleted=%s out of %s", user.id, deleted_count, total)
    except Exception:
        logger.exception("Nuke failed during execution")
        await _safe_edit_message(query, "❌ Nuke failed due to an internal error.")


async def handle_nuke_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle cancel click for nuke."""
    query = update.callback_query
    if not query:
        logger.warning("handle_nuke_cancel called without callback_query")
        return

    try:
        await query.answer()
    except Exception:
        logger.debug("Failed to answer callback in handle_nuke_cancel")

    logger.info("Nuke cancelled by user=%s", update.effective_user.id if update.effective_user else None)
    try:
        await _safe_edit_message(query, "❌ Nuke cancelled.")
    except Exception:
        logger.exception("Failed to edit message on nuke cancel")


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
        await _safe_reply_text(update, "❌ You are not authorized to use this command.")
        return

    logger.info("list_users_command invoked by admin=%s", user.id)
    try:
        users = await db.get_all_users()
        if not users:
            await _safe_reply_text(update, "ℹ️ No users found.")
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
        await _safe_reply_text(update, "👥 Users:\n" + "\n".join(lines) + suffix)
    except Exception:
        logger.exception("Failed to list users")
        await _safe_reply_text(update, "❌ Failed to list users.")


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
        await _safe_reply_text(update, "❌ You are not authorized to use this command.")
        return

    args = context.args
    if len(args) < 2:
        await _safe_reply_text(update, "Usage: /setcategory <product_id> <category> [subcategory]")
        return

    try:
        product_id = int(args[0])
    except ValueError:
        await _safe_reply_text(update, "Product ID must be an integer.")
        return

    category = args[1].upper().strip()
    subcategory: Optional[str] = " ".join(args[2:]).upper().strip() if len(args) > 2 else None
    if subcategory == "":
        subcategory = None

    logger.info("Admin %s setting category for product %s -> %s/%s", user.id, product_id, category, subcategory)
    try:
        success = await db.set_product_category(product_id, category, subcategory)
        if success:
            await _safe_reply_text(
                update, f"✅ Product {product_id} categorized as '{category}/{subcategory or '—'}'."
            )
            logger.info("Admin %s set category for product %s: %s/%s", user.id, product_id, category, subcategory)
        else:
            await _safe_reply_text(update, "❌ Failed to set category.")
    except Exception:
        logger.exception("Error setting category for product %s", product_id)
        await _safe_reply_text(update, "❌ Failed to set category due to an internal error.")