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