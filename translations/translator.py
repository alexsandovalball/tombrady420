"""
Translation service using deep-translator library for multi-language support.
Provides automatic translation of bot messages based on user language preferences.
"""
import logging
import asyncio
import concurrent.futures
from typing import Optional
from collections import OrderedDict
from deep_translator import GoogleTranslator
from translations.language_config import DEFAULT_LANGUAGE, is_valid_language
from translations.strings import get_string as get_base_string

logger = logging.getLogger(__name__)


class BoundedCache:
    """LRU cache with maximum size limit."""
    
    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self._cache = OrderedDict()
    
    def get(self, key: str) -> Optional[str]:
        """Get value from cache, moving it to end (most recent)."""
        if key not in self._cache:
            return None
        # Move to end (most recently used)
        self._cache.move_to_end(key)
        return self._cache[key]
    
    def set(self, key: str, value: str):
        """Set value in cache, removing oldest if at capacity."""
        if key in self._cache:
            # Update existing and move to end
            self._cache[key] = value
            self._cache.move_to_end(key)
        else:
            # Add new item
            self._cache[key] = value
            # Remove oldest if over capacity
            if len(self._cache) > self.max_size:
                self._cache.popitem(last=False)
    
    def clear(self):
        """Clear the cache."""
        self._cache.clear()


class TranslationService:
    """Service for translating bot messages to different languages."""
    
    def __init__(self, cache_size: int = 1000, max_workers: int = 3):
        """Initialize translation service with bounded cache."""
        self._cache = BoundedCache(max_size=cache_size)
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
    
    async def translate(self, text: str, target_lang: str, source_lang: str = "en") -> str:
        """
        Translate text from source language to target language asynchronously.
        
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
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        
        try:
            # Run translation in thread pool to avoid blocking event loop
            loop = asyncio.get_event_loop()
            
            def _translate_sync():
                translator = GoogleTranslator(source=normalized_source, target=normalized_target)
                return translator.translate(text)
            
            translated = await loop.run_in_executor(self._executor, _translate_sync)
            
            # Cache the result
            self._cache.set(cache_key, translated)
            
            return translated
        except Exception as e:
            logger.error(f"Translation error ({normalized_source} -> {normalized_target}): {e}")
            return text  # Return original text if translation fails
    
    async def get_string(self, key: str, lang: str = DEFAULT_LANGUAGE, **kwargs) -> str:
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
        return await self.translate(base_string, lang)
    
    def clear_cache(self):
        """Clear the translation cache."""
        self._cache.clear()


# Create singleton instance
translation_service = TranslationService()


async def get_translated_string_async(key: str, lang: str = DEFAULT_LANGUAGE, **kwargs) -> str:
    """
    Async function to get a translated string.
    
    Args:
        key: String key from strings.py
        lang: Target language code
        **kwargs: Format arguments for the string
    
    Returns:
        Translated and formatted string
    """
    return await translation_service.get_string(key, lang, **kwargs)


def get_translated_string(key: str, lang: str = DEFAULT_LANGUAGE, **kwargs) -> str:
    """
    Synchronous wrapper for getting a translated string.
    Performs actual translation if not in cache using synchronous GoogleTranslator.
    
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
    
    # Normalize language codes (both USA and UK English use 'en')
    normalized_target = lang if lang != "en-US" else "en"
    
    # If normalized language is default, return as is
    if normalized_target == DEFAULT_LANGUAGE:
        return base_string
    
    # Validate normalized language code
    if not is_valid_language(normalized_target):
        logger.warning(f"Invalid language code: {normalized_target}, using default")
        return base_string
    
    # Check cache first
    cache_key = f"en:{normalized_target}:{base_string}"
    cached = translation_service._cache.get(cache_key)
    
    if cached is not None:
        return cached
    
    # If not in cache, perform translation synchronously
    try:
        translator = GoogleTranslator(source="en", target=normalized_target)
        translated = translator.translate(base_string)
        
        # Cache the result
        translation_service._cache.set(cache_key, translated)
        
        return translated
    except Exception as e:
        logger.error(f"Translation error (en -> {normalized_target}): {e}")
        return base_string


async def translate_text_async(text: str, target_lang: str) -> str:
    """
    Async convenience function to translate arbitrary text.
    
    Args:
        text: Text to translate
        target_lang: Target language code
    
    Returns:
        Translated text
    """
    return await translation_service.translate(text, target_lang)


def translate_text(text: str, target_lang: str) -> str:
    """
    Synchronous wrapper to translate arbitrary text.
    Performs actual translation if not in cache.
    
    Args:
        text: Text to translate
        target_lang: Target language code
    
    Returns:
        Translated text
    """
    # Normalize language codes (both USA and UK English use 'en')
    normalized_target = target_lang if target_lang != "en-US" else "en"
    
    if normalized_target == "en":
        return text
    
    # Validate normalized language code
    if not is_valid_language(normalized_target):
        logger.warning(f"Invalid language code: {normalized_target}")
        return text
    
    cache_key = f"en:{normalized_target}:{text}"
    cached = translation_service._cache.get(cache_key)
    
    if cached is not None:
        return cached
    
    # If not in cache, perform translation synchronously
    try:
        translator = GoogleTranslator(source="en", target=normalized_target)
        translated = translator.translate(text)
        
        # Cache the result
        translation_service._cache.set(cache_key, translated)
        
        return translated
    except Exception as e:
        logger.error(f"Translation error (en -> {normalized_target}): {e}")
        return text

