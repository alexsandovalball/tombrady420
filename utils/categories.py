import re

# Master category map with subcategories
CATEGORY_MAP = {
    "CARTRIDGES": ["AUTHENTICS", "REPLICAS"],
    "EDIBLES": ["FLOWEREDIBLES", "SHROOMEDIBLES"],
    "CONCENTRATES": [
        "SNOWCAPS", "MOONROCKS", "HASHANDKIEF", "BADDER", "SHATTER",
        "DISTILLATE", "THCAPOWDER", "RSO", "ROSIN", "SUGAR", "OTHERS"
    ],
    "PREROLLS": ["FLOWERPREROLLS", "INFUSEDFLOWERPREROLLS"],
    "SHROOMS": [],
    "FLOWER": [
        "TOPSHELFCANDY", "PREMIUMEXOTICS", "EXOTICS",
        "PREMIUMLIGHTDEPS", "LIGHTDEPS", "LIGHTASSIST", "LOWS"
    ],
    "DATEDPROOFS": [],          # NEW CATEGORY
    "CLIENTTOUCHDOWNS": []      # NEW CATEGORY
}


def extract_category_and_subcategory(caption: str):
    """
    Parse hashtags from caption and return (clean_caption, category, subcategory).
    - Detects hashtags like #FLOWER #TOPSHELFCANDY
    - Matches them against CATEGORY_MAP
    - Removes hashtags from caption for display
    """
    if not caption:
        return caption, None, None

    hashtags = re.findall(r"#(\w+)", caption)
    category, subcategory = None, None

    for tag in hashtags:
        tag_upper = tag.upper()

        # Check if it's a top-level category
        if tag_upper in CATEGORY_MAP:
            category = tag_upper
        else:
            # Check if it's a subcategory under any category
            for cat, subs in CATEGORY_MAP.items():
                normalized_subs = [s.replace(" ", "").upper() for s in subs]
                if tag_upper in normalized_subs:
                    category = cat
                    subcategory = tag_upper
                    break

    # Remove hashtags from caption for display
    clean_caption = re.sub(r"#\w+", "", caption).strip()

    return clean_caption, category, subcategory