# utils/pagination.py
"""
Deprecated thin wrapper for pagination helpers.

This module remains for compatibility: it re-exports the merged helpers from
utils.helpers. Prefer importing from utils.helpers in new code.
"""

import warnings
from typing import Any, List, Tuple, Sequence, Optional

from .helpers import create_pagination_keyboard, paginate_items  # re-export

warnings.warn("utils.pagination is deprecated; import helpers.create_pagination_keyboard and helpers.paginate_items instead", DeprecationWarning, stacklevel=2)

__all__ = ["create_pagination_keyboard", "paginate_items"]