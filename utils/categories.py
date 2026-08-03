"""
Category extraction and management utilities.
"""
from typing import Optional, List

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
        "TOPSHELFCANDY", "EXOTICS",
        "LIGHTDEPS", "LIGHTASSIST", "LOWS",
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

SUBCATEGORY_DISPLAY = {
    "AUTHENTICS": "Authentics",
    "REPLICAS": "Replicas",
    "FLOWER EDIBLES": "Flower Edibles",
    "SHROOM EDIBLES": "Shroom Edibles",
    "SOLVENTLESS": "🌿 Solventless",
    "LIVE RESIN AND EXTRACTS": "🔥 Live Resin & Extracts",
    "OILS AND ISOLATES": "💧 Oils & Isolates",
    "HASH AND KIEF": "🍵 Hash & Kief",
    "OTHERS": "📦 Others",
    "FLOWER PREROLLS": "Flower Pre-Rolls",
    "INFUSED FLOWER PREROLLS": "Infused Flower Pre-Rolls",
    "TOPSHELFCANDY": "Top Shelf Candy",
    "EXOTICS": "Exotics",
    "LIGHTDEPS": "Light Deps",
    "LIGHTASSIST": "Light Assist",
    "LOWS": "Lows",
    "MOONROCKS AND SNOWCAPS": "🌙 Moonrocks/Snowcaps",
}

SUBCATEGORY_ALIASES = {
    "FLOWER": {
        "PREMIUMEXOTICS": "EXOTICS",
        "PREMIUM EXOTICS": "EXOTICS",
        "PREMIUMLIGHTDEPS": "LIGHTDEPS",
        "PREMIUM LIGHT DEPS": "LIGHTDEPS",
    }
}

_CATEGORY_TRANSLATIONS = {
    "nl": {
        "CARTRIDGES": "🛒 Cartridges",
        "EDIBLES": "🍫 Edibles",
        "CONCENTRATES": "💎 Concentraten",
        "PREROLLS": "🚬 Pre-rolls",
        "SHROOMS": "🍄 Paddenstoelen",
        "FLOWER": "🌸 Bloem",
        "DATEDPROOFS": "📅 Gedateerde Bewijzen",
        "CLIENTTOUCHDOWNS": "✈️ Klant Touchdowns",
        "ANNOUNCEMENTS": "📢 Aankondigingen",
    },
    "de": {
        "CARTRIDGES": "🛒 Kartuschen",
        "EDIBLES": "🍫 Edibles",
        "CONCENTRATES": "💎 Konzentrate",
        "PREROLLS": "🚬 Pre-Rolls",
        "SHROOMS": "🍄 Pilze",
        "FLOWER": "🌸 Blüten",
        "DATEDPROOFS": "📅 Datierte Nachweise",
        "CLIENTTOUCHDOWNS": "✈️ Kunden Touchdowns",
        "ANNOUNCEMENTS": "📢 Ankündigungen",
    },
    "it": {
        "CARTRIDGES": "🛒 Cartucce",
        "EDIBLES": "🍫 Edibili",
        "CONCENTRATES": "💎 Concentrati",
        "PREROLLS": "🚬 Pre-roll",
        "SHROOMS": "🍄 Funghi",
        "FLOWER": "🌸 Fiori",
        "DATEDPROOFS": "📅 Prove Datate",
        "CLIENTTOUCHDOWNS": "✈️ Touchdown Clienti",
        "ANNOUNCEMENTS": "📢 Annunci",
    },
    "es": {
        "CARTRIDGES": "🛒 Cartuchos",
        "EDIBLES": "🍫 Comestibles",
        "CONCENTRATES": "💎 Concentrados",
        "PREROLLS": "🚬 Pre-rolls",
        "SHROOMS": "🍄 Hongos",
        "FLOWER": "🌸 Flor",
        "DATEDPROOFS": "📅 Pruebas con Fecha",
        "CLIENTTOUCHDOWNS": "✈️ Entregas de Clientes",
        "ANNOUNCEMENTS": "📢 Anuncios",
    },
    "fr": {
        "CARTRIDGES": "🛒 Cartouches",
        "EDIBLES": "🍫 Comestibles",
        "CONCENTRATES": "💎 Concentrés",
        "PREROLLS": "🚬 Pré-rolls",
        "SHROOMS": "🍄 Champignons",
        "FLOWER": "🌸 Fleurs",
        "DATEDPROOFS": "📅 Preuves Datées",
        "CLIENTTOUCHDOWNS": "✈️ Touchdowns Clients",
        "ANNOUNCEMENTS": "📢 Annonces",
    },
}

