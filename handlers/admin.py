# handlers/admin.py
import logging
import sqlite3
import traceback
from typing import List, Dict, Any, Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest

from database import db, DB_PATH  # shared module-level instance and DB path
from utils.helpers import is_admin
from config.taxonomy import CATEGORIES
from utils.taxonomy_utils import validate_category_and_subcategory

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
# Admin interactive Set category callbacks
# -------------------------
async def handle_admin_setcategory_request(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback: admin_setcat|<product_id>
    Show top-level category keyboard to the admin for the given product.
    """
    query = update.callback_query
    if not query:
        return

    try:
        await query.answer()
    except Exception:
        pass

    user = update.effective_user
    if not user or not is_admin(user.id):
        try:
            await query.edit_message_text("❌ You are not authorized to set categories.")
        except Exception:
            logger.debug("Failed to edit message for unauthorized admin_setcat attempt")
        return

    parts = (query.data or "").split("|")
    if len(parts) < 2:
        try:
            await query.answer("Invalid request", show_alert=True)
        except Exception:
            pass
        return

    try:
        product_id = int(parts[1])
    except Exception:
        try:
            await query.answer("Invalid product id", show_alert=True)
        except Exception:
            pass
        return

    # Save product id in user_data for follow-up callbacks
    context.user_data["admin_set_product_id"] = product_id

    # Build category keyboard from CATEGORIES
    buttons = [[InlineKeyboardButton(cat, callback_data=f"category_admin|{cat}")] for cat in CATEGORIES.keys()]
    buttons.append([InlineKeyboardButton("Cancel", callback_data="admin_cancel")])

    try:
        await query.edit_message_text(f"Select category for product {product_id}:", reply_markup=InlineKeyboardMarkup(buttons))
    except Exception:
        logger.exception("Failed to show admin category keyboard for product %s", product_id)


async def handle_category_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback: category_admin|<CATEGORY>
    If the category has subcategories, show them; otherwise set the category immediately.
    """
    query = update.callback_query
    if not query:
        return

    try:
        await query.answer()
    except Exception:
        pass

    user = update.effective_user
    if not user or not is_admin(user.id):
        try:
            await query.edit_message_text("❌ You are not authorized to set categories.")
        except Exception:
            logger.debug("Failed to edit message for unauthorized category_admin attempt")
        return

    parts = (query.data or "").split("|")
    if len(parts) < 2:
        try:
            await query.answer("Invalid request", show_alert=True)
        except Exception:
            pass
        return

    category = parts[1]
    product_id = context.user_data.get("admin_set_product_id")
    if not product_id:
        try:
            await query.edit_message_text("Session expired. Please press Set category again.")
        except Exception:
            logger.debug("Failed to notify about expired admin_set_product_id")
        return

    subs = CATEGORIES.get(category) or []
    # Filter out implicit ALL entries and empty values
    display_subs = [s for s in subs if s and s.strip() and s.strip().upper() != "ALL"]

    if display_subs:
        buttons = []
        for s in display_subs:
            compact = s.strip().upper().replace(" ", "").replace("_", "")
            buttons.append([InlineKeyboardButton(s, callback_data=f"category_sub_admin|{category}|{compact}")])
        buttons.append([InlineKeyboardButton("Cancel", callback_data="admin_cancel")])
        try:
            await query.edit_message_text(f"Select subcategory for {category}:", reply_markup=InlineKeyboardMarkup(buttons))
        except Exception:
            logger.exception("Failed to show admin subcategory keyboard for product %s", product_id)
        return

    # No subcategories: set immediately
    try:
        success = await db.set_product_category(product_id, category, None)
        if success:
            prod = await db.get_product(product_id)
            if prod and prod.get("media_group_id"):
                try:
                    await db.update_products_by_media_group(prod.get("media_group_id"), prod.get("chat_id"), category, None)
                except Exception:
                    logger.exception("Failed to propagate category to media_group for product %s", product_id)
            await query.edit_message_text(f"✅ Product {product_id} categorized as {category}.")
        else:
            await query.edit_message_text("❌ Failed to set category.")
    except Exception:
        logger.exception("Failed to set category for product %s", product_id)
        try:
            await query.edit_message_text("❌ Internal error while setting category.")
        except Exception:
            pass


async def handle_category_sub_admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Callback: category_sub_admin|<CATEGORY>|<COMPACT_SUB>
    Set the category and subcategory for the product and propagate to media_group rows.
    """
    query = update.callback_query
    if not query:
        return

    try:
        await query.answer()
    except Exception:
        pass

    user = update.effective_user
    if not user or not is_admin(user.id):
        try:
            await query.edit_message_text("❌ You are not authorized to set categories.")
        except Exception:
            logger.debug("Failed to edit message for unauthorized category_sub_admin attempt")
        return

    parts = (query.data or "").split("|")
    if len(parts) < 3:
        try:
            await query.answer("Invalid request", show_alert=True)
        except Exception:
            pass
        return

    category = parts[1]
    compact_sub = parts[2]
    product_id = context.user_data.get("admin_set_product_id")
    if not product_id:
        try:
            await query.edit_message_text("Session expired. Please press Set category again.")
        except Exception:
            logger.debug("Failed to notify about expired admin_set_product_id in sub callback")
        return

    sub_to_store = compact_sub.strip().upper()
    try:
        success = await db.set_product_category(product_id, category, sub_to_store)
        if success:
            prod = await db.get_product(product_id)
            if prod and prod.get("media_group_id"):
                try:
                    await db.update_products_by_media_group(prod.get("media_group_id"), prod.get("chat_id"), category, sub_to_store)
                except Exception:
                    logger.exception("Failed to propagate subcategory to media_group for product %s", product_id)
            await query.edit_message_text(f"✅ Product {product_id} categorized as {category}/{sub_to_store}.")
        else:
            await query.edit_message_text("❌ Failed to set category/subcategory.")
    except Exception:
        logger.exception("Failed to set subcategory for product %s", product_id)
        try:
            await query.edit_message_text("❌ Internal error while setting subcategory.")
        except Exception:
            pass


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
        await _safe_reply_text(update, "❌ Nuke failed due to an internal error.")


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
# /listcategories command
# -------------------------
async def list_categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Show the hardwired taxonomy to admins.
    Usage: /listcategories
    """
    user = update.effective_user
    if not user or not is_admin(user.id):
        await _safe_reply_text(update, "❌ You are not authorized to use this command.")
        return

    try:
        lines: List[str] = []
        for cat, subs in CATEGORIES.items():
            if subs:
                # show subcategories as comma-separated
                lines.append(f"{cat} -> {', '.join(subs)}")
            else:
                lines.append(f"{cat} -> (no subcategories)")
        await _safe_reply_text(update, "Allowed categories and subcategories:\n" + "\n".join(lines))
    except Exception:
        logger.exception("Failed to list categories")
        await _safe_reply_text(update, "❌ Failed to list categories.")


# -------------------------
# /listproducts command (admin convenience)
# -------------------------
async def list_products_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    List recent products for admins. Usage: /list_products
    Shows id, category/subcategory, chat and message id, and a short caption preview.
    """
    user = update.effective_user
    if not user or not is_admin(user.id):
        await _safe_reply_text(update, "❌ You are not authorized to use this command.")
        return

    logger.info("list_products_command invoked by admin=%s", user.id)
    try:
        rows = await db.get_all_products()
        if not rows:
            await _safe_reply_text(update, "ℹ️ No products found.")
            return

        lines: List[str] = []
        for r in rows[:50]:
            pid = r.get("id")
            cat = r.get("category") or "None"
            sub = r.get("subcategory") or "None"
            chat = r.get("chat_id")
            msg = r.get("message_id")
            cap = (r.get("caption") or "").replace("\n", " ")[:80]
            lines.append(f"{pid} | {cat} / {sub} | chat={chat} msg={msg} | {cap}")

        text = "Recent products:\n" + "\n".join(lines)
        for chunk in [text[i:i+3900] for i in range(0, len(text), 3900)]:
            await _safe_reply_text(update, chunk)
    except Exception:
        logger.exception("Failed to list products")
        await _safe_reply_text(update, "❌ Failed to list products.")


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

    # Accept category and subcategory with spaces; normalize/validate via taxonomy utils
    category_input = args[1]
    subcategory_input = " ".join(args[2:]) if len(args) > 2 else None

    # Validate against hardwired taxonomy
    cat_valid, sub_valid = validate_category_and_subcategory(category_input, subcategory_input)

    if not cat_valid:
        await _safe_reply_text(update, "❌ Invalid category. Use /listcategories to see allowed values.")
        return

    logger.info("Admin %s setting category for product %s -> %s/%s", user.id, product_id, cat_valid, sub_valid)
    try:
        success = await db.set_product_category(product_id, cat_valid, sub_valid)
        if success:
            await _safe_reply_text(
                update, f"✅ Product {product_id} categorized as '{cat_valid}/{sub_valid or '—'}'."
            )
            logger.info("Admin %s set category for product %s: %s/%s", user.id, product_id, cat_valid, sub_valid)
        else:
            await _safe_reply_text(update, "❌ Failed to set category.")
    except Exception:
        logger.exception("Error setting category for product %s", product_id)
        await _safe_reply_text(update, "❌ Failed to set category due to an internal error.")


# -------------------------
# DB backfill command (admin-only)
# -------------------------
async def _run_sql_and_count(db_path: str, sql: str) -> int:
    """Run a single SQL statement and return number of rows changed."""
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        conn.commit()
        return conn.total_changes
    finally:
        conn.close()


async def backfill_db_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin-only command to normalize category/subcategory values and propagate tags
    to media_group representative rows. Usage: /backfill_db
    Returns the number of rows changed for each step.
    """
    user = update.effective_user
    if not user or not is_admin(user.id):
        try:
            if update.message:
                await update.message.reply_text("❌ You are not authorized to run this command.")
        except Exception:
            pass
        return

    try:
        if update.message:
            await update.message.reply_text("⚙️ Running DB backfill. This may take a moment...")

        # 1) Normalize category and subcategory storage
        normalize_sql = """
        UPDATE products
        SET category = UPPER(TRIM(category)),
            subcategory = CASE
                WHEN subcategory IS NULL OR TRIM(subcategory) = '' THEN NULL
                ELSE UPPER(REPLACE(REPLACE(TRIM(subcategory), ' ', ''), '_', ''))
            END;
        """
        changed_normalize = await _run_sql_and_count(DB_PATH, normalize_sql)

        # 2) Propagate tags to media_group rows (fill missing category/subcategory from any item in the same group)
        propagate_sql = """
        UPDATE products
        SET category = COALESCE(category, (
            SELECT category FROM products p2
            WHERE p2.media_group_id = products.media_group_id AND p2.category IS NOT NULL LIMIT 1
        )),
            subcategory = COALESCE(subcategory, (
            SELECT subcategory FROM products p3
            WHERE p3.media_group_id = products.media_group_id AND p3.subcategory IS NOT NULL LIMIT 1
        ))
        WHERE media_group_id IS NOT NULL;
        """
        changed_propagate = await _run_sql_and_count(DB_PATH, propagate_sql)

        # Report results
        msg = (
            f"✅ Backfill complete.\n"
            f"Rows changed by normalization: {changed_normalize}\n"
            f"Rows changed by media_group propagation: {changed_propagate}"
        )
        if update.message:
            await update.message.reply_text(msg)
    except Exception as e:
        tb = traceback.format_exc()
        try:
            if update.message:
                await update.message.reply_text("❌ Backfill failed. See logs for details.")
        except Exception:
            pass
        # Log full traceback to server logs
        logger.exception("backfill_db_command failed: %s\n%s", e, tb)