# utils/taxonomy_utils.py
from typing import Optional, Tuple
from config.taxonomy import CATEGORIES

def normalize_tag(value: str) -> str:
    """
    Normalize a raw tag value to the canonical stored form:
    - strip whitespace
    - uppercase
    - replace internal spaces with underscores
    """
    return value.strip().upper().replace(" ", "_")

def validate_category_and_subcategory(cat: Optional[str], sub: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """
    Validate category and optional subcategory against CATEGORIES.

    Returns:
      (category, subcategory) where values are normalized and guaranteed to be present
      in CATEGORIES. If category is invalid, returns (None, None).
      If category is valid but subcategory is invalid or missing, returns (category, None).
    """
    if not cat:
        return None, None

    cat_n = normalize_tag(cat)
    if cat_n not in CATEGORIES:
        return None, None

    if sub:
        sub_n = normalize_tag(sub)
        if sub_n in CATEGORIES.get(cat_n, []):
            return cat_n, sub_n
        return cat_n, None

    return cat_n, None