_SUBCATEGORY_TRANSLATIONS = {
    "nl": {
        "AUTHENTICS": "Authentiek",
        "REPLICAS": "Replica's",
        "FLOWER EDIBLES": "Flower Edibles",
        "SHROOM EDIBLES": "Shroom Edibles",
        "SOLVENTLESS": "🌿 Oplosmiddelvrij",
        "LIVE RESIN AND EXTRACTS": "🔥 Live Resin & Extracten",
        "OILS AND ISOLATES": "💧 Oliën & Isolaten",
        "HASH AND KIEF": "🍵 Hasj & Kief",
        "OTHERS": "📦 Overig",
        "FLOWER PREROLLS": "Flower Pre-rolls",
        "INFUSED FLOWER PREROLLS": "Infused Flower Pre-rolls",
        "LOWS": "Lows",
    },
    "de": {
        "AUTHENTICS": "Authentisch",
        "REPLICAS": "Repliken",
        "FLOWER EDIBLES": "Blüten-Edibles",
        "SHROOM EDIBLES": "Pilz-Edibles",
        "SOLVENTLESS": "🌿 Lösungsmittelfrei",
        "LIVE RESIN AND EXTRACTS": "🔥 Live Resin & Extrakte",
        "OILS AND ISOLATES": "💧 Öle & Isolate",
        "HASH AND KIEF": "🍵 Hasch & Kief",
        "OTHERS": "📦 Sonstiges",
        "FLOWER PREROLLS": "Blüten Pre-Rolls",
        "INFUSED FLOWER PREROLLS": "Infused Blüten Pre-Rolls",
    },
    "it": {
        "AUTHENTICS": "Autentici",
        "REPLICAS": "Repliche",
        "FLOWER EDIBLES": "Edibili Flower",
        "SHROOM EDIBLES": "Edibili Shroom",
        "SOLVENTLESS": "🌿 Senza solventi",
        "LIVE RESIN AND EXTRACTS": "🔥 Live Resin & Estratti",
        "OILS AND ISOLATES": "💧 Oli & Isolati",
        "HASH AND KIEF": "🍵 Hash & Kief",
        "OTHERS": "📦 Altro",
        "FLOWER PREROLLS": "Pre-roll Flower",
        "INFUSED FLOWER PREROLLS": "Pre-roll Flower Infusi",
    },
    "es": {
        "AUTHENTICS": "Auténticos",
        "REPLICAS": "Réplicas",
        "FLOWER EDIBLES": "Comestibles Flower",
        "SHROOM EDIBLES": "Comestibles Shroom",
        "SOLVENTLESS": "🌿 Sin solventes",
        "LIVE RESIN AND EXTRACTS": "🔥 Live Resin & Extractos",
        "OILS AND ISOLATES": "💧 Aceites & Aislados",
        "HASH AND KIEF": "🍵 Hash & Kief",
        "OTHERS": "📦 Otros",
        "FLOWER PREROLLS": "Pre-rolls Flower",
        "INFUSED FLOWER PREROLLS": "Pre-rolls Flower Infundidos",
    },
    "fr": {
        "AUTHENTICS": "Authentiques",
        "REPLICAS": "Répliques",
        "FLOWER EDIBLES": "Comestibles Flower",
        "SHROOM EDIBLES": "Comestibles Shroom",
        "SOLVENTLESS": "🌿 Sans solvant",
        "LIVE RESIN AND EXTRACTS": "🔥 Live Resin & Extraits",
        "OILS AND ISOLATES": "💧 Huiles & Isolats",
        "HASH AND KIEF": "🍵 Hash & Kief",
        "OTHERS": "📦 Autres",
        "FLOWER PREROLLS": "Pré-rolls Flower",
        "INFUSED FLOWER PREROLLS": "Pré-rolls Flower Infusés",
    },
}

