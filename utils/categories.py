"""
Category extraction and management utilities.
"""
import re
from typing import Optional, List
from translations.translator import get_translated_string

# Define category structure
CATEGORIES = {
    "CARTRIDGES": ["AUTHENTICS", "REPLICAS"],
    "EDIBLES": ["FLOWER EDIBLES", "SHROOM EDIBLES"],
    "CONCENTRATES": [
        "SOLVENTLESS",
        "LIVE RESIN AND EXTRACTS",
        "OILS AND ISOLATES",
        "HASH AND KIEF",
        "OTHERS"
    ],
    "PREROLLS": ["FLOWER PREROLLS", "INFUSED FLOWER PREROLLS"],
    "SHROOMS": [],
    "FLOWER": [
        "TOPSHELFCANDY", "PREMIUMEXOTICS", "EXOTICS", 
        "PREMIUMLIGHTDEPS", "LIGHTDEPS", "LIGHTASSIST", "LOWS",
        "MOONROCKS AND SNOWCAPS"
    ],
    "DATEDPROOFS": [],
    "CLIENTTOUCHDOWNS": [],
    "ANNOUNCEMENTS": []
}

# Category display names with emojis
CATEGORY_DISPLAY = {
    "CARTRIDGES": "🛒 Cartridges",
    "EDIBLES": "🍫 Edibles",
    "CONCENTRATES": "💎 Concentrates",
    "PREROLLS": "🚬 Pre-Rolls",
    "SHROOMS": "🍄 Shrooms",
    "FLOWER": "🌸 Flower",
    "DATEDPROOFS": "📅 Dated Proofs",
    "CLIENTTOUCHDOWNS": "✈️ Client Touchdowns",
    "ANNOUNCEMENTS": "📢 Announcements"
}

# Categories to exclude from "All Products" view
# Add category names here to hide them from the "All Products" button/view
# while keeping them accessible when browsing categories directly
EXCLUDED_FROM_ALL_PRODUCTS = ["DATEDPROOFS", "CLIENTTOUCHDOWNS", "ANNOUNCEMENTS"]

# Categories to exclude from triggering notifications
# All categories now trigger notifications
NOTIFICATION_EXCLUDED_CATEGORIES = []


def get_category_display_name(category: str) -> str:
    """Get display name for a category."""
    return CATEGORY_DISPLAY.get(category, category)


def get_all_categories() -> List[str]:
    """Get list of all category names."""
    return list(CATEGORIES.keys())


def get_subcategories(category: str) -> List[str]:
    """Get subcategories for a given category."""
    return CATEGORIES.get(category, [])


def get_subcategory_display_name(subcategory: str, user_lang: str = "en") -> str:
    """
    Get translated display name for a subcategory.
    
    Args:
        subcategory: The subcategory name (e.g., "AUTHENTICS", "DIAMONDS AND SAUCE")
        user_lang: User's language preference
    
    Returns:
        Translated subcategory display name
    
    Note:
        Normalizes subcategory by converting any sequence of non-alphanumeric 
        characters to a single underscore and trimming leading/trailing underscores.
        This handles special characters robustly.
        Examples: 
        - "FLOWER EDIBLES" -> "flower_edibles"
        - "DIAMONDS AND SAUCE" -> "diamonds_and_sauce"
        - "LIVE-RESIN" -> "live_resin"
    """
    # Normalize: convert non-alphanumeric sequences to single underscore, trim edges
    normalized = re.sub(r'[^0-9a-zA-Z]+', '_', subcategory.lower()).strip('_')
    key = f"subcategory_{normalized}"
    
    # Get translated string
    translated = get_translated_string(key, user_lang)
    
    # If translation not found, return title-cased version
    if translated == key:
        return subcategory.title()
    
    return translated


def format_category_info(category: Optional[str], subcategory: Optional[str], user_lang: str = "en") -> str:
    """
    Format category and subcategory for display with translations.
    
    Args:
        category: Category name
        subcategory: Subcategory name
        user_lang: User's language preference
    
    Returns:
        Formatted category info string
    """
    if not category:
        # Get translated "Uncategorized" string
        uncategorized = get_translated_string("uncategorized", user_lang)
        return uncategorized if uncategorized != "uncategorized" else "Uncategorized"
    
    display = get_category_display_name(category)
    if subcategory:
        translated_subcat = get_subcategory_display_name(subcategory, user_lang)
        display += f" • {translated_subcat}"
    
    return display
