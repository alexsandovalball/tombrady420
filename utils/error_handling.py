# utils/error_handling.py
"""
Small helpers for consistent exception logging and formatting.
"""

from __future__ import annotations

import logging
import traceback
from typing import Optional

logger = logging.getLogger(__name__)


def format_exception(exc: Optional[BaseException] = None) -> str:
    """
    Return a compact, human-friendly traceback string for logging.
    If exc is None, formats the current exception.
    """
    if exc is None:
        return "".join(traceback.format_exc())
    tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    return tb


def log_exception(msg: str = "Unhandled exception", exc: Optional[BaseException] = None) -> None:
    """
    Log an exception with a standard prefix and full traceback at ERROR level.
    """
    tb = format_exception(exc)
    logger.error("%s: %s", msg, tb)