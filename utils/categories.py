# utils/categories.py
import re
from typing import Tuple, Optional

def extract_category_and_subcategory(caption: Optional[str]) -> Tuple[str, Optional[str], Optional[str]]:
    """
    Returns (clean_caption, CATEGORY, SUBCATEGORY)
    Recognizes:
      - [CATEGORY/SUBCATEGORY] at start
      - #CATEGORY or #CATEGORY_SUBCATEGORY anywhere
    Normalizes to uppercase; spaces -> underscores for bracket tags.
    """
    if not caption:
        return "", None, None
    text = caption.strip()

    # [CATEGORY/SUBCATEGORY] at start
    m = re.match(r"^\s*\[([A-Za-z0-9 _-]+)(?:/([A-Za-z0-9 _-]+))?\]\s*(.*)$", text)
    if m:
        cat = m.group(1).strip().upper().replace(" ", "_")
        sub = m.group(2).strip().upper().replace(" ", "_") if m.group(2) else None
        rest = m.group(3).strip()
        return rest, cat, sub

    # #CATEGORY or #CATEGORY_SUB anywhere
    m2 = re.search(r"#([A-Za-z0-9_]+)(?:_([A-Za-z0-9_]+))?", text)
    if m2:
        cat = m2.group(1).strip().upper()
        sub = m2.group(2).strip().upper() if m2.group(2) else None
        clean = re.sub(r"#([A-Za-z0-9_]+)(?:_([A-Za-z0-9_]+))?", "", text).strip()
        return clean, cat, sub

    return text, None, None