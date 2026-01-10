# config/taxonomy.py
# Hardwired taxonomy for the catalog bot.
# Keys are top-level categories (uppercase). Subcategories are uppercase with underscores for multiword names.

CATEGORIES = {
    "CARTRIDGES": ["AUTHENTICS", "REPLICAS"],
    "EDIBLES": ["FLOWER_EDIBLES", "SHROOM_EDIBLES"],
    "CONCENTRATES": [
        "SNOWCAPS", "MOONROCKS", "HASH_AND_KIEF", "BADDER", "SHATTER",
        "DISTILLATE", "THCAPOWDER", "RSO", "ROSIN", "SUGAR", "OTHERS"
    ],
    "PREROLLS": ["FLOWER_PREROLLS", "INFUSED_FLOWER_PREROLLS"],
    "SHROOMS": [],
    "FLOWER": [
        "TOPSHELFCANDY", "PREMIUMEXOTICS", "EXOTICS",
        "PREMIUMLIGHTDEPS", "LIGHTDEPS", "LIGHTASSIST", "LOWS"
    ],
    "DATEDPROOFS": [],
    "CLIENTTOUCHDOWNS": [],
}

# Human-friendly display names for categories and subcategories
# Keys must match the keys used in CATEGORIES exactly.
# Use these values when rendering labels in the UI (uppercase, spaces preserved).
DISPLAY_NAMES = {
    "CARTRIDGES": "CARTRIDGES",
    "AUTHENTICS": "AUTHENTICS",
    "REPLICAS": "REPLICAS",

    "CLIENTTOUCHDOWNS": "CLIENT TOUCHDOWNS",

    "CONCENTRATES": "CONCENTRATES",
    "SNOWCAPS": "SNOWCAPS",
    "MOONROCKS": "MOONROCKS",
    "HASH_AND_KIEF": "HASH AND KIEF",
    "BADDER": "BADDER",
    "SHATTER": "SHATTER",
    "DISTILLATE": "DISTILLATE",
    "THCAPOWDER": "THCA POWDER",
    "RSO": "RSO",
    "ROSIN": "ROSIN",
    "SUGAR": "SUGAR",
    "OTHERS": "OTHERS",

    "DATEDPROOFS": "DATED PROOFS",

    "EDIBLES": "EDIBLES",
    "FLOWER_EDIBLES": "FLOWER EDIBLES",
    "SHROOM_EDIBLES": "SHROOM EDIBLES",

    "FLOWER": "FLOWER",
    "TOPSHELFCANDY": "TOPSHELF CANDY",
    "PREMIUMEXOTICS": "PREMIUM EXOTICS",
    "EXOTICS": "EXOTICS",
    "PREMIUMLIGHTDEPS": "PREMIUM LIGHT DEPS",
    "LIGHTDEPS": "LIGHT DEPS",
    "LIGHTASSIST": "LIGHT ASSIST",
    "LOWS": "LOWS",

    "PREROLLS": "PREROLLS",
    "FLOWER_PREROLLS": "FLOWER PREROLLS",
    "INFUSED_FLOWER_PREROLLS": "INFUSED FLOWER PREROLLS",

    "SHROOMS": "SHROOMS",
}