_CATALOG_UI_TEXT = {
    "en": {
        "product_categories": "📂 **Product Categories**\n\nSelect a category to browse products:",
        "all_products": "📦 All Products",
        "button_all_in_category": "📦 All {category}",
        "button_back_to_categories": "« Back to Categories",
        "button_back_to_subcategories": "🔙 Back to Subcategories",
        "button_refresh": "🔄 Refresh",
        "button_previous_page": "« Previous",
        "button_next_page": "Next »",
        "page_indicator": "Page {page}/{total_pages}",
        "select_subcategory": "Select a subcategory to browse products:",
        "showing_products": "Showing {current} of {total} products",
        "no_products_in_category": "📭 No products in this {context}.",
    },
    "nl": {
        "product_categories": "📂 **Productcategorieën**\n\nKies een categorie om producten te bekijken:",
        "all_products": "📦 Alle Producten",
        "button_all_in_category": "📦 Alles in {category}",
        "button_back_to_categories": "« Terug naar Categorieën",
        "button_back_to_subcategories": "🔙 Terug naar Subcategorieën",
        "button_refresh": "🔄 Vernieuwen",
        "button_previous_page": "« Vorige",
        "button_next_page": "Volgende »",
        "page_indicator": "Pagina {page}/{total_pages}",
        "select_subcategory": "Kies een subcategorie om producten te bekijken:",
        "showing_products": "Toont {current} van {total} producten",
        "no_products_in_category": "📭 Geen producten in deze {context}.",
    },
    "de": {
        "product_categories": "📂 **Produktkategorien**\n\nWähle eine Kategorie zum Durchsuchen:",
        "all_products": "📦 Alle Produkte",
        "button_all_in_category": "📦 Alle in {category}",
        "button_back_to_categories": "« Zurück zu Kategorien",
        "button_back_to_subcategories": "🔙 Zurück zu Unterkategorien",
        "button_refresh": "🔄 Aktualisieren",
        "button_previous_page": "« Zurück",
        "button_next_page": "Weiter »",
        "page_indicator": "Seite {page}/{total_pages}",
        "select_subcategory": "Wähle eine Unterkategorie:",
        "showing_products": "Zeige {current} von {total} Produkten",
        "no_products_in_category": "📭 Keine Produkte in diesem {context}.",
    },
    "it": {
        "product_categories": "📂 **Categorie Prodotti**\n\nScegli una categoria:",
        "all_products": "📦 Tutti i Prodotti",
        "button_all_in_category": "📦 Tutto in {category}",
        "button_back_to_categories": "« Torna alle Categorie",
        "button_back_to_subcategories": "🔙 Torna alle Sottocategorie",
        "button_refresh": "🔄 Aggiorna",
        "button_previous_page": "« Precedente",
        "button_next_page": "Successiva »",
        "page_indicator": "Pagina {page}/{total_pages}",
        "select_subcategory": "Scegli una sottocategoria:",
        "showing_products": "Mostra {current} di {total} prodotti",
        "no_products_in_category": "📭 Nessun prodotto in questo {context}.",
    },
    "es": {
        "product_categories": "📂 **Categorías de Productos**\n\nElige una categoría:",
        "all_products": "📦 Todos los Productos",
        "button_all_in_category": "📦 Todo en {category}",
        "button_back_to_categories": "« Volver a Categorías",
        "button_back_to_subcategories": "🔙 Volver a Subcategorías",
        "button_refresh": "🔄 Actualizar",
        "button_previous_page": "« Anterior",
        "button_next_page": "Siguiente »",
        "page_indicator": "Página {page}/{total_pages}",
        "select_subcategory": "Elige una subcategoría:",
        "showing_products": "Mostrando {current} de {total} productos",
        "no_products_in_category": "📭 No hay productos en este {context}.",
    },
    "fr": {
        "product_categories": "📂 **Catégories de Produits**\n\nChoisis une catégorie:",
        "all_products": "📦 Tous les Produits",
        "button_all_in_category": "📦 Tout dans {category}",
        "button_back_to_categories": "« Retour aux Catégories",
        "button_back_to_subcategories": "🔙 Retour aux Sous-catégories",
        "button_refresh": "🔄 Actualiser",
        "button_previous_page": "« Précédent",
        "button_next_page": "Suivant »",
        "page_indicator": "Page {page}/{total_pages}",
        "select_subcategory": "Choisis une sous-catégorie:",
        "showing_products": "Affiche {current} sur {total} produits",
        "no_products_in_category": "📭 Aucun produit dans ce {context}.",
    },
}

