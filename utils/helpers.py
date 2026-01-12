 url=https://github.com/alexsandovalball/tombrady420/blob/main/utils/helpers.py
# utils/helpers.py
"""
Helper utilities: logging setup, pagination keyboard builder, small helpers.

This module consolidates:
 - logging setup
 - chunked helper
 - short_caption helper (used for keyboard labels)
 - delete_last_displayed_messages (async)
 - safe scope truncation for callback_data
 - build_pagination_keyboard (used by catalog/product handlers)
 - create_pagination_keyboard + paginate_items (compatibility helpers merged from utils.pagination)
"""

from __future__ import annotations

import logging
from typing import List, Iterator, Sequence, Optional, Any, Tuple
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import BadRequest

logger = logging.getLogger(__name__)


def setup_logging(level: str = "INFO") -> None:
    """
    Configure root logging once. Call early in application startup.

    `level` may be a string like "DEBUG", "INFO", etc.
    """
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=lvl, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger().setLevel(lvl)


def chunked(seq: Sequence, n: int) -> Iterator[Sequence]:
    """
    Yield successive n-sized chunks from seq.

    Example:
        list(chunked([1,2,3,4,5], 2)) -> [[1,2], [3,4], [5]]
    """
    if n <= 0:
        raise ValueError("n must be a positive integer")
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _safe_scope_for_callback(scope: str, max_len: int = 40) -> str:
    """
    Ensure the scope string used in callback_data is not excessively long.
    Truncates long scopes and replaces whitespace with underscores.

    Telegram limits callback_data to 64 bytes; keep scope reasonably short.
    """
    if not scope:
        return ""
    s = scope.replace(" ", "_")
    if len(s) <= max_len:
        return s
    head = s[: max_len // 2 - 2]
    tail = s[-(max_len // 2 - 2) :]
    return f"{head}..{tail}"


def short_caption(caption: Optional[str], length: int = 40) -> str:
    """
    Return a short, single-line caption suitable for keyboard labels.

    - returns "No caption" for empty/None captions
    - collapses newlines to spaces and trims
    - truncates with an ellipsis if longer than `length`
    """
    if not caption:
        return "No caption"
    c = caption.strip().replace("\n", " ")
    if len(c) <= length:
        return c
    return c[: length - 1].rstrip() + "…"


async def delete_last_displayed_messages(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Delete messages recorded in context.user_data['last_displayed_messages'].

    Each entry is expected to be a (chat_id, message_id) tuple/list.
    The function is defensive: ignores failures and clears the stored list.
    """
    bot = context.bot
    last = context.user_data.get("last_displayed_messages") or []
    if not last:
        return
    for chat_id, message_id in last:
        try:
            await bot.delete_message(chat_id=chat_id, message_id=message_id)
        except BadRequest:
            logger.debug("Could not delete displayed message %s/%s (BadRequest)", chat_id, message_id)
        except Exception:
            logger.debug("Could not delete displayed message %s/%s", chat_id, message_id)
    context.user_data.pop("last_displayed_messages", None)


def build_pagination_keyboard(scope: str, page: int, per_page: int, total: int) -> List[List[InlineKeyboardButton]]:
    """
    Build a simple pagination keyboard.

    Args:
      scope: string identifying the scope (category or "category::subcategory").
      page: zero-based page index.
      per_page: items per page.
      total: total number of items.

    Returns:
      A list of keyboard rows (each row is a list of InlineKeyboardButton).
    """
    keyboard: List[List[InlineKeyboardButton]] = []
    try:
        total_pages = max(1, (int(total) + int(per_page) - 1) // int(per_page))
    except Exception:
        total_pages = 1

    try:
        page = int(page)
    except Exception:
        page = 0
    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1

    safe_scope = _safe_scope_for_callback(scope or "")

    row: List[InlineKeyboardButton] = []
    if page > 0:
        row.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"CAT:PAGE:{safe_scope}::{page-1}"))
    row.append(InlineKeyboardButton(text=f"Page {page+1}/{total_pages}", callback_data=f"CAT:PAGE:{safe_scope}::{page}"))
    if page + 1 < total_pages:
        row.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"CAT:PAGE:{safe_scope}::{page+1}"))
    keyboard.append(row)

    keyboard.append([InlineKeyboardButton(text="Back", callback_data="CAT:BACK")])
    return keyboard


# Compatibility functions merged from previous utils.pagination
def create_pagination_keyboard(
    products: List[dict],
    page: int,
    total_pages: int,
    callback_prefix: str,
    query: Optional[str] = None,
) -> InlineKeyboardMarkup:
    """
    Create a pagination InlineKeyboardMarkup. This is a convenience wrapper used
    by a few older modules; prefer the lighter `build_pagination_keyboard` for
    catalog-style flows.
    """
    keyboard: List[List[InlineKeyboardButton]] = []
    # Product buttons (trim)
    for product in products:
        caption = product.get("caption", "") or "No caption"
        button_text = caption[:47] + "..." if len(caption) > 50 else caption
        if not button_text.strip():
            button_text = f"Product #{product.get('id')}"
        callback_data = f"product|{product.get('id')}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

    # Navigation
    if total_pages > 1:
        nav_buttons: List[InlineKeyboardButton] = []
        if page > 1:
            prev_data = f"page|{callback_prefix}|{query}|{page - 1}" if query else f"page|{callback_prefix}|{page - 1}"
            nav_buttons.append(InlineKeyboardButton("← Previous", callback_data=prev_data))
        nav_buttons.append(InlineKeyboardButton(f"Page {page}/{total_pages}", callback_data="noop"))
        if page < total_pages:
            next_data = f"page|{callback_prefix}|{query}|{page + 1}" if query else f"page|{callback_prefix}|{page + 1}"
            nav_buttons.append(InlineKeyboardButton("Next →", callback_data=next_data))
        keyboard.append(nav_buttons)

    return InlineKeyboardMarkup(keyboard)


def paginate_items(items: Sequence[Any], page: int, per_page: int = 5) -> Tuple[List[Any], int]:
    """
    Paginate a list of items.

    Returns (items_for_page, total_pages). Page is 1-indexed here (compat).
    """
    total_pages = (len(items) + per_page - 1) // per_page if items else 1
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * per_page
    end_idx = start_idx + per_page
    return list(items)[start_idx:end_idx], total_pages