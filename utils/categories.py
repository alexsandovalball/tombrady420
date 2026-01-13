# utils/categories.py
"""
Deprecated wrapper: categories utilities are consolidated into utils.taxonomy_utils.

This file exists for backward compatibility. Please import from
utils.taxonomy_utils instead.
"""

import warnings
from .taxonomy_utils import map_admin_tag_to_taxonomy, validate_subcategory_for_category, apply_admin_categorization, CategoryResult

warnings.warn("utils.categories is deprecated; import from utils.taxonomy_utils instead", DeprecationWarning, stacklevel=2)

__all__ = ["map_admin_tag_to_taxonomy", "validate_subcategory_for_category", "apply_admin_categorization", "CategoryResult"]