# handlers/debug.py
import logging
from typing import Any
from telegram import Update
from telegram.ext import ContextTypes
from database import Database

logger = logging.getLogger(__name__)
db = Database()

async def dbcheck_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /dbcheck <product_id> — prints DB row for product (safe debug)."""
    if not context.args:
        await update.message.reply_text("Usage: /dbcheck <product_id>")
        return
    try:
        pid = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid id")
        return
    try:
        row = await db.get_product(pid)
        await update.message.reply_text(f"Product {pid}: {row}")
    except Exception:
        logger.exception("dbcheck failed for id=%s", pid)
        await update.message.reply_text("Error reading DB; check server logs.")

async def dump_user_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dump keys and small samples of context.user_data for the current user."""
    ud = context.user_data or {}
    # Avoid dumping large or sensitive values; show types for non-primitive entries
    sample = {}
    for k, v in ud.items():
        if isinstance(v, (str, int, float, bool)):
            sample[k] = v
        else:
            sample[k] = str(type(v))
    await update.message.reply_text(f"user_data keys: {list(ud.keys())}\nsample: {sample}")