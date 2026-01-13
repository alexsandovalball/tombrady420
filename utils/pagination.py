"""
Pagination utilities for catalog display.
"""
from typing import List, Dict, Any, Tuple
from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def create_pagination_keyboard(
    products: List[Dict[str, Any]],
    page: int,
    total_pages: int,
    callback_prefix: str,
    query: str = None
) -> InlineKeyboardMarkup:
    """
    Create pagination keyboard with product buttons and navigation.
    
    Args:
        products: List of product dictionaries
        page: Current page number (1-indexed)
        total_pages: Total number of pages
        callback_prefix: Prefix for callback data (e.g., "catalog" or "search")
        query: Optional search query for search pagination
    
    Returns:
        InlineKeyboardMarkup with product buttons and pagination controls
    """
    keyboard = []
    
    # Add product buttons (max 5 per page)
    for product in products:
        caption = product.get("caption", "No caption") or "No caption"
        # Trim caption to 50 chars max
        button_text = caption[:47] + "..." if len(caption) > 50 else caption
        if not button_text.strip():
            button_text = f"Product #{product['id']}"
        
        callback_data = f"product|{product['id']}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])
    
    # Add pagination controls if more than 5 products
    if total_pages > 1:
        nav_buttons = []
        
        # Previous button
        if page > 1:
            if query:
                prev_data = f"page|{callback_prefix}|{query}|{page - 1}"
            else:
                prev_data = f"page|{callback_prefix}|{page - 1}"
            nav_buttons.append(InlineKeyboardButton("← Previous", callback_data=prev_data))
        
        # Page indicator
        nav_buttons.append(InlineKeyboardButton(f"Page {page}/{total_pages}", callback_data="noop"))
        
        # Next button
        if page < total_pages:
            if query:
                next_data = f"page|{callback_prefix}|{query}|{page + 1}"
            else:
                next_data = f"page|{callback_prefix}|{page + 1}"
            nav_buttons.append(InlineKeyboardButton("Next →", callback_data=next_data))
        
        keyboard.append(nav_buttons)
    
    return InlineKeyboardMarkup(keyboard)


def paginate_items(items: List[Any], page: int, per_page: int = 5) -> Tuple[List[Any], int]:
    """
    Paginate a list of items.
    
    Args:
        items: List of items to paginate
        page: Current page number (1-indexed)
        per_page: Number of items per page
    
    Returns:
        Tuple of (items_for_page, total_pages)
    """
    total_pages = (len(items) + per_page - 1) // per_page if items else 1
    page = max(1, min(page, total_pages))
    
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    
    return items[start_idx:end_idx], total_pages

