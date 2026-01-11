# utils/helpers.py
"""
Helper utilities: logging setup, pagination keyboard builder, small helpers.
"""

from __future__ import annotations

import logging
from typing import List, Tuple
from telegram import InlineKeyboardButton

logger = logging.getLogger(__name__)


def setup_logging(level: str = "INFO") -> None:
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=lvl, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def chunked(seq, n):
    """Yield successive n-sized chunks from seq."""
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def build_pagination_keyboard(scope: str, page: int, per_page: int, total: int) -> List[List[InlineKeyboardButton]]:
    """
    Build a simple pagination keyboard.
    scope: string identifying the scope (category or category::subcategory)
    """
    keyboard = []
    total_pages = (total + per_page - 1) // per_page
    row = []
    if page > 0:
        row.append(InlineKeyboardButton(text="⬅️ Prev", callback_data=f"CAT:PAGE:{scope}:{page-1}"))
    row.append(InlineKeyboardButton(text=f"Page {page+1}/{max(1,total_pages)}", callback_data=f"CAT:PAGE:{scope}:{page}"))
    if page + 1 < total_pages:
        row.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"CAT:PAGE:{scope}:{page+1}"))
    keyboard.append(row)
    keyboard.append([InlineKeyboardButton(text="Back", callback_data="CAT:BACK")])
    return keyboard