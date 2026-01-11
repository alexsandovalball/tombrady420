# utils/helpers.py
"""
Helper utilities: logging setup, pagination keyboard builder, small helpers.

Improvements:
- Stronger typing and docstrings.
- Safer pagination callback construction with optional scope truncation to avoid
  exceeding Telegram callback_data limits in extreme cases.
- Small utility `chunked` typed and documented.
"""

from __future__ import annotations

import logging
from typing import List, Iterator, Sequence
from telegram import InlineKeyboardButton

logger = logging.getLogger(__name__)


def setup_logging(level: str = "INFO") -> None:
    """
    Configure root logging once. Call early in application startup.

    `level` may be a string like "DEBUG", "INFO", etc.
    """
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=lvl, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    # Also set the root logger level explicitly to ensure consistency
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
    Telegram limits callback_data to 64 bytes; we keep scope reasonably short.
    This function truncates long scopes and replaces whitespace with underscores.

    It is conservative: it does not attempt to encode or compress the scope.
    If your scope can be arbitrarily long, consider using a short id instead.
    """
    if not scope:
        return ""
    s = scope.replace(" ", "_")
    if len(s) <= max_len:
        return s
    # Truncate preserving start and end for readability
    head = s[: max_len // 2 - 2]
    tail = s[-(max_len // 2 - 2) :]
    return f"{head}..{tail}"


def build_pagination_keyboard(scope: str, page: int, per_page: int, total: int) -> List[List[InlineKeyboardButton]]:
    """
    Build a simple pagination keyboard.

    Args:
      scope: string identifying the scope (category or "category::subcategory").
             This will be embedded in callback_data; keep it reasonably short.
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

    # Normalize page bounds
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
    # Current page indicator (non-actionable but kept as callback to preserve state)
    row.append(InlineKeyboardButton(text=f"Page {page+1}/{total_pages}", callback_data=f"CAT:PAGE:{safe_scope}::{page}"))
    if page + 1 < total_pages:
        row.append(InlineKeyboardButton(text="Next ➡️", callback_data=f"CAT:PAGE:{safe_scope}::{page+1}"))
    keyboard.append(row)

    # Always include a Back button on its own row for consistent UX
    keyboard.append([InlineKeyboardButton(text="Back", callback_data="CAT:BACK")])
    return keyboard