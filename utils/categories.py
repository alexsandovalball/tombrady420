# utils/categories.py
import re
from typing import Tuple, Optional
from config.taxonomy import CATEGORIES

BRACKET_RE = re.compile(r"^\s*\[([A-Za-z0-9 _-]+)(?:/([A-Za-z0-9 _-]+))?\]\s*(.*)$", re.IGNORECASE)
HASHTAG_RE = re.compile(r"#([A-Za-z0-9_]+)")

def _normalize_tag_to_key(tag: Optional[str]) -> Optional[str]:
    """
    Map a tag (may contain underscores or be concatenated) to a taxonomy key if possible.
    Returns a taxonomy key (exact key from CATEGORIES) or None if no match.
    """
    if not tag:
        return None
    t = tag.strip().upper()
    # direct match
    if t in CATEGORIES:
        return t
    # try underscore -> concatenated (e.g., TOP_SHELF -> TOPSHELF)
    t_no_ = t.replace("_", "").replace(" ", "")
    if t_no_ in CATEGORIES:
        return t_no_
    # try concatenated -> underscore variant (if taxonomy uses underscores)
    for k in CATEGORIES:
        if k.replace("_", "") == t_no_:
            return k
    # try replacing hyphens/spaces with underscore and check
    t_us = re.sub(r"[-\s]+", "_", t)
    if t_us in CATEGORIES:
        return t_us
    return None

def extract_category_and_subcategory(caption: Optional[str]) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Returns (clean_caption, CATEGORY, SUBCATEGORY)
    Recognizes (in priority order):
      - [CATEGORY/SUBCATEGORY] at start
      - #CATEGORY or #CATEGORY_SUBCATEGORY anywhere
    Normalizes to taxonomy keys (uppercase keys from CATEGORIES).
    clean_caption is the caption with the tag removed (or original caption if no tag found).
    """
    if not caption:
        return "", None, None
    text = caption.strip()

    # 1) bracket syntax at start: [CATEGORY/SUBCATEGORY] rest...
    m = BRACKET_RE.match(text)
    if m:
        raw_cat = m.group(1).strip()
        raw_sub = m.group(2).strip() if m.group(2) else None
        rest = m.group(3).strip() or ""
        cat_key = _normalize_tag_to_key(raw_cat)
        sub_key = _normalize_tag_to_key(raw_sub) if raw_sub else None
        # validate subcategory belongs to category
        if cat_key and sub_key and sub_key not in CATEGORIES.get(cat_key, []):
            sub_key = None
        if cat_key:
            return rest, cat_key, sub_key
        # if bracket category not recognized, fall through to hashtag parsing

    # 2) hashtags anywhere in text
    tags = HASHTAG_RE.findall(text)
    if tags:
        # take first two meaningful tags as category and subcategory
        cat_key = _normalize_tag_to_key(tags[0]) if len(tags) >= 1 else None
        sub_key = _normalize_tag_to_key(tags[1]) if len(tags) >= 2 else None
        if cat_key and sub_key and sub_key not in CATEGORIES.get(cat_key, []):
            sub_key = None
        # remove all hashtag tokens from the caption to produce clean text
        clean = HASHTAG_RE.sub("", text).strip()
        return clean, cat_key, sub_key

    # No tag found
    return text, None, None