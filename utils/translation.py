"""
Translation utilities for the bot.
Provides auto-translation capabilities for product captions.
"""
import logging
from typing import Optional
from deep_translator import GoogleTranslator

logger = logging.getLogger(__name__)

# Supported languages
SUPPORTED_LANGUAGES = {
    'en': 'English',
    'es': 'Spanish',
    'fr': 'French',
    'de': 'German',
    'it': 'Italian',
    'pt': 'Portuguese',
    'ru': 'Russian',
    'zh-CN': 'Chinese (Simplified)',
    'ja': 'Japanese',
    'ko': 'Korean',
    'ar': 'Arabic',
    'hi': 'Hindi'
}

# Default source language (auto-detect)
DEFAULT_SOURCE_LANG = 'auto'


def translate_text(text: str, target_lang: str, source_lang: str = DEFAULT_SOURCE_LANG) -> Optional[str]:
    """
    Translate text to the target language.
    
    Args:
        text: Text to translate
        target_lang: Target language code (e.g., 'es', 'fr')
        source_lang: Source language code (default: 'auto' for auto-detect)
        
    Returns:
        Translated text or None if translation fails
    """
    if not text or not text.strip():
        return text
    
    # Don't translate if target language is English (assumed default)
    if target_lang == 'en':
        return text
    
    try:
        translator = GoogleTranslator(source=source_lang, target=target_lang)
        translated = translator.translate(text)
        logger.debug(f"Translated text from {source_lang} to {target_lang}")
        return translated
    except Exception as e:
        logger.warning(f"Translation error (network may be unavailable): {e}")
        # Return None to indicate translation failed
        # The calling code should handle this and show original text
        return None


def get_language_name(lang_code: str) -> str:
    """Get the display name for a language code."""
    return SUPPORTED_LANGUAGES.get(lang_code, lang_code)


def is_supported_language(lang_code: str) -> bool:
    """Check if a language code is supported."""
    return lang_code in SUPPORTED_LANGUAGES
