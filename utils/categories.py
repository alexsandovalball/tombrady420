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
        "LIVE RESIN", "ROSIN", "DIAMONDS AND SAUCE", "SHATTER", 
        "BADDER", "CRUMBLE", "SUGAR", "DISTILLATE", "HASH", 
        "KIEF", "THCAPOWDER", "RSO", "OTHERS"
    ],
    "PREROLLS": ["FLOWER PREROLLS", "INFUSED FLOWER PREROLLS"],
    "SHROOMS": [],
    "FLOWER": [
        "TOPSHELFCANDY", "PREMIUMEXOTICS", "EXOTICS", 
        "PREMIUMLIGHTDEPS", "LIGHTDEPS", "LIGHTASSIST", "LOWS",
        "INFUSED FLOWER"
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


# Keyword synonyms for better auto-detection
# Maps common variations to canonical subcategory names
SUBCATEGORY_SYNONYMS = {
    "LIVE RESIN": ["LIVERESIN", "LIVE-RESIN", "LIVE RESINS"],
    "ROSIN": ["ROSINS"],
    "DIAMONDS AND SAUCE": ["DIAMONDS&SAUCE", "DIAMONDSANDSAUCE", "DIAMONDS-AND-SAUCE", "DIAMONDSAUCE"],
    "INFUSED FLOWER": ["INFUSEDFLOWER", "INFUSED-FLOWER", "MOONROCKS", "SNOWCAPS"],
    "HASH": ["HASHES"],
    "KIEF": ["KEIF"],  # Common misspelling
}


def extract_category_from_caption(caption: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract category and subcategory from caption based on hashtags.
    
    Returns:
        Tuple of (category, subcategory) or (None, None) if no match
    """
    if not caption:
        return None, None
    
    # Extract hashtags by splitting on # and taking uppercase words
    # This correctly handles multi-word hashtags like #LIVE RESIN or #DIAMONDS AND SAUCE
    parts = [p.strip() for p in caption.split('#') if p.strip()]
    
    hashtags = []
    for part in parts:
        # Take only the uppercase words at the start of each hashtag
        words = part.split()
        tag_words = []
        for word in words:
            # Include uppercase words (including those with digits like "420" if uppercase)
            # Reject standalone digits or mixed-case words
            if word.isupper():
                tag_words.append(word)
            else:
                break  # Stop at first non-uppercase word (start of regular text)
        if tag_words:
            hashtags.append(' '.join(tag_words))
    
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
                            # Direct match
                            if clean_tag2 == subcat or clean_tag2.replace(" ", "") == subcat.replace(" ", ""):
                                subcategory = subcat
                                break
                            # Check synonyms
                            if subcat in SUBCATEGORY_SYNONYMS:
                                for synonym in SUBCATEGORY_SYNONYMS[subcat]:
                                    if clean_tag2 == synonym or clean_tag2.replace(" ", "") == synonym.replace(" ", ""):
                                        subcategory = subcat
                                        break
                            if subcategory:
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
                    # Direct match
                    if clean_tag == subcat or clean_tag.replace(" ", "") == subcat.replace(" ", ""):
                        return category, subcat
                    # Check synonyms
                    if subcat in SUBCATEGORY_SYNONYMS:
                        for synonym in SUBCATEGORY_SYNONYMS[subcat]:
                            if clean_tag == synonym or clean_tag.replace(" ", "") == synonym.replace(" ", ""):
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
