# utils/translate.py
"""
Translation helper using a LibreTranslate-compatible HTTP API.

Configuration via environment:
 - TRANSLATE_API_URL (default 'https://libretranslate.com/translate')
 - TRANSLATE_API_KEY (optional)
Cache:
 - If app.bot_data['redis'] exists (aioredis client), it will use it.
 - Otherwise uses a small in-process LRU cache (best-effort; process-local only).
"""
from __future__ import annotations
import os
import json
import asyncio
import logging
from typing import Optional
from functools import lru_cache

import httpx

logger = logging.getLogger(__name__)

TRANSLATE_API_URL = os.getenv("TRANSLATE_API_URL", "https://libretranslate.com/translate")
TRANSLATE_API_KEY = os.getenv("TRANSLATE_API_KEY")  # optional

# simple in-process cache for small deployments
@lru_cache(maxsize=4096)
def _local_cache_key(text: str, target: str, source: Optional[str]) -> str:
    return f"{target}|{source or ''}|{text[:4000]}"

async def translate_text(text: str, target_lang: str, source_lang: Optional[str] = None, app_context=None, ttl: int = 60*60*24) -> str:
    """
    Translate `text` to `target_lang`. If app_context is provided, it will attempt to use:
      - app_context.bot_data.get('redis') for caching if available.
    Returns the translated text (or original text on failure).
    """
    if not text:
        return text

    # normalize target lang (e.g. "en", "es")
    target = (target_lang or "").lower()

    # short-circuit if target equals source or text is short and probably language-neutral
    if not target:
        return text

    # Try to get per-process cached translation
    local_key = _local_cache_key(text, target, source_lang)
    if local_key:
        try:
            # If redis is configured in app_context, use it
            if app_context and app_context.bot_data and app_context.bot_data.get("redis"):
                redis = app_context.bot_data["redis"]
                try:
                    cached = await redis.get(f"tr:{local_key}")
                    if cached:
                        return cached.decode("utf-8") if isinstance(cached, bytes) else str(cached)
                except Exception:
                    logger.debug("Redis read failed for translation cache (non-fatal)")
            else:
                # lru_cache used implicitly by _local_cache_key wrapper if we call an lru cached function
                pass
        except Exception:
            logger.debug("Translation cache lookup failed (non-fatal)")

    # Call remote translate service
    payload = {
        "q": text,
        "source": source_lang or "auto",
        "target": target,
        "format": "text",
    }
    headers = {"Content-Type": "application/json"}
    if TRANSLATE_API_KEY:
        payload["api_key"] = TRANSLATE_API_KEY

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.post(TRANSLATE_API_URL, json=payload, headers=headers)
            if r.status_code == 200:
                data = r.json()
                # LibreTranslate returns {"translatedText":"..."}
                translated = data.get("translatedText") or data.get("translation") or data.get("result") or ""
                if translated:
                    # Cache to Redis if available
                    if app_context and app_context.bot_data and app_context.bot_data.get("redis"):
                        try:
                            redis = app_context.bot_data["redis"]
                            await redis.setex(f"tr:{local_key}", ttl, translated)
                        except Exception:
                            logger.debug("Redis write failed for translation cache (non-fatal)")
                    else:
                        # populate local lru cache by calling a dummy cached function (store in process memory)
                        _local_set_cache(local_key, translated)
                    return translated
            else:
                logger.warning("Translate API returned status %s: %s", r.status_code, r.text[:400])
    except Exception:
        logger.exception("Translation API call failed")

    # fallback: return original
    return text


# small helper to populate the lru cache (since lru_cache wraps functions)
@lru_cache(maxsize=4096)
def _local_set_cache(key: str, value: str):
    # this function exists only to allow setting via lru_cache cache.
    # we return the value so callers can access it, but main usage is cache side-effect.
    return value