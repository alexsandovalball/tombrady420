"""
Translation service using deep-translator library for multi-language support.
Provides automatic translation of bot messages based on user language preferences.
"""
import logging
from typing import Optional
from deep_translator import GoogleTranslator
from translations.language_config import DEFAULT_LANGUAGE, is_valid_language
from translations.strings import get_string as get_base_string

logger = logging.getLogger(__name__)


class TranslationService:
    """Service for translating bot messages to different languages."""
    
    def __init__(self):
        """Initialize translation service."""
        self._cache = {}  # Simple cache for translations
    
    def translate(self, text: str, target_lang: str, source_lang: str = "en") -> str:
        """
        Translate text from source language to target language.
        
        Args:
            text: Text to translate
            target_lang: Target language code
            source_lang: Source language code (default: 'en')
        
        Returns:
            Translated text, or original text if translation fails
        """
        # Normalize language codes (both USA and UK English use 'en')
        normalized_target = target_lang if target_lang != "en-US" else "en"
        normalized_source = source_lang if source_lang != "en-US" else "en"
        
        # No translation needed if target is same as source
        if normalized_target == normalized_source:
            return text
        
        # Validate language code
        if not is_valid_language(target_lang):
            logger.warning(f"Invalid language code: {target_lang}, using default")
            return text
        
        # Check cache
        cache_key = f"{normalized_source}:{normalized_target}:{text}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        try:
            # Use GoogleTranslator from deep-translator
            translator = GoogleTranslator(source=normalized_source, target=normalized_target)
            translated = translator.translate(text)
            
            # Cache the result
            self._cache[cache_key] = translated
            
            return translated
        except Exception as e:
            logger.error(f"Translation error ({normalized_source} -> {normalized_target}): {e}")
            return text  # Return original text if translation fails
    
    def get_string(self, key: str, lang: str = DEFAULT_LANGUAGE, **kwargs) -> str:
        """
        Get a translatable string by key and translate it to the target language.
        
        Args:
            key: String key from strings.py
            lang: Target language code
            **kwargs: Format arguments for the string
        
        Returns:
            Translated and formatted string
        """
        # Get base string in English
        base_string = get_base_string(key, **kwargs)
        
        # If language is English or default, return as is
        if lang == DEFAULT_LANGUAGE:
            return base_string
        
        # Translate to target language
        return self.translate(base_string, lang)
    
    def clear_cache(self):
        """Clear the translation cache."""
        self._cache.clear()


# Create singleton instance
translation_service = TranslationService()


def get_translated_string(key: str, lang: str = DEFAULT_LANGUAGE, **kwargs) -> str:
    """
    Convenience function to get a translated string.
    
    Args:
        key: String key from strings.py
        lang: Target language code
        **kwargs: Format arguments for the string
    
    Returns:
        Translated and formatted string
    """
    return translation_service.get_string(key, lang, **kwargs)


def translate_text(text: str, target_lang: str) -> str:
    """
    Convenience function to translate arbitrary text.
    
    Args:
        text: Text to translate
        target_lang: Target language code
    
    Returns:
        Translated text
    """
    return translation_service.translate(text, target_lang)
