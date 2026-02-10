"""
Product view handler.
"""
import logging
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo
from telegram.ext import ContextTypes
from database import Database
from utils.helpers import send_media_message, is_admin, get_bot_specific_file_id
from utils.pagination import paginate_items
from utils.categories import format_category_info
from translations.translator import translate_text_async, get_translated_string_async

logger = logging.getLogger(__name__)
db = Database()


async def show_product(update: Update, context: ContextTypes.DEFAULT_TYPE, product_id: int):
    """Show a single product with media and details."""
    try:
        product = await db.get_product(product_id)
        
        if not product:
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
        
        # Create keyboard
        keyboard_buttons = [
            [InlineKeyboardButton("🔙 Back to results", callback_data=back_callback)]
        ]
        
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
        
        # Get user's language preference
        user_lang = await db.get_user_language(user_id)
        
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
        
        # Add category to caption
        full_caption = f"{caption}\n\n📂 {category_info}"
        
        # Check if this is a media group (has additional files)
        additional_file_ids = product.get("additional_file_ids")
        
        if additional_file_ids:
            # This is a media group - send as album
            try:
                file_data = json.loads(additional_file_ids)
                
                # Check if we need bot-specific file ID resolution
                product_bot = product.get("bot_username")
                current_bot = context.bot.username
                # Handle None values for bot usernames
                needs_bot_specific_ids = (
                    product_bot is None or 
                    current_bot is None or 
                    product_bot.lower() != current_bot.lower()
                )
                
                # Parse additional message IDs if available
                additional_message_ids_json = product.get("additional_message_ids")
                message_ids = []
                use_bot_specific_ids = False
                
                if additional_message_ids_json:
                    try:
                        additional_msg_ids = json.loads(additional_message_ids_json)
                        # Build complete message ID list: [first_message_id, ...additional_message_ids]
                        message_ids = [product["message_id"]] + additional_msg_ids
                        # Use bot-specific IDs if different bot OR no message IDs stored
                        use_bot_specific_ids = needs_bot_specific_ids
                    except (json.JSONDecodeError, TypeError) as e:
                        logger.warning(f"Failed to parse additional_message_ids: {e}")
                        use_bot_specific_ids = False
                else:
                    # No message IDs - need bot-specific resolution if different bot
                    if needs_bot_specific_ids:
                        # We need message IDs for bot-specific resolution
                        # Estimate sequential message IDs (may not be accurate for all cases)
                        logger.warning(
                            f"Product {product_id} has no stored message IDs - estimating sequential IDs. "
                            f"This may fail if messages weren't sent sequentially."
                        )
                        # Build message ID list from the first message ID with correct sequential numbering
                        # message_ids[0] = base, message_ids[1] = base+1, message_ids[2] = base+2, etc.
                        message_ids = [product["message_id"] + i for i in range(len(file_data) + 1)]
                        use_bot_specific_ids = True
                    else:
                        use_bot_specific_ids = False
                
                # Prepare media list
                media_list = []
                
                # Add first media (with caption)
                first_type = product["file_type"]
                first_id = product["file_id"]
                chat_id = product["chat_id"]
                
                # Try to get bot-specific file ID for the first media if message IDs are available
                if use_bot_specific_ids:
                    bot_specific_id = await get_bot_specific_file_id(
                        context,
                        chat_id,
                        message_ids[0],
                        first_type,
                        file_index=0
                    )
                    # Use bot-specific ID if available, otherwise fall back to original
                    first_id = bot_specific_id or first_id
                
                if first_type == "photo":
                    media_list.append(InputMediaPhoto(media=first_id, caption=full_caption))
                elif first_type == "video":
                    media_list.append(InputMediaVideo(media=first_id, caption=full_caption))
                else:
                    # For other types, fall back to single message
                    await send_media_message(
                        context,
                        update.effective_chat.id,
                        first_id,
                        first_type,
                        caption=full_caption,
                        reply_markup=keyboard
                    )
                    await update.callback_query.answer()
                    return
                
                # Add additional media with bot-specific file IDs if available
                for idx, (file_id, file_type) in enumerate(file_data, start=1):
                    if use_bot_specific_ids:
                        # Verify we have a message ID for this file
                        if idx < len(message_ids):
                            # Get the corresponding message ID
                            msg_id = message_ids[idx]
                            
                            # Try to get bot-specific file ID
                            bot_specific_id = await get_bot_specific_file_id(
                                context,
                                chat_id,
                                msg_id,
                                file_type,
                                file_index=idx
                            )
                            
                            # Use bot-specific ID if available, otherwise fall back to original
                            file_id = bot_specific_id or file_id
                        else:
                            # Message ID missing for this file - log warning and use original
                            logger.warning(
                                f"Message ID missing for file {idx} in product {product_id} "
                                f"(have {len(message_ids)} IDs, need {len(file_data)+1})"
                            )
                    
                    if file_type == "photo":
                        media_list.append(InputMediaPhoto(media=file_id))
                    elif file_type == "video":
                        media_list.append(InputMediaVideo(media=file_id))
                
                # Send media group
                await context.bot.send_media_group(
                    chat_id=update.effective_chat.id,
                    media=media_list
                )
                
                # Get order contact
                order_contact = await db.get_order_contact()
                
                # Get translated DM to order message
                dm_message = await get_translated_string_async("dm_to_order", user_lang, contact=order_contact)
                
                # Send keyboard in a separate message since media groups can't have keyboards
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=dm_message,
                    reply_markup=keyboard
                )
                
                await update.callback_query.answer()
                return
                
            except Exception as e:
                logger.error(f"Error sending media group: {e}")
                # Fall back to single media
        
        # Send single media with caption
        # Check if we need bot-specific file ID
        product_bot = product.get("bot_username")
        current_bot = context.bot.username
        file_id_to_use = product["file_id"]
        
        # Handle None values for bot usernames
        if (product_bot is None or 
            current_bot is None or 
            product_bot.lower() != current_bot.lower()):
            # Try to get bot-specific file ID for single media
            bot_specific_id = await get_bot_specific_file_id(
                context,
                product["chat_id"],
                product["message_id"],
                product["file_type"],
                file_index=0
            )
            if bot_specific_id:
                file_id_to_use = bot_specific_id
                logger.debug(f"Using bot-specific file ID for product {product_id}")
            else:
                logger.warning(f"Could not get bot-specific file ID for product {product_id}, using original")
        
        await send_media_message(
            context,
            update.effective_chat.id,
            file_id_to_use,
            product["file_type"],
            caption=full_caption,
            reply_markup=None  # Don't add keyboard to media message
        )
        
        # Get order contact
        order_contact = await db.get_order_contact()
        
        # Get translated DM to order message
        dm_message = await get_translated_string_async("dm_to_order", user_lang, contact=order_contact)
        
        # Send DM message with keyboard separately for consistency
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=dm_message,
            reply_markup=keyboard
        )
        
        await update.callback_query.answer()
        
    except Exception as e:
        logger.error(f"Error showing product: {e}")
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
        await show_product(update, context, product_id)
    else:
        await update.callback_query.answer("Invalid callback data", show_alert=True)

