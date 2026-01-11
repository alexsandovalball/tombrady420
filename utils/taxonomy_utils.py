# utils/taxonomy_utils.py
"""
Taxonomy normalization and validation helpers.

This module provides small, testable utilities to canonicalize taxonomy keys,
validate category/subcategory pairs against the canonical CATEGORIES mapping,
and render human-friendly display names.
"""

from typing import Optional, Tuple, List

from config.taxonomy import CATEGORIES, DISPLAY_NAMES, canonicalize_key


def normalize_tag(value: str) -> str:
    """
    Normalize a raw tag value to the canonical stored form.

    Normalization rules:
    - strip surrounding whitespace
    - uppercase
    - replace internal spaces with underscores
    - collapse repeated underscores (via canonicalize_key)
    """
    if not value:
        return ""
    return canonicalize_key(value)


def canonicalize_key_safe(value: Optional[str]) -> Optional[str]:
    """Return canonical key or None if input is falsy."""
    if not value:
        return None
    k = canonicalize_key(value)
    return k if k else None


def is_valid_category(cat: Optional[str]) -> bool:
    """Return True if the provided category (raw or canonical) exists in CATEGORIES."""
    if not cat:
        return False
    cat_canon = canonicalize_key(cat)
    return cat_canon in CATEGORIES


def is_valid_subcategory(cat: Optional[str], sub: Optional[str]) -> bool:
    """Return True if sub is a valid subcategory for the given category."""
    if not cat or not sub:
        return False
    cat_canon = canonicalize_key(cat)
    sub_canon = canonicalize_key(sub)
    return sub_canon in (CATEGORIES.get(cat_canon) or [])


def validate_category_and_subcategory(cat: Optional[str], sub: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Validate category and optional subcategory against CATEGORIES.

    Returns:
      (category, subcategory) where values are canonicalized and guaranteed to be present
      in CATEGORIES. If category is invalid, returns (None, None).
      If category is valid but subcategory is invalid or missing, returns (category, None).
    """
    if not cat:
        return None, None

    cat_n = canonicalize_key(cat)
    if cat_n not in CATEGORIES:
        return None, None

    if sub:
        sub_n = canonicalize_key(sub)
        if sub_n in CATEGORIES.get(cat_n, []):
            return cat_n, sub_n
        return cat_n, None

    return cat_n, None


def get_display_name(key: Optional[str]) -> str:
    """
    Return a human-friendly display name for a canonical taxonomy key.
    Falls back to a readable title-cased form if DISPLAY_NAMES lacks the key.
    """
    if not key:
        return ""
    k = canonicalize_key(key)
    try:
        return DISPLAY_NAMES.get(k, k.replace("_", " ").title())
    except Exception:
        return k.replace("_", " ").title()


def list_subcategories(cat: Optional[str]) -> List[str]:
    """Return the list of canonical subcategory keys for a given category (empty list if none)."""
    if not cat:
        return []
    cat_n = canonicalize_key(cat)
    return list(CATEGORIES.get(cat_n, []))