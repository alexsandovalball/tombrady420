"""
Translation service using deep-translator library for multi-language support.
Provides automatic translation of bot messages based on user language preferences.
"""
import logging
import asyncio
import concurrent.futures
import re
from typing import Optional, Tuple, Dict
from collections import OrderedDict
from deep_translator import GoogleTranslator
from translations.language_config import DEFAULT_LANGUAGE, is_valid_language
from translations.strings import get_string as get_base_string

logger = logging.getLogger(__name__)


def protect_placeholders(text: str) -> Tuple[str, Dict[str, str]]:
    """
    Replace formatting placeholders with protected markers that won't be translated.
    
    Args:
        text: Text containing placeholders like {name}, {contact}, etc.
    
    Returns:
        Tuple of (protected_text, placeholder_map) where placeholder_map can be used to restore
    """
    # Find all placeholders like {name}, {contact}, {category}, etc.
    placeholder_pattern = r'\{([^}]+)\}'
    placeholders = re.findall(placeholder_pattern, text)
    
    # Create a mapping of placeholders to protected markers
    placeholder_map = {}
    protected_text = text
    
    for i, placeholder in enumerate(placeholders):
        # Use markers unlikely to be translated: XPLACEHOLDERX0X, XPLACEHOLDERX1X, etc.
        marker = f"XPLACEHOLDERX{i}X"
        original = f"{{{placeholder}}}"
        placeholder_map[marker] = original
        protected_text = protected_text.replace(original, marker, 1)
    
    return protected_text, placeholder_map


def restore_placeholders(text: str, placeholder_map: Dict[str, str]) -> str:
    """
    Restore original placeholders from protected markers.
    
    Args:
        text: Text with protected markers
        placeholder_map: Mapping from markers to original placeholders
    
    Returns:
        Text with original placeholders restored
    """
    restored_text = text
    for marker, original in placeholder_map.items():
        restored_text = restored_text.replace(marker, original)
    return restored_text


def normalize_language_code(lang: str) -> str:
    """
    Normalize language codes for consistency.
    Both USA and UK English use 'en'.
    
    Args:
        lang: Language code to normalize
    
    Returns:
        Normalized language code
    """
    return "en" if lang == "en-US" else lang


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
    
    def __init__(self, cache_size: int = 1000, max_workers: int = 3, db=None):
        """
        Initialize translation service with bounded cache.
        
        Args:
            cache_size: Maximum size of in-memory cache
            max_workers: Maximum number of worker threads
            db: Optional Database instance for persistent caching
        """
        self._cache = BoundedCache(max_size=cache_size)
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        self._db = db
    
    def set_database(self, db):
        """Set the database instance for persistent caching."""
        self._db = db
    
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
        # Normalize language codes
        normalized_target = normalize_language_code(target_lang)
        normalized_source = normalize_language_code(source_lang)
        
        # No translation needed if target is same as source
        if normalized_target == normalized_source:
            return text
        
        # Validate language code
        if not is_valid_language(target_lang):
            logger.warning(f"Invalid language code: {target_lang}, using default")
            return text
        
        # Check in-memory cache first
        cache_key = f"{normalized_source}:{normalized_target}:{text}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        
        # Check database cache if available
        if self._db:
            try:
                db_cached = await self._db.get_cached_translation(text, normalized_source, normalized_target)
                if db_cached:
                    # Also update in-memory cache
                    self._cache.set(cache_key, db_cached)
                    return db_cached
            except Exception as e:
                logger.error(f"Error checking database translation cache: {e}")
        
        try:
            # Protect placeholders before translation
            protected_text, placeholder_map = protect_placeholders(text)
            
            # Run translation in thread pool to avoid blocking event loop
            loop = asyncio.get_event_loop()
            
            def _translate_sync():
                translator = GoogleTranslator(source=normalized_source, target=normalized_target)
                return translator.translate(protected_text)
            
            translated = await loop.run_in_executor(self._executor, _translate_sync)
            
            # Restore placeholders after translation
            translated = restore_placeholders(translated, placeholder_map)
            
            # Cache the result in memory
            self._cache.set(cache_key, translated)
            
            # Cache the result in database if available
            if self._db:
                try:
                    await self._db.cache_translation(text, normalized_source, normalized_target, translated)
                except Exception as e:
                    logger.error(f"Error saving translation to database: {e}")
            
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
        # If language is English or default, format and return without translation
        if lang == DEFAULT_LANGUAGE:
            return get_base_string(key, **kwargs)
        
        # Get base string template in English (without formatting)
        base_string = get_base_string(key)
        
        # Translate the template (with placeholders intact)
        translated = await self.translate(base_string, lang)
        
        # Format the translated string with actual values
        if kwargs:
            try:
                return translated.format(**kwargs)
            except (KeyError, ValueError):
                # If formatting fails, return as-is
                return translated
        return translated
    
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
    # If language is English or default, format and return without translation
    if lang == DEFAULT_LANGUAGE:
        return get_base_string(key, **kwargs)
    
    # Normalize language codes
    normalized_target = normalize_language_code(lang)
    
    # If normalized language is default, format and return without translation
    if normalized_target == DEFAULT_LANGUAGE:
        return get_base_string(key, **kwargs)
    
    # Validate normalized language code
    if not is_valid_language(normalized_target):
        logger.warning(f"Invalid language code: {normalized_target}, using default")
        return get_base_string(key, **kwargs)
    
    # Get base string template in English (without formatting)
    base_string = get_base_string(key)
    
    # Check cache first (cache key uses the template, not the formatted string)
    cache_key = f"en:{normalized_target}:{base_string}"
    cached = translation_service._cache.get(cache_key)
    
    if cached is not None:
        # Format the cached translation with actual values
        if kwargs:
            try:
                return cached.format(**kwargs)
            except (KeyError, ValueError):
                return cached
        return cached
    
    # If not in cache, perform translation synchronously
    try:
        # Protect placeholders before translation
        protected_text, placeholder_map = protect_placeholders(base_string)
        
        translator = GoogleTranslator(source="en", target=normalized_target)
        translated = translator.translate(protected_text)
        
        # Restore placeholders after translation
        translated = restore_placeholders(translated, placeholder_map)
        
        # Cache the result (cache the template, not the formatted string)
        translation_service._cache.set(cache_key, translated)
        
        # Format the translated string with actual values
        if kwargs:
            try:
                return translated.format(**kwargs)
            except (KeyError, ValueError):
                return translated
        return translated
    except Exception as e:
        logger.error(f"Translation error (en -> {normalized_target}): {e}")
        return get_base_string(key, **kwargs)


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
    # Normalize language codes
    normalized_target = normalize_language_code(target_lang)
    
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
        # Protect placeholders before translation
        protected_text, placeholder_map = protect_placeholders(text)
        
        translator = GoogleTranslator(source="en", target=normalized_target)
        translated = translator.translate(protected_text)
        
        # Restore placeholders after translation
        translated = restore_placeholders(translated, placeholder_map)
        
        # Cache the result
        translation_service._cache.set(cache_key, translated)
        
        return translated
    except Exception as e:
        logger.error(f"Translation error (en -> {normalized_target}): {e}")
        return text

