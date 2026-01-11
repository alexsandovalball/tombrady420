# utils/categories.py
import re
import logging
from dataclasses import dataclass
from typing import Dict, Optional, Set

from config.taxonomy import CATEGORIES  # expects the structure shown in config/taxonomy.py

logger = logging.getLogger(__name__)

# Precompiled regexes
_NON_ALNUM_RE = re.compile(r"[^A-Z0-9]+")
_SPACE_HYPHEN_RE = re.compile(r"[-\s]+")

# Build canonical sets and lookup maps at import time for O(1) mapping
_CANONICAL_KEYS: Set[str] = set(k.upper() for k in CATEGORIES.keys())

# compact_map: compacted form (remove non-alnum) -> canonical key
# underscore_map: underscore-normalized form -> canonical key
compact_map: Dict[str, str] = {}
underscore_map: Dict[str, str] = {}

for key in _CANONICAL_KEYS:
    compact = _NON_ALNUM_RE.sub("", key)  # e.g., TOPSHELF_CANDY -> TOPSHELFCANDY
    compact_map[compact] = key
    underscore = key.replace("-", "_")
    underscore_map[underscore] = key

# Index subcategories for quick validation (uppercase canonical)
_subcategories_index: Dict[str, Set[str]] = {}
for cat, subs in CATEGORIES.items():
    _subcategories_index[cat.upper()] = set(s.upper() for s in (subs or []))


@dataclass(frozen=True)
class CategoryResult:
    clean_caption: str
    category: Optional[str]
    subcategory: Optional[str]


def _normalize_input(tag: str) -> str:
    """Trim and uppercase an admin-provided tag."""
    return tag.strip().upper()


def _compact_form(tag: str) -> str:
    """Return compacted form used for matching: remove non-alphanumeric characters."""
    return _NON_ALNUM_RE.sub("", tag.upper())


def _underscore_form(tag: str) -> str:
    """Return underscore-normalized form used for matching: replace spaces/hyphens with underscore."""
    return _SPACE_HYPHEN_RE.sub("_", tag.strip().upper())


def map_admin_tag_to_taxonomy(tag: Optional[str]) -> Optional[str]:
    """
    Map an admin-provided tag to a canonical taxonomy key (category or subcategory).
    Returns the canonical key (uppercase) or None if no match.
    """
    if not tag:
        return None
    t = _normalize_input(tag)

    # direct canonical match
    if t in _CANONICAL_KEYS:
        return t

    # underscore-normalized match
    t_us = _underscore_form(t)
    if t_us in underscore_map:
        return underscore_map[t_us]

    # compact match
    t_compact = _compact_form(t)
    if t_compact in compact_map:
        return compact_map[t_compact]

    # try matching against subcategories as well (some subcategories may not be top-level keys)
    # check compact against all subcategories
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


def validate_subcategory_for_category(category_key: str, subcategory_key: Optional[str]) -> Optional[str]:
    """
    Validate that subcategory_key belongs to category_key.
    Returns canonical subcategory (uppercase) if valid, otherwise None.
    """
    if not subcategory_key:
        return None
    if not category_key:
        return None
    cat_up = category_key.strip().upper()
    sub_up = subcategory_key.strip().upper()

    allowed = _subcategories_index.get(cat_up, set())
    if sub_up in allowed:
        return sub_up

    # try mapping the provided subcategory to a canonical key and re-check
    mapped = map_admin_tag_to_taxonomy(sub_up)
    if mapped and mapped in allowed:
        return mapped

    logger.debug("validate_subcategory_for_category: subcategory '%s' invalid for category '%s'", subcategory_key, category_key)
    return None


def apply_admin_categorization(caption: Optional[str], admin_category_input: Optional[str], admin_subcategory_input: Optional[str] = None) -> CategoryResult:
    """
    Apply admin-provided category and optional subcategory to a product.

    - Does NOT parse the caption for tags.
    - Maps admin inputs to canonical taxonomy keys.
    - Validates subcategory membership and drops invalid subcategories.
    - Returns CategoryResult with trimmed caption and canonical keys or None.
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