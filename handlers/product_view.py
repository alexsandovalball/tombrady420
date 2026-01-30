"""
Product view handler.
"""
import logging
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputMediaPhoto, InputMediaVideo, InputMediaDocument
from telegram.ext import ContextTypes
from database import Database
from utils.helpers import send_media_message, is_admin
from utils.pagination import paginate_items
from utils.categories import format_category_info

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
        
        if latest_state and "state_type" in latest_state and "page" in latest_state and "query" in latest_state:
            state_type = latest_state["state_type"]
            page = latest_state["page"]
            query = latest_state["query"]
            
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
            elif state_type.startswith("category_") and query:
                # The query field contains the category name
                # Note: Categories are controlled values from CATEGORIES dict, so they are safe
                back_callback = f"category|{query}|{page}"
            elif state_type == "catalog":
                # User was browsing all products
                back_callback = f"menu|{page}"
            else:
                # Default fallback
                back_callback = "menu|1"
        else:
            # Check last search (if user came from search)
            last_search = await db.get_last_search(user_id)
            if last_search and last_search.get("query"):
                # Use the stored query and page for search results
                query = last_search['query']
                page = last_search['page']
                # Replace pipe characters in query to avoid breaking callback data
                # Also truncate if too long (Telegram callback_data limit is 64 bytes)
                safe_query = query.replace("|", "_PIPE_")
                # Estimate: "page|search|" = 12, "|" + page (max 3 digits) = 4, so ~16 bytes overhead
                max_query_len = 64 - 16
                if len(safe_query) > max_query_len:
                    safe_query = safe_query[:max_query_len]
                back_callback = f"page|search|{safe_query}|{page}"
            else:
                back_callback = "menu|1"  # Default to catalog page 1
        
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
        
        # Get caption and add category info
        caption = product.get("caption") or "No description"
        category_info = format_category_info(product.get("category"), product.get("subcategory"))
        
        # Add category to caption
        full_caption = f"{caption}\n\n📂 {category_info}"
        
        # Check if this is a media group (has additional files)
        additional_file_ids = product.get("additional_file_ids")
        
        if additional_file_ids:
            # This is a media group - send as album
            try:
                file_data = json.loads(additional_file_ids)
                
                # Prepare media list
                media_list = []
                
                # Add first media (with caption)
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
                await context.bot.send_media_group(
                    chat_id=update.effective_chat.id,
                    media=media_list
                )
                
                # Send keyboard in a separate message since media groups can't have keyboards
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text="👆 DM TO ORDER:@FLYAWAYPEP",
                    reply_markup=keyboard
                )
                
                await update.callback_query.answer()
                return
                
            except Exception as e:
                logger.error(f"Error sending media group: {e}")
                # Fall back to single media
        
        # Send single media with caption
        await send_media_message(
            context,
            update.effective_chat.id,
            product["file_id"],
            product["file_type"],
            caption=full_caption,
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

