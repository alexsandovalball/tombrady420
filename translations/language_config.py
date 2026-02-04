"""
Language configuration and supported languages.
"""

# Supported languages with their codes
SUPPORTED_LANGUAGES = {
    "en": "English",
    "de": "German",
    "nl": "Dutch",  # Belgian and Dutch both use Dutch language
    "it": "Italian",
    "es": "Spanish",
    "fr": "French"
}

# Default language
DEFAULT_LANGUAGE = "en"

# Language display names with emojis
LANGUAGE_DISPLAY = {
    "en": "🇬🇧 English (UK)",
    "en-US": "🇺🇸 English (USA)",
    "de": "🇩🇪 German",
    "nl": "🇳🇱 Dutch",
    "it": "🇮🇹 Italian",
    "es": "🇪🇸 Spanish",
    "fr": "🇫🇷 French"
}


def get_language_name(lang_code: str) -> str:
    """Get language display name from code."""
    return LANGUAGE_DISPLAY.get(lang_code, LANGUAGE_DISPLAY[DEFAULT_LANGUAGE])


def get_all_languages() -> dict:
    """Get all supported languages."""
    return SUPPORTED_LANGUAGES


def is_valid_language(lang_code: str) -> bool:
    """Check if language code is supported."""
    return lang_code in SUPPORTED_LANGUAGES or lang_code in LANGUAGE_DISPLAY
