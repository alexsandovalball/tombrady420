 url=https://github.com/alexsandovalball/tombrady420/blob/main/utils/taxonomy_utils.py
# utils/taxonomy_utils.py
"""
Taxonomy normalization and validation helpers.

This consolidates category/subcategory mapping and admin tag mapping into a
single authoritative module so handlers import the same utilities.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass
from typing import Optional, Tuple, List, Dict, Set

from config.taxonomy import CATEGORIES, DISPLAY_NAMES  # expected dicts in config

logger = logging.getLogger(__name__)

# Normalization helpers
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]+")
_SPACE_HYPHEN_RE = re.compile(r"[-\s]+")


def canonicalize_key(value: Optional[str]) -> Optional[str]:
    """
    Convert arbitrary input into a canonical taxonomy key that matches keys in CATEGORIES.

    Rules:
    - None/empty -> None
    - strip, uppercase
    - replace runs of whitespace/hyphen with underscore
    - collapse non-alnum sequences to underscore and trim underscores
    """
    if not value:
        return None
    v = str(value).strip().upper()
    # Replace spaces/hyphens with underscore
    v = _SPACE_HYPHEN_RE.sub("_", v)
    # Replace other non-alnum with nothing (compact)
    v = _NON_ALNUM_RE.sub("_", v)
    # collapse multiple underscores
    v = re.sub(r"_+", "_", v)
    v = v.strip("_")
    return v if v else None


def get_display_name(key: Optional[str]) -> str:
    """Return a human-friendly display name for a canonical taxonomy key."""
    if not key:
        return ""
    k = canonicalize_key(key)
    if not k:
        return ""
    try:
        return DISPLAY_NAMES.get(k, k.replace("_", " ").title())
    except Exception:
        return k.replace("_", " ").title()


def list_subcategories(cat: Optional[str]) -> List[str]:
    """Return canonical subcategory keys for a given category (empty list if none)."""
    if not cat:
        return []
    cat_n = canonicalize_key(cat)
    return list(CATEGORIES.get(cat_n, []))


# Admin mapping utilities (previously in utils/categories.py)
_CANONICAL_KEYS: Set[str] = set(k.upper() for k in CATEGORIES.keys())

# build compact/underscore maps for fast admin mapping
_compact_map: Dict[str, str] = {}
_underscore_map: Dict[str, str] = {}
_subcategories_index: Dict[str, Set[str]] = {}

for key in _CANONICAL_KEYS:
    compact = _NON_ALNUM_RE.sub("", key)  # remove non-alphanumeric for compact form
    _compact_map[compact] = key
    underscore = key.replace("-", "_")
    _underscore_map[underscore] = key

for cat, subs in CATEGORIES.items():
    _subcategories_index[cat.upper()] = set(s.upper() for s in (subs or []))


@dataclass(frozen=True)
class CategoryResult:
    clean_caption: str
    category: Optional[str]
    subcategory: Optional[str]


def map_admin_tag_to_taxonomy(tag: Optional[str]) -> Optional[str]:
    """
    Map an admin-provided tag to a canonical taxonomy key (category or subcategory).
    Returns the canonical key (uppercase-like canonical form) or None if no match.
    """
    if not tag:
        return None
    t = str(tag).strip().upper()
    if t in _CANONICAL_KEYS:
        return t

    t_us = _SPACE_HYPHEN_RE.sub("_", t)
    t_compact = _NON_ALNUM_RE.sub("", t)

    if t_us in _underscore_map:
        return _underscore_map[t_us]
    if t_compact in _compact_map:
        return _compact_map[t_compact]

    # check against subcategories
    for cat, subs in _subcategories_index.items():
        for sub in subs:
            if sub == t:
                return sub
            if _NON_ALNUM_RE.sub("", sub) == t_compact:
                return sub
            if _SPACE_HYPHEN_RE.sub("_", sub) == t_us:
                return sub

    logger.debug("map_admin_tag_to_taxonomy: no mapping for tag '%s'", tag)
    return None


def validate_subcategory_for_category(category_key: Optional[str], subcategory_key: Optional[str]) -> Optional[str]:
    """
    Validate that subcategory_key belongs to category_key.
    Returns canonical subcategory (string) if valid, otherwise None.
    """
    if not category_key or not subcategory_key:
        return None
    cat_up = canonicalize_key(category_key)
    sub_up = canonicalize_key(subcategory_key)
    if not cat_up or not sub_up:
        return None
    allowed = _subcategories_index.get(cat_up.upper(), set())
    if sub_up.upper() in allowed:
        return sub_up
    mapped = map_admin_tag_to_taxonomy(sub_up)
    if mapped and mapped in allowed:
        return mapped
    logger.debug("validate_subcategory_for_category: subcategory '%s' invalid for category '%s'", subcategory_key, category_key)
    return None


def apply_admin_categorization(caption: Optional[str], admin_category_input: Optional[str], admin_subcategory_input: Optional[str] = None) -> CategoryResult:
    """
    Apply admin-provided category and optional subcategory to a product.

    Returns CategoryResult with trimmed caption and canonical keys or None.
    """
    clean = (caption or "").strip()
    cat_key = map_admin_tag_to_taxonomy(admin_category_input) if admin_category_input else None
    sub_key = map_admin_tag_to_taxonomy(admin_subcategory_input) if admin_subcategory_input else None

    if cat_key and sub_key:
        valid_sub = validate_subcategory_for_category(cat_key, sub_key)
        if not valid_sub:
            logger.warning("Admin subcategory '%s' is not valid for category '%s'; ignoring subcategory", sub_key, cat_key)
            sub_key = None
        else:
            sub_key = valid_sub

    return CategoryResult(clean_caption=clean, category=cat_key, subcategory=sub_key)