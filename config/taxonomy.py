# config/taxonomy.py
# Canonical taxonomy used by the bot. Keys are canonical category names (uppercase).
# Subcategories are lists of canonical names (uppercase). Keep keys stable.
#
# This file also exposes DISPLAY_NAMES: a mapping from canonical keys to
# human-friendly labels used in UIs. Handlers should use DISPLAY_NAMES.get(key, fallback).

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

# Human-friendly display names for categories and subcategories.
# Handlers should call DISPLAY_NAMES.get(key, fallback) to render labels.
DISPLAY_NAMES = {
    # Top-level categories
    "CARTRIDGES": "Cartridges",
    "EDIBLES": "Edibles",
    "CONCENTRATES": "Concentrates",
    "PREROLLS": "Prerolls",
    "SHROOMS": "Shrooms",
    "FLOWER": "Flower",
    "DATEDPROOFS": "Dated Proofs",
    "CLIENTTOUCHDOWNS": "Client Touchdowns",

    # Cartridges subcategories
    "AUTHENTICS": "Authentics",
    "REPLICAS": "Replicas",

    # Edibles subcategories
    "FLOWER_EDIBLES": "Flower Edibles",
    "SHROOM_EDIBLES": "Shroom Edibles",

    # Concentrates subcategories
    "SNOWCAPS": "Snowcaps",
    "MOONROCKS": "Moonrocks",
    "HASH_AND_KIEF": "Hash & Kief",
    "BADDER": "Badder",
    "SHATTER": "Shatter",
    "DISTILLATE": "Distillate",
    "THCA_POWDER": "THCA Powder",
    "RSO": "RSO",
    "ROSIN": "Rosin",
    "SUGAR": "Sugar",
    "OTHERS": "Others",

    # Prerolls subcategories
    "FLOWER_PREROLLS": "Flower Prerolls",
    "INFUSED_FLOWER_PREROLLS": "Infused Flower Prerolls",

    # Flower subcategories
    "TOPSHELF_CANDY": "Topshelf Candy",
    "PREMIUM_EXOTICS": "Premium Exotics",
    "EXOTICS": "Exotics",
    "PREMIUM_LIGHT_DEPS": "Premium Light Deps",
    "LIGHT_DEPS": "Light Deps",
    "LIGHT_ASSIST": "Light Assist",
    "LOWS": "Lows",
}

# Utility: ensure keys are canonical (uppercase, stripped)
def canonicalize_key(key: str) -> str:
    """Return canonical form for a taxonomy key (uppercase, underscores for spaces)."""
    if not key:
        return ""
    return key.strip().upper().replace(" ", "_")