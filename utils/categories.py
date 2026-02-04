"""
Category extraction and management utilities.
"""
import re
from typing import Tuple, Optional, List
from translations.translator import get_translated_string

# Define category structure
CATEGORIES = {
    "CARTRIDGES": ["AUTHENTICS", "REPLICAS"],
    "EDIBLES": ["FLOWER EDIBLES", "SHROOM EDIBLES"],
    "CONCENTRATES": [
        "SNOWCAPS", "MOONROCKS", "HASH AND KIEF", "BADDER", 
        "SHATTER", "DISTILLATE", "THCAPOWDER", "RSO", "ROSIN", "SUGAR", "OTHERS"
    ],
    "PREROLLS": ["FLOWER PREROLLS", "INFUSED FLOWER PREROLLS"],
    "SHROOMS": [],
    "FLOWER": [
        "TOPSHELFCANDY", "PREMIUMEXOTICS", "EXOTICS", 
        "PREMIUMLIGHTDEPS", "LIGHTDEPS", "LIGHTASSIST", "LOWS"
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
# Note: ANNOUNCEMENTS is NOT in this list - it will trigger notifications
#       even though it's excluded from "All Products" view
NOTIFICATION_EXCLUDED_CATEGORIES = ["DATEDPROOFS", "CLIENTTOUCHDOWNS"]



def extract_category_from_caption(caption: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract category and subcategory from caption based on hashtags.
    
    Returns:
        Tuple of (category, subcategory) or (None, None) if no match
    """
    if not caption:
        return None, None
    
    # Normalize caption to uppercase for matching
    caption_upper = caption.upper()
    
    # Extract all hashtags - improved pattern to handle spaces within hashtags
    # Matches #WORD, #WORD WORD, etc.
    hashtags = re.findall(r'#([A-Z0-9]+(?:\s+[A-Z0-9]+)*)', caption_upper)
    
    if not hashtags:
        return None, None
    
    # First, check for main categories (with ## prefix)
    for tag in hashtags:
        # Remove leading # if present (already extracted by regex)
        clean_tag = tag.strip()
        
        # Check if it's a main category
        for category, subcategories in CATEGORIES.items():
            if clean_tag == category or clean_tag.replace(" ", "") == category:
                # Found main category, now look for subcategory
                subcategory = None
                
                # If category has subcategories, look for them
                if subcategories:
                    for tag2 in hashtags:
                        clean_tag2 = tag2.strip()
                        for subcat in subcategories:
                            if clean_tag2 == subcat or clean_tag2.replace(" ", "") == subcat.replace(" ", ""):
                                subcategory = subcat
                                break
                        if subcategory:
                            break
                
                return category, subcategory
    
    # If no ## category found, check if any hashtag matches a subcategory
    # This handles cases where only subcategory is mentioned
    for tag in hashtags:
        clean_tag = tag.strip()
        for category, subcategories in CATEGORIES.items():
            if subcategories:
                for subcat in subcategories:
                    if clean_tag == subcat or clean_tag.replace(" ", "") == subcat.replace(" ", ""):
                        return category, subcat
    
    return None, None


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
        subcategory: The subcategory name (e.g., "AUTHENTICS", "HASH AND KIEF")
        user_lang: User's language preference
    
    Returns:
        Translated subcategory display name
    
    Note:
        Converts subcategory to key format by lowercasing and replacing all
        whitespace (including spaces in conjunctions like 'AND') with underscores.
        Examples: "FLOWER EDIBLES" -> "flower_edibles", "HASH AND KIEF" -> "hash_and_kief"
    """
    # Convert subcategory to key format (lowercase with underscores)
    # Handle all whitespace variations consistently
    normalized_subcategory = re.sub(r'\s+', '_', subcategory.lower())
    key = f"subcategory_{normalized_subcategory}"
    
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
