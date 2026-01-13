# handlers/language.py
"""
User language preference handlers:
 - /setlang <code>  (set preference, e.g. /setlang en)
 - /mylang          (show current preference or detected language)
 - /langs           (list supported language codes from the translation endpoint)
"""
from __future__ import annotations
import logging
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from utils.translate import TRANSLATE_API_URL, translate_text
from database import db

logger = logging.getLogger(__name__)

async def cmd_mylang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    try:
        pref = await db.get_user_language(user.id)
        det = getattr(user, "language_code", None)
        text = f"Detected language: {det or 'unknown'}\n"
        text += f"Preferred language: {pref or 'not set'}\n"
        text += "Use /setlang <code> to set your preferred language (e.g. /setlang en)."
        await update.message.reply_text(text)
    except Exception:
        logger.exception("cmd_mylang failed")

async def cmd_setlang(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return
    args = context.args or []
    if not args:
        await update.message.reply_text("Usage: /setlang <language_code> (e.g. en, es, fr)")
        return
    code = args[0].lower()
    try:
        # store preference in DB
        ok = await db.set_user_language(user.id, code)
        if ok:
            await update.message.reply_text(f"Saved preferred language: {code}")
        else:
            await update.message.reply_text("Failed to save preference (server error).")
    except Exception:
        logger.exception("cmd_setlang failed")
        await update.message.reply_text("Failed to set language due to server error.")

async def cmd_langs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Query the translate provider for supported languages if possible.
    try:
        # LibreTranslate supports /languages; try to fetch
        import httpx
        url = TRANSLATE_API_URL.replace("/translate", "/languages")
        async with httpx.AsyncClient(timeout=6.0) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                # expected list of {"code":"en","name":"English"}
                items = [f"{it.get('code')}: {it.get('name')}" for it in data]
                text = "Supported languages:\n" + ", ".join(items)
                await update.message.reply_text(text)
                return
    except Exception:
        logger.debug("Failed to query translate /languages endpoint; falling back to examples")
    # Fallback small list
    await update.message.reply_text("Common codes: en (English), es (Spanish), fr (French), de (German), pt (Portuguese), ru (Russian), zh (Chinese)")