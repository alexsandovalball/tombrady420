"""
Product view handler.
"""
import logging
import json
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo, InputMediaDocument
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
                
                # Get bot-specific file IDs for all media in the group
                source_chat_id = product.get("chat_id")
                source_message_id = product.get("message_id")
                bot_instance = context.bot_data.get('bot_instance', 1)
                
                # Check if we have cached file IDs for this bot instance
                cached_files = await db.get_cached_file_ids(product["id"], bot_instance)
                
                # Prepare media list
                media_list = []
                
                # Build list of all file IDs (first + additional)
                all_file_ids = [(product["file_id"], product["file_type"])] + file_data
                
                # For secondary bots without cache, proactively get bot-specific file IDs
                if bot_instance > 1 and (not cached_files or len(cached_files) != len(all_file_ids)):
                    logger.info(f"Proactively getting bot-specific file IDs for media group, product {product['id']}, bot {bot_instance}")
                    try:
                        # Get bot-specific file IDs for all media concurrently for better performance
                        async def get_file_id_task(idx, file_id, file_type):
                            return await get_bot_specific_file_id(
                                context,
                                product["id"],
                                file_id,
                                file_type,
                                source_chat_id,
                                source_message_id,
                                file_index=idx,
                                temp_chat_id=update.effective_chat.id
                            )
                        
                        # Create tasks for concurrent execution
                        tasks = [
                            get_file_id_task(idx, file_id, file_type)
                            for idx, (file_id, file_type) in enumerate(all_file_ids)
                        ]
                        
                        # Execute all tasks concurrently
                        bot_file_ids = await asyncio.gather(*tasks)
                        
                        # Check if any file_ids are None (failed to obtain)
                        if None in bot_file_ids:
                            logger.warning(f"Some bot-specific file IDs could not be obtained for media group, product {product['id']}")
                            # Fall through to try with original IDs
                        else:
                            # Build the list of (file_id, file_type) tuples
                            new_file_ids = [
                                (bot_file_id, file_type)
                                for bot_file_id, (_, file_type) in zip(bot_file_ids, all_file_ids)
                            ]
                            
                            # Use the newly obtained bot-specific file IDs
                            cached_files = new_file_ids
                            logger.info(f"Successfully obtained {len(new_file_ids)} bot-specific file IDs for media group concurrently")
                    except Exception as get_error:
                        logger.error(f"Failed to proactively get bot-specific file IDs: {get_error}")
                        # Fall through to try with original IDs
                
                # Use cached/obtained file IDs if available, otherwise use original
                if cached_files and len(cached_files) == len(all_file_ids):
                    # Use cached or freshly obtained file IDs
                    if bot_instance > 1:
                        logger.debug(f"Using bot-specific media group file IDs for product {product['id']}, bot {bot_instance}")
                    else:
                        logger.debug(f"Using cached media group for product {product['id']}, bot {bot_instance}")
                    for idx, (cached_file_id, cached_file_type) in enumerate(cached_files):
                        if idx == 0:
                            # First media with caption
                            if cached_file_type == "photo":
                                media_list.append(InputMediaPhoto(media=cached_file_id, caption=full_caption))
                            elif cached_file_type == "video":
                                media_list.append(InputMediaVideo(media=cached_file_id, caption=full_caption))
                        else:
                            # Additional media without caption
                            if cached_file_type == "photo":
                                media_list.append(InputMediaPhoto(media=cached_file_id))
                            elif cached_file_type == "video":
                                media_list.append(InputMediaVideo(media=cached_file_id))
                else:
                    # Use original file IDs (may need to cache them after successful send)
                    logger.debug(f"Using original media group for product {product['id']}, bot {bot_instance}")
                    first_type = product["file_type"]
                    first_id = product["file_id"]
                    
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
                    
                    # Add additional media
                    for file_id, file_type in file_data:
                        if file_type == "photo":
                            media_list.append(InputMediaPhoto(media=file_id))
                        elif file_type == "video":
                            media_list.append(InputMediaVideo(media=file_id))
                
                # Send media group
                sent_messages = await context.bot.send_media_group(
                    chat_id=update.effective_chat.id,
                    media=media_list
                )
                
                # Cache file IDs if this is a secondary bot and we used original IDs
                if bot_instance > 1 and (not cached_files or len(cached_files) != len(all_file_ids)):
                    try:
                        # Extract file IDs from sent messages
                        new_file_ids = []
                        for msg in sent_messages:
                            if hasattr(msg, 'photo') and msg.photo:
                                new_file_ids.append((msg.photo[-1].file_id, "photo"))
                            elif hasattr(msg, 'video') and msg.video:
                                new_file_ids.append((msg.video.file_id, "video"))
                        
                        if len(new_file_ids) == len(all_file_ids):
                            await db.cache_file_ids(product["id"], bot_instance, new_file_ids)
                            logger.info(f"Cached media group file IDs for product {product['id']}, bot {bot_instance}")
                    except Exception as cache_error:
                        logger.warning(f"Failed to cache media group file IDs: {cache_error}")
                
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
        bot_instance = context.bot_data.get('bot_instance', 1)
        
        # Check cache for single media
        cached_files = await db.get_cached_file_ids(product["id"], bot_instance)
        
        if cached_files and len(cached_files) > 0:
            # Use cached file ID
            file_id, cached_file_type = cached_files[0]
            logger.debug(f"Using cached file_id for product {product['id']}, bot {bot_instance}")
        else:
            # Use original file ID for bot 1, or proactively get bot-specific file ID for secondary bots
            if bot_instance == 1:
                file_id = product["file_id"]
                logger.debug(f"Using original file_id for product {product['id']}, bot {bot_instance}")
            else:
                # Proactively get bot-specific file ID for secondary bots
                logger.info(f"Proactively getting bot-specific file_id for product {product['id']}, bot {bot_instance}")
                try:
                    source_chat_id = product.get("chat_id")
                    source_message_id = product.get("message_id")
                    
                    file_id = await get_bot_specific_file_id(
                        context,
                        product["id"],
                        product["file_id"],
                        product["file_type"],
                        source_chat_id,
                        source_message_id,
                        file_index=0,
                        temp_chat_id=update.effective_chat.id
                    )
                    
                    if file_id is None:
                        logger.warning(f"Failed to get bot-specific file_id for product {product['id']}, bot {bot_instance}, using fallback")
                        file_id = product["file_id"]
                    else:
                        logger.debug(f"Obtained bot-specific file_id for product {product['id']}, bot {bot_instance}")
                except Exception as get_error:
                    logger.warning(f"Failed to proactively get bot-specific file_id, using original: {get_error}")
                    file_id = product["file_id"]
        
        try:
            await send_media_message(
                context,
                update.effective_chat.id,
                file_id,
                product["file_type"],
                caption=full_caption,
                reply_markup=None  # Don't add keyboard to media message
            )
            
            # Cache the file ID if we successfully sent and didn't have it cached already
            if not cached_files:
                # The file ID worked, so cache it for future use
                await db.cache_file_ids(product["id"], bot_instance, [(file_id, product["file_type"])])
                logger.info(f"Cached file_id for product {product['id']}, bot {bot_instance}")
                
        except Exception as e:
            # If sending failed and we're on a secondary bot, try to get a new file_id as final fallback
            if bot_instance > 1:
                logger.warning(f"Failed to send media, attempting final fallback to get bot-specific file_id: {e}")
                source_chat_id = product.get("chat_id")
                source_message_id = product.get("message_id")
                
                new_file_id = await get_bot_specific_file_id(
                    context,
                    product["id"],
                    product["file_id"],
                    product["file_type"],
                    source_chat_id,
                    source_message_id,
                    file_index=0,
                    temp_chat_id=update.effective_chat.id
                )
                
                if new_file_id is None:
                    # Unable to get bot-specific file_id, send text-only fallback
                    logger.error(f"Unable to obtain bot-specific file_id for product {product['id']}, bot {bot_instance}, sending text-only fallback")
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=f"{full_caption}\n\n⚠️ Media temporarily unavailable. Please try again later.",
                        reply_markup=None
                    )
                else:
                    # Try sending again with the new file_id
                    await send_media_message(
                        context,
                        update.effective_chat.id,
                        new_file_id,
                        product["file_type"],
                        caption=full_caption,
                        reply_markup=None
                    )
            else:
                # Re-raise the exception for bot instance 1
                raise
        
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

