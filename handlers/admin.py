"""
Admin handlers for album ingestion, preview, categorization, and deletion.
"""

from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, Message, InputMediaPhoto, InputMediaVideo
from telegram.ext import ContextTypes, CallbackQueryHandler, MessageHandler, filters, Application
from database import (
    add_media_group_meta,
    mark_media_group_previewed,
    add_product,
    product_exists,
    is_admin,
    get_db,
)
from utils.helpers import build_category_keyboard, build_subcategory_keyboard, select_representative_media
from config.config import ADMIN_IDS, CATEGORIES

# In-memory cache for collecting media group messages
media_group_cache = {}

async def album_ingest(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handler for new media group messages in the channel.
    Collects all messages in the group, then triggers preview for admins.
    """
    message = update.message
    if not message.media_group_id:
        return
    chat_id = message.chat_id
    media_group_id = message.media_group_id
    # Collect messages in cache
    group = media_group_cache.setdefault(media_group_id, [])
    group.append(message)
    # Wait for all messages in the group (Telegram sends them in quick succession)
    if len(group) == message.media_group_total_count:
        # All messages received, process album
        await process_album(group, context)
        # Remove from cache
        del media_group_cache[media_group_id]

async def process_album(messages: list, context: ContextTypes.DEFAULT_TYPE):
    """
    Process a complete album: deduplication, preview to admins, categorization.
    """
    media_group_id = messages[0].media_group_id
    chat_id = messages[0].chat_id
    # Deduplication: skip if all media already exist
    all_exist = True
    media_list = []
    for msg in messages:
        media = None
        if msg.photo:
            file_id = msg.photo[-1].file_id
            file_unique_id = msg.photo[-1].file_unique_id
            media_type = "photo"
        elif msg.video:
            file_id = msg.video.file_id
            file_unique_id = msg.video.file_unique_id
            media_type = "video"
        else:
            continue
        caption = msg.caption or ""
        exists = await product_exists(file_unique_id)
        if not exists:
            all_exist = False
        media_list.append({
            "file_id": file_id,
            "file_unique_id": file_unique_id,
            "media_type": media_type,
            "caption": caption,
        })
    if all_exist:
        return  # Album already ingested
    # Preview to admins
    rep_media = select_representative_media(media_list)
    for admin_id in ADMIN_IDS:
        if rep_media["media_type"] == "photo":
            await context.bot.send_photo(
                chat_id=admin_id,
                photo=rep_media["file_id"],
                caption=f"New album received. Please categorize.\n\n{rep_media['caption']}",
                reply_markup=build_category_keyboard(list(CATEGORIES.keys()))
            )
        elif rep_media["media_type"] == "video":
            await context.bot.send_video(
                chat_id=admin_id,
                video=rep_media["file_id"],
                caption=f"New album received. Please categorize.\n\n{rep_media['caption']}",
                reply_markup=build_category_keyboard(list(CATEGORIES.keys()))
            )
    # Store album meta
    await add_media_group_meta(media_group_id, chat_id)
    # Store media_list in context for categorization step
    context.chat_data[media_group_id] = media_list

async def admin_category_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin selects a category for the album.
    """
    query = update.callback_query
    await query.answer()
    category = query.data.split(":", 1)[1]
    # Show subcategories
    keyboard = build_subcategory_keyboard(category)
    await query.edit_message_text(
        f"Select a subcategory for {category}:", reply_markup=keyboard
    )
    # Store category in context
    context.user_data["pending_category"] = category

async def admin_subcategory_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Admin selects a subcategory; commit album to catalog.
    """
    query = update.callback_query
    await query.answer()
    _, category, subcategory = query.data.split(":")
    # Retrieve pending album from context
    media_group_id = context.user_data.get("pending_media_group_id")
    media_list = context.chat_data.get(media_group_id, [])
    # Store products in DB
    for media in media_list:
        await add_product(
            media_group_id=media_group_id,
            file_unique_id=media["file_unique_id"],
            file_id=media["file_id"],
            media_type=media["media_type"],
            caption=media["caption"],
            category=category,
            subcategory=subcategory,
        )
    await mark_media_group_previewed(media_group_id)
    await query.edit_message_text("Album categorized and added to the catalog.")

def register_handlers(application: Application):
    """
    Register admin handlers with the application.
    """
    application.add_handler(MessageHandler(filters.MEDIA_GROUP, album_ingest))
    application.add_handler(CallbackQueryHandler(admin_category_selected, pattern=r"^cat:"))
    application.add_handler(CallbackQueryHandler(admin_subcategory_selected, pattern=r"^sub:"))