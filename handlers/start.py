from telegram import Update
from telegram.ext import ContextTypes

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("WELCOME! Use /menu to browse the catalog and DM @flyawaypep to order.")
