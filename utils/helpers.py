"""
Utility functions for media handling, deduplication, pagination, and inline keyboard generation.
"""

from typing import List, Dict, Any, Optional
from telegram import InlineKeyboardMarkup, InlineKeyboardButton
from config.config import CATEGORIES, PRODUCTS_PER_PAGE

def build_category_keyboard(categories: List[str]) -> InlineKeyboardMarkup:
    """
    Build an inline keyboard for category selection.
    """
    buttons = [
        [InlineKeyboardButton(text=cat, callback_data=f"cat:{cat}")]
        for cat in categories
    ]
    return InlineKeyboardMarkup(buttons)

def build_subcategory_keyboard(category: str) -> InlineKeyboardMarkup:
    """
    Build an inline keyboard for subcategory selection.
    """
    subcats = CATEGORIES.get(category, [])
    buttons = [
        [InlineKeyboardButton(text=sub, callback_data=f"sub:{category}:{sub}")]
        for sub in subcats
    ]
    buttons.append([InlineKeyboardButton(text="Back", callback_data="back:categories")])
    return InlineKeyboardMarkup(buttons)

def build_product_pagination_keyboard(
    category: str, subcategory: str, page: int, total_pages: int
) -> InlineKeyboardMarkup:
    """
    Build an inline keyboard for paginated product listings.
    """
    buttons = []
    if page > 1:
        buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"page:{category}:{subcategory}:{page-1}"))
    if page < total_pages:
        buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"page:{category}:{subcategory}:{page+1}"))
    nav_row = buttons if buttons else [InlineKeyboardButton("Back", callback_data=f"sub:{category}")]
    return InlineKeyboardMarkup([nav_row])

def select_representative_media(media_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Select the media with the most informative caption as the representative.
    """
    captioned = [m for m in media_list if m.get("caption")]
    if captioned:
        # Prefer the longest caption
        return max(captioned, key=lambda m: len(m["caption"]))
    return media_list[0] if media_list else {}

def paginate(items: List[Any], page: int, per_page: int = PRODUCTS_PER_PAGE) -> List[Any]:
    """
    Return a slice of items for the given page.
    """
    start = (page - 1) * per_page
    end = start + per_page
    return items[start:end]

def total_pages(items: List[Any], per_page: int = PRODUCTS_PER_PAGE) -> int:
    """
    Calculate total number of pages.
    """
    return (len(items) + per_page - 1) // per_page