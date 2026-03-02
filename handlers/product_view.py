"""
Product view handler.
"""
import logging
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo, InputMediaAnimation, InputMediaDocument
from telegram.ext import ContextTypes
from database import Database
from utils.helpers import send_media_message, is_admin, get_bot_specific_file_id, get_user_lang_async
from utils.safe_send import safe_send_message
from utils.pagination import paginate_items
from utils.categories import format_category_info
from translations.translator import translate_text_async, get_translated_string_async

logger = logging.getLogger(__name__)
db = Database()


async def show_product_media(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
    """Show all product media at once (as media group if multiple files)."""
    try:
        product = await db.get_product(product_id)
        
        if not product:
            if update.callback_query:
                await update.callback_query.answer(
                    "❌ Product not found.",
                    show_alert=True
                )
            return
        
        # Get user's pagination state to determine back button behavior
        user_id = update.effective_user.id
        
        # Get the most recent pagination state to determine where user was browsing
        latest_state = await db.get_latest_pagination_state(user_id)
        
        # Determine appropriate back button based on context
        back_callback = "menu|1"  # Default fallback
        
        if latest_state and "state_type" in latest_state and "page" in latest_state:
            state_type = latest_state["state_type"]
            page = latest_state["page"]
            query = latest_state.get("query", "")
            
            # Check if user was browsing a subcategory
            if state_type.startswith("subcategory_"):
                # Extract category and subcategory from state_type
                # Format: subcategory_{category}_{subcategory}
                parts = state_type.split("_", 2)  # Split into at most 3 parts
                if len(parts) >= 3:
                    category = parts[1]
                    subcategory = parts[2]
                    back_callback = f"subcategory|{category}|{subcategory}|{page}"
                else:
                    # Fallback if format is unexpected
                    back_callback = "menu|1"
            # Check if user was browsing a category
            elif state_type.startswith("category_"):
                # Extract category from state_type: category_{categoryname}
                parts = state_type.split("_", 1)
                if len(parts) >= 2:
                    category = parts[1]
                    back_callback = f"category|{category}|{page}"
                else:
                    # Fallback
                    back_callback = "menu|1"
            elif state_type == "search":
                # User was searching - use the query from the state
                if query:
                    # Replace pipe characters to avoid breaking callback data
                    safe_query = query.replace("|", "_PIPE_")
                    # Truncate if needed (Telegram callback_data limit is 64 bytes)
                    max_query_len = 64 - 16
                    if len(safe_query) > max_query_len:
                        safe_query = safe_query[:max_query_len]
                    back_callback = f"page|search|{safe_query}|{page}"
                else:
                    back_callback = "menu|1"
            elif state_type == "catalog":
                # User was browsing all products
                back_callback = f"menu|{page}"
            else:
                # Default fallback
                back_callback = "menu|1"
        else:
            # No pagination state found - user likely came from notification
            # Use product's category/subcategory to determine back button
            product_category = product.get("category")
            product_subcategory = product.get("subcategory")
            
            if product_category and product_subcategory:
                # Product has both category and subcategory - go to subcategory view
                back_callback = f"subcategory|{product_category}|{product_subcategory}|1"
            elif product_category:
                # Product has only category - go to category view
                back_callback = f"category|{product_category}|1"
            # else: keep default "menu|1"
        
        # Get user's language preference
        user_lang = await get_user_lang_async(user_id)
        
        # Get caption and translate it
        caption = product.get("caption") or "No description"
        
        # Translate caption if user language is not English
        if user_lang and user_lang not in ["en", "en-US"]:
            try:
                caption = await translate_text_async(caption, user_lang)
            except Exception as e:
                logger.error(f"Error translating caption: {e}")
                # Keep original caption if translation fails
        
        category_info = format_category_info(product.get("category"), product.get("subcategory"), user_lang)
        
        # Collect all media information
        additional_file_ids = product.get("additional_file_ids")
        media_list = [(product["file_id"], product["file_type"])]
        
        if additional_file_ids:
            try:
                file_data = json.loads(additional_file_ids)
                media_list.extend(file_data)
            except Exception as e:
                logger.error(f"Error parsing additional_file_ids: {e}")
        
        total_media = len(media_list)
        media_group_id = product.get("media_group_id")
        logger.info(
            f"show_product_media: product_id={product_id}, "
            f"raw_media_count={total_media}, "
            f"media_group_id={media_group_id}"
        )
        
        # Try to get bot-specific file IDs if needed
        product_bot = product.get("bot_username")
        current_bot = context.bot.username
        
        using_copy_path = False
        if (product_bot is None or current_bot is None or product_bot.lower() != current_bot.lower()):
            # Try to get bot-specific file IDs for all media
            additional_message_ids_json = product.get("additional_message_ids")
            if additional_message_ids_json:
                try:
                    additional_msg_ids = json.loads(additional_message_ids_json)
                    message_ids = [product["message_id"]] + additional_msg_ids
                    
                    # Update file IDs with bot-specific ones
                    updated_media_list = []
                    for idx, (file_id, file_type) in enumerate(media_list):
                        if idx < len(message_ids):
                            bot_specific_id = await get_bot_specific_file_id(
                                context,
                                product["chat_id"],
                                message_ids[idx],
                                file_type,
                                file_index=idx
                            )
                            if bot_specific_id:
                                updated_media_list.append((bot_specific_id, file_type))
                            else:
                                updated_media_list.append((file_id, file_type))
                        else:
                            updated_media_list.append((file_id, file_type))
                    media_list = updated_media_list
                    using_copy_path = True
                except Exception as e:
                    logger.warning(f"Could not get bot-specific file IDs: {e}")
        
        logger.debug(
            f"show_product_media: product_id={product_id}, "
            f"deduped_media_count={len(media_list)}, "
            f"copy_path={using_copy_path}"
        )
        
        # Build full caption
        full_caption = f"{caption}\n\n📂 {category_info}"
        
        # Create keyboard with back button and admin buttons
        keyboard_buttons = []
        
        # Add back button
        keyboard_buttons.append([
            InlineKeyboardButton("🔙 Back to results", callback_data=back_callback)
        ])
        
        # Add delete and recategorize buttons for admins
        if is_admin(user_id):
            keyboard_buttons.append([
                InlineKeyboardButton(
                    "🔄 Recategorize",
                    callback_data=f"recategorize|{product_id}"
                ),
                InlineKeyboardButton(
                    "🗑 Delete this product",
                    callback_data=f"delete|{product_id}"
                )
            ])
        
        keyboard = InlineKeyboardMarkup(keyboard_buttons)
        
        # Send all media at once
        if total_media == 1:
            # Single media - send with caption
            file_id, file_type = media_list[0]
            await send_media_message(
                context,
                update.effective_chat.id,
                file_id,
                file_type,
                caption=full_caption,
                reply_markup=None
            )
        else:
            # Multiple media - send as media group
            try:
                media_group = []
                unsupported_media = []  # Track media that can't be in groups
                caption_used = False  # Track if caption has been added to any media
                
                # Helper to get caption (only for first media item)
                def get_caption():
                    nonlocal caption_used
                    if not caption_used:
                        caption_used = True
                        return full_caption
                    return None
                
                for idx, (file_id, file_type) in enumerate(media_list):
                    if file_type == "photo":
                        media_group.append(InputMediaPhoto(media=file_id, caption=get_caption()))
                    elif file_type == "video":
                        media_group.append(InputMediaVideo(media=file_id, caption=get_caption()))
                    elif file_type == "animation":
                        media_group.append(InputMediaAnimation(media=file_id, caption=get_caption()))
                    elif file_type == "document":
                        # Documents can be in media groups (unlike audio/voice/video_note)
                        media_group.append(InputMediaDocument(media=file_id, caption=get_caption()))
                    elif file_type in ["audio", "voice", "video_note"]:
                        # These types cannot be in media groups - send separately
                        # Caption will be added to first media item encountered
                        unsupported_media.append((file_id, file_type, get_caption()))
                    else:
                        # Unknown type - log and skip
                        logger.warning(f"Unknown media type '{file_type}' for product {product_id}, skipping")
                
                # Send media group if we have any supported media
                if media_group:
                    logger.info(
                        f"show_product_media: product_id={product_id}, "
                        f"sending media_group with {len(media_group)} items"
                    )
                    await context.bot.send_media_group(
                        chat_id=update.effective_chat.id,
                        media=media_group
                    )
                
                # Send unsupported media types individually
                for file_id, file_type, item_caption in unsupported_media:
                    await send_media_message(
                        context,
                        update.effective_chat.id,
                        file_id,
                        file_type,
                        caption=item_caption,
                        reply_markup=None
                    )
                    
            except Exception as e:
                logger.exception(f"Error sending media group: {e}")
                logger.info(
                    f"show_product_media: product_id={product_id}, "
                    f"using album fallback (individual send) for {len(media_list)} items"
                )
                # Fallback: send all media individually
                for idx, (file_id, file_type) in enumerate(media_list):
                    # Add caption only to first media
                    item_caption = full_caption if idx == 0 else None
                    try:
                        await send_media_message(
                            context,
                            update.effective_chat.id,
                            file_id,
                            file_type,
                            caption=item_caption,
                            reply_markup=None
                        )
                    except Exception as send_error:
                        logger.error(f"Failed to send media {idx + 1}/{total_media}: {send_error}")
        
        # Get order contact
        order_contact = await db.get_order_contact()
        
        # Get translated DM to order message
        dm_message = await get_translated_string_async("dm_to_order", user_lang, contact=order_contact)
        
        # Send DM message with keyboard separately
        await safe_send_message(
            context.bot,
            update.effective_chat.id,
            text=dm_message,
            parse_mode=None,
            reply_markup=keyboard
        )
        
        if update.callback_query:
            await update.callback_query.answer()
        
    except Exception as e:
        logger.exception(f"Error showing product media: {e}")
        if update.callback_query:
            await update.callback_query.answer(
                "❌ An error occurred while loading the product.",
                show_alert=True
            )

async def handle_product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle product button callback."""
    callback_data = update.callback_query.data
    parts = callback_data.split("|")
    
    if len(parts) == 2 and parts[0] == "product":
        product_id = int(parts[1])
        await show_product_media(update, context, product_id)
    else:
        await update.callback_query.answer("Invalid callback data", show_alert=True)