# Categories to exclude from "All Products" view
# Add category names here to hide them from the "All Products" button/view
# while keeping them accessible when browsing categories directly
EXCLUDED_FROM_ALL_PRODUCTS = ["DATEDPROOFS", "CLIENTTOUCHDOWNS", "ANNOUNCEMENTS"]

# Categories to exclude from triggering notifications
# All categories now trigger notifications
NOTIFICATION_EXCLUDED_CATEGORIES = []


def _normalize_lang(user_lang: str = "en") -> str:
    lang = (user_lang or "en").split("-", 1)[0].lower()
    return lang if lang in _CATALOG_UI_TEXT else "en"


def get_catalog_ui_text(key: str, user_lang: str = "en", **kwargs) -> str:
    """Return a fast static catalog/navigation label without live translation."""
    lang = _normalize_lang(user_lang)
    template = _CATALOG_UI_TEXT.get(lang, {}).get(key) or _CATALOG_UI_TEXT["en"].get(key, key)
    if kwargs:
        try:
            return template.format(**kwargs)
        except (KeyError, ValueError):
            return template
    return template


def get_category_display_name(category: str, user_lang: str = "en") -> str:
    """Get display name for a category."""
    lang = _normalize_lang(user_lang)
    translated = _CATEGORY_TRANSLATIONS.get(lang, {}).get(category)
    if translated:
        return translated
    return CATEGORY_DISPLAY.get(category, category)


def get_all_categories() -> List[str]:
    """Get list of all category names."""
    return list(CATEGORIES.keys())


def get_subcategories(category: str) -> List[str]:
    """Get subcategories for a given category."""
    return CATEGORIES.get(category, [])


def canonicalize_subcategory(category: Optional[str], subcategory: Optional[str]) -> Optional[str]:
    """Map retired subcategory names to the canonical visible subcategory."""
    if not subcategory:
        return subcategory
    category_key = (category or "").strip().upper()
    subcategory_key = str(subcategory).strip().upper()
    return SUBCATEGORY_ALIASES.get(category_key, {}).get(subcategory_key, subcategory)


def get_subcategory_display_name(subcategory: str, user_lang: str = "en") -> str:
    """
    Get display name for a subcategory without live network translation.
    
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
    lang = _normalize_lang(user_lang)
    translated = _SUBCATEGORY_TRANSLATIONS.get(lang, {}).get(subcategory)
    if translated:
        return translated
    return SUBCATEGORY_DISPLAY.get(subcategory, subcategory.title())


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
        return "Uncategorized"
    
    display = get_category_display_name(category, user_lang)
    if subcategory:
        translated_subcat = get_subcategory_display_name(subcategory, user_lang)
        display += f" • {translated_subcat}"
    
    return display
