"""
Main entry point for the Telegram media catalog bot.
"""
import sys
import os
import logging
import re
import asyncio
from datetime import datetime
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
)

# Ensure root logger writes to stdout so hosts capture runtime logs
root = logging.getLogger()
if not root.handlers:
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    root.addHandler(stream_handler)
root.setLevel(logging.INFO)

# Use the module-level db instance (database.py exposes `db` and DB_PATH)
from database import db, DB_PATH

# Utility helpers used by handlers defined in this module
from utils.helpers import get_file_id_and_type, has_media, get_channel_id, get_channel_username
from utils.categories import extract_category_and_subcategory

# Load environment variables
load_dotenv()

# Configure module logger
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# NOTE: use the shared module-level db instance imported above
user_last_message = {}


async def channel_post_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle new posts in the monitored channel and save each media item with media_group_id."""
    try:
        message = update.channel_post
        if not message:
            return

        chat_id = message.chat.id
        msg_id = message.message_id
        media_group_id = getattr(message, "media_group_id", None)

        if await db.is_message_ignored(msg_id, chat_id):
            return
        if not has_media(update):
            return

        file_id, file_type = get_file_id_and_type(update)
        if not file_id:
            return

        caption = message.caption if getattr(message, "caption", None) else (message.text or "")
        caption = caption.strip() if isinstance(caption, str) else ""

        if media_group_id and not caption:
            try:
                existing = await db.get_products_by_media_group(media_group_id, chat_id)
                for ex in existing:
                    ex_caption = (ex.get("caption") or "").strip()
                    if ex_caption:
                        caption = ex_caption
                        break
            except Exception:
                logger.exception("Error while looking up existing media_group captions")

        try:
            # ingest_and_save_product will be imported and bound into globals() inside build_application()
            product_id = await ingest_and_save_product(
                message=message,
                context=context,
                file_id=file_id,
                file_type=file_type,
                msg_id=msg_id,
                chat_id=chat_id,
                media_group_id=media_group_id,
            )

            if product_id and int(product_id) > 0:
                logger.info(
                    "New product saved: ID=%s, type=%s (channel=%s msg=%s)",
                    product_id,
                    file_type,
                    chat_id,
                    msg_id,
                )
            else:
                logger.warning("Product save returned non-positive id for file_id=%s", file_id)
        except Exception:
            logger.exception("Failed to ingest and save product for file_id=%s", file_id)

    except Exception as e:
        logger.error(f"Error handling channel post: {e}", exc_info=True)


def _split_callback(data: str):
    """Split callback data into (action, value)."""
    if not data:
        return None, None
    data = data.strip()
    parts = re.split(r"[|_]", data)
    if len(parts) == 1:
        return parts[0], None
    action = "_".join(parts[:-1])
    value = parts[-1]
    return action, value


async def callback_query_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generic handler for inline button callbacks."""
    query = update.callback_query
    if not query:
        return

    user = update.effective_user
    if user:
        try:
            await db.save_user(
                user_id=user.id,
                username=user.username or None,
                first_name=user.first_name or None,
                last_name=user.last_name or None,
                last_seen=datetime.utcnow(),
            )
        except Exception:
            logger.debug("Failed to save user info")

    try:
        await query.answer()
    except Exception:
        logger.exception("Failed to answer callback query")

    data = (query.data or "").strip()
    action, value = _split_callback(data)

    try:
        if action in ("catalog_page", "catalogpage", "catalog"):
            # This branch is kept for backward compatibility; wrapper in build_application
            page = int(value) if value else 1
            await handle_catalog_pagination(update, context, page)
            return

        if action in ("search_page", "searchpage", "search"):
            parts = data.split("|")
            if len(parts) >= 3:
                query_text = parts[1]
                page = int(parts[2])
                await handle_search_pagination(update, context, query_text, page)
            return

        if action in ("search_back", "backsearch"):
            await handle_back_to_search(update, context)
            return

        if action in ("product", "productview", "item"):
            await handle_product_callback(update, context)
            return

        if action in ("delete", "remove"):
            await handle_delete_callback(update, context)
            return

        if action in ("menu", "catalog_menu"):
            page = int(value) if value else 1
            await handle_catalog_pagination(update, context, page)
            return

        if action == "noop":
            return

        logger.warning("Unknown callback action: %s", data)
        try:
            await query.answer("Unknown action", show_alert=True)
        except Exception:
            pass

    except Exception:
        logger.exception("Unhandled exception in callback_query_handler")


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Treat all non-command messages as search queries (private chats)."""
    # DEBUG: log incoming message metadata to help diagnose search issues
    try:
        if update and getattr(update, "message", None):
            logger.debug(
                "message_handler invoked: user=%s text=%s chat_type=%s",
                getattr(update.effective_user, "id", None),
                getattr(update.message, "text", None),
                getattr(update.effective_chat, "type", None),
            )
    except Exception:
        logger.exception("Failed to log incoming message in message_handler")

    if update.message and update.message.text and update.message.text.startswith("/"):
        return
    if update.channel_post:
        return

    user = update.effective_user
    if user:
        try:
            await db.save_user(
                user_id=user.id,
                username=user.username or None,
                first_name=user.first_name or None,
                last_name=user.last_name or None,
                last_seen=datetime.utcnow(),
            )
        except Exception:
            logger.debug("Failed to save user info")

    if not user:
        return

    user_id = user.id
    current_ts = asyncio.get_event_loop().time()
    last_ts = user_last_message.get(user_id, 0)
    if current_ts - last_ts < 1.0:
        if update.message:
            await update.message.reply_text("⏳ Please wait a moment before sending another message.")
        return

    user_last_message[user_id] = current_ts

    if update.message and update.message.text:
        try:
            await handle_search(update, context)
        except Exception:
            logger.exception("Error while handling search command")


async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(f"Update {update} caused error {context.error}", exc_info=True)
    if update and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="❌ An unexpected error occurred. Please try again later.",
            )
        except Exception:
            logger.exception("Failed to notify user about an unexpected error")


async def post_init(application: Application):
    try:
        # Ensure DB is initialized during async startup
        await db.init_db()
        logger.info("Database initialized")
        # Log the DB_PATH used by the running process so you can verify which DB file is active
        try:
            logger.info("Using DB_PATH: %s", DB_PATH)
        except Exception:
            logger.exception("Failed to log DB_PATH")
    except Exception as e:
        logger.exception("Failed to initialize database: %s", e)
        raise


def build_application():
    """
    Create the Application instance, register handlers, and return it.
    All imports of handler modules are performed here to avoid import-time side effects.
    """
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        logger.error("BOT_TOKEN not found in environment.")
        raise RuntimeError("BOT_TOKEN not set")

    application = Application.builder().token(bot_token).post_init(post_init).build()

    # Import handler callables here (deferred import to avoid import-time side effects)
    try:
        # Command handlers and callbacks
        from handlers.start import start_command
        from handlers.menu import (
            menu_command,
            handle_catalog_pagination,
            handle_category_callback,
            handle_category_sub_callback,
            handle_menu_categories_callback,
        )
        from handlers.admin import (
            stats_command,
            delete_product,
            nuke_command,
            list_users_command,
            set_category_command,
            handle_nuke_step1,
            handle_nuke_final,
            handle_nuke_cancel,
            list_products_command,
            backfill_db_command,
            handle_admin_setcategory_request,
            handle_category_admin_callback,
            handle_category_sub_admin_callback,
        )
        from handlers.search import handle_search, handle_search_pagination, handle_back_to_search
        from handlers.product_view import (
            show_product,
            handle_product_callback,
            handle_delete_callback,
            ingest_and_save_product,
        )
        from handlers.debug import dbcheck_command, dump_user_data

        # Bind imported names into this module's globals so the top-level handlers can call them
        globals().update(
            {
                "start_command": start_command,
                "menu_command": menu_command,
                "handle_catalog_pagination": handle_catalog_pagination,
                "handle_category_callback": handle_category_callback,
                "handle_category_sub_callback": handle_category_sub_callback,
                "handle_menu_categories_callback": handle_menu_categories_callback,
                "stats_command": stats_command,
                "delete_product": delete_product,
                "nuke_command": nuke_command,
                "list_users_command": list_users_command,
                "set_category_command": set_category_command,
                "handle_nuke_step1": handle_nuke_step1,
                "handle_nuke_final": handle_nuke_final,
                "handle_nuke_cancel": handle_nuke_cancel,
                "list_products_command": list_products_command,
                "backfill_db_command": backfill_db_command,
                "handle_admin_setcategory_request": handle_admin_setcategory_request,
                "handle_category_admin_callback": handle_category_admin_callback,
                "handle_category_sub_admin_callback": handle_category_sub_admin_callback,
                "handle_search": handle_search,
                "handle_search_pagination": handle_search_pagination,
                "handle_back_to_search": handle_back_to_search,
                "show_product": show_product,
                "handle_product_callback": handle_product_callback,
                "handle_delete_callback": handle_delete_callback,
                "ingest_and_save_product": ingest_and_save_product,
                "dbcheck_command": dbcheck_command,
                "dump_user_data": dump_user_data,
            }
        )
    except Exception:
        logger.exception("Failed to import handler modules; check handler filenames and package layout")
        raise

    # Command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("nuke", nuke_command))
    application.add_handler(CommandHandler("users", list_users_command))
    application.add_handler(CommandHandler("setcategory", set_category_command))
    # Admin convenience command to inspect deployed DB
    application.add_handler(CommandHandler("list_products", list_products_command))
    # Admin DB backfill (normalize categories / propagate media_group tags)
    application.add_handler(CommandHandler("backfill_db", backfill_db_command))

    # Debug commands
    application.add_handler(CommandHandler("dbcheck", dbcheck_command))
    application.add_handler(CommandHandler("dumpuserdata", dump_user_data))

    # Specific callback query handlers FIRST (more specific patterns before generic)
    application.add_handler(CallbackQueryHandler(handle_nuke_step1, pattern="^nuke_step1_yes$"))
    application.add_handler(CallbackQueryHandler(handle_nuke_final, pattern="^nuke_final_yes$"))
    application.add_handler(CallbackQueryHandler(handle_nuke_cancel, pattern="^nuke_cancel$"))

    # Admin interactive tagging callbacks (register from handlers.admin)
    application.add_handler(CallbackQueryHandler(handle_admin_setcategory_request, pattern=r"^admin_setcat\|"))
    application.add_handler(CallbackQueryHandler(handle_category_admin_callback, pattern=r"^category_admin\|"))
    application.add_handler(CallbackQueryHandler(handle_category_sub_admin_callback, pattern=r"^category_sub_admin\|"))

    # Category navigation handlers (must be registered before the generic router)
    application.add_handler(CallbackQueryHandler(handle_category_callback, pattern=r"^category\|"))
    application.add_handler(CallbackQueryHandler(handle_category_sub_callback, pattern=r"^category_sub\|"))
    application.add_handler(CallbackQueryHandler(handle_menu_categories_callback, pattern=r"^menu_categories$"))

    # Wrap catalog pagination handler so we extract page from callback data and call the existing handler
    def _wrap_catalog_pagination(handler):
        async def _inner(update, context):
            try:
                data = (update.callback_query.data or "").strip()
                parts = data.split("|")
                # Expecting patterns like: category_page|CATEGORY|SUBCATEGORY|<page>
                # Last part should be page if present; fallback to 1
                page = 1
                if parts:
                    # If last part is numeric, use it; otherwise default to 1
                    last = parts[-1]
                    try:
                        page = int(last) if last != "" else 1
                    except Exception:
                        # If last part is not numeric, try to find numeric part in the list
                        for p in reversed(parts):
                            try:
                                page = int(p)
                                break
                            except Exception:
                                continue
                await handler(update, context, page)
            except Exception:
                logger.exception("Wrapped catalog pagination handler failed")
        return _inner

    application.add_handler(CallbackQueryHandler(_wrap_catalog_pagination(handle_catalog_pagination), pattern=r"^category_page\|"))

    # Product and delete specific handlers (so they are handled before the generic router)
    application.add_handler(CallbackQueryHandler(handle_product_callback, pattern=r"^(product\||\d+$)"))
    application.add_handler(CallbackQueryHandler(handle_delete_callback, pattern=r"^delete\|"))

    # Generic callback query handler LAST
    application.add_handler(CallbackQueryHandler(callback_query_handler))

    # Message handlers
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE, message_handler)
    )

    # Add a test command to invoke search directly for debugging
    async def _test_search_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
        try:
            await handle_search(update, context)
        except Exception:
            logger.exception("test_search_cmd failed")

    application.add_handler(CommandHandler("testsearch", _test_search_cmd))

    application.add_error_handler(error_handler)

    # Channel monitoring
    channel_id = get_channel_id()
    channel_username = get_channel_username()
    if channel_id:
        application.add_handler(
            MessageHandler(
                filters.Chat(chat_id=channel_id) & filters.ChatType.CHANNEL,
                channel_post_handler,
            )
        )
        logger.info(f"Channel monitoring enabled for {channel_id}")
    elif channel_username:
        application.add_handler(
            MessageHandler(
                filters.Chat(username=channel_username.lstrip("@")) & filters.ChatType.CHANNEL,
                channel_post_handler,
            )
        )
        logger.info(f"Channel monitoring enabled for {channel_username}")
    else:
        logger.warning("No CHANNEL_ID or CHANNEL_USERNAME set. Channel monitoring disabled.")

    return application


# Expose application object for webhook_server.py
application = build_application()

if __name__ == "__main__":
    logger.info("Starting bot in polling mode...")
    try:
        application.run_polling(
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
    except Exception as e:
        logger.exception("Application run_polling exited with exception: %s", e)
        raise