# utils/categories.py
import re
from typing import Tuple, Optional
from config.taxonomy import CATEGORIES

BRACKET_RE = re.compile(r"^\s*\[([A-Za-z0-9 _-]+)(?:/([A-Za-z0-9 _-]+))?\]\s*(.*)$", re.IGNORECASE)
HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]+)")

def _map_to_taxonomy_key(tag: Optional[str]) -> Optional[str]:
    """
    Map a tag variant to a canonical taxonomy key from CATEGORIES.
    Accepts variants like:
      - TOPSHELF_CANDY
      - TOPSHELFCANDY
      - Top Shelf Candy
      - top-shelf-candy
    Returns the exact key from CATEGORIES or None if no match.
    """
    if not tag:
        return None
    t = tag.strip().upper()
    # direct match
    if t in CATEGORIES:
        return t
    # remove underscores/spaces/hyphens to try concatenated form
    t_compact = re.sub(r"[_\s-]+", "", t)
    if t_compact in CATEGORIES:
        return t_compact
    # try matching by removing underscores from taxonomy keys
    for k in CATEGORIES:
        if k.replace("_", "") == t_compact:
            return k
    # try underscore variant (defensive)
    t_us = re.sub(r"[-\s]+", "_", t)
    if t_us in CATEGORIES:
        return t_us
    return None

def extract_category_and_subcategory(caption: Optional[str]) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Parse caption and return (clean_caption, CATEGORY, SUBCATEGORY).

    Priority:
      1. Bracket syntax at start: [CATEGORY/SUBCATEGORY] caption...
      2. Hashtags anywhere: #CATEGORY #SUBCATEGORY caption...

    CATEGORY and SUBCATEGORY are canonical keys from config.taxonomy.CATEGORIES
    (uppercase, exact key names). If no tag is found, returns (original_caption, None, None).
    """
    if not caption:
        return "", None, None
    text = caption.strip()

    # 1) bracket syntax at start
    m = BRACKET_RE.match(text)
    if m:
        raw_cat = m.group(1).strip()
        raw_sub = m.group(2).strip() if m.group(2) else None
        rest = (m.group(3) or "").strip()
        cat_key = _map_to_taxonomy_key(raw_cat)
        sub_key = _map_to_taxonomy_key(raw_sub) if raw_sub else None
        # validate subcategory belongs to category
        if cat_key and sub_key and sub_key not in CATEGORIES.get(cat_key, []):
            sub_key = None
        if cat_key:
            return rest, cat_key, sub_key
        # fall through to hashtags if bracket category not recognized

    # 2) hashtags anywhere
    tags = HASHTAG_RE.findall(text)
    if tags:
        cat_key = _map_to_taxonomy_key(tags[0]) if len(tags) >= 1 else None
        sub_key = _map_to_taxonomy_key(tags[1]) if len(tags) >= 2 else None
        if cat_key and sub_key and sub_key not in CATEGORIES.get(cat_key, []):
            sub_key = None
        # remove all hashtag tokens from the caption to produce clean text
        clean = HASHTAG_RE.sub("", text).strip()
        return clean, cat_key, sub_key

    # No tag found
    return text, None, None