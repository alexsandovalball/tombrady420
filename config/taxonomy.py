# config/taxonomy.py
# Canonical taxonomy used by the bot. Keys are canonical category names (uppercase).
# Subcategories are lists of canonical names (uppercase). Keep keys stable.

CATEGORIES = {
    "CARTRIDGES": ["AUTHENTICS", "REPLICAS"],
    "EDIBLES": ["FLOWER_EDIBLES", "SHROOM_EDIBLES"],
    "CONCENTRATES": [
        "SNOWCAPS",
        "MOONROCKS",
        "HASH_AND_KIEF",
        "BADDER",
        "SHATTER",
        "DISTILLATE",
        "THCA_POWDER",
        "RSO",
        "ROSIN",
        "SUGAR",
        "OTHERS",
    ],
    "PREROLLS": ["FLOWER_PREROLLS", "INFUSED_FLOWER_PREROLLS"],
    "SHROOMS": [],
    "FLOWER": [
        "TOPSHELF_CANDY",
        "PREMIUM_EXOTICS",
        "EXOTICS",
        "PREMIUM_LIGHT_DEPS",
        "LIGHT_DEPS",
        "LIGHT_ASSIST",
        "LOWS",
    ],
    "DATEDPROOFS": [],
    "CLIENTTOUCHDOWNS": [],
}