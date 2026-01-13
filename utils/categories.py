"""
Category extraction and management utilities.
"""
import re
from typing import Tuple, Optional, List

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
    "CLIENTTOUCHDOWNS": []
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
    "CLIENTTOUCHDOWNS": "✈️ Client Touchdowns"
}


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
    
    # Extract all hashtags
    hashtags = re.findall(r'#(\w+(?:\s+\w+)*)', caption_upper)
    
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


def format_category_info(category: Optional[str], subcategory: Optional[str]) -> str:
    """Format category and subcategory for display."""
    if not category:
        return "Uncategorized"
    
    display = get_category_display_name(category)
    if subcategory:
        display += f" • {subcategory.title()}"
    
    return display
