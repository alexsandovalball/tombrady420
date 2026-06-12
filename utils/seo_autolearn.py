"""Conservative SEO query auto-learning.

The learner maps repeated no-result searches to existing catalog taxonomy only
when the query strongly matches approved SEO vocabulary, category labels, or
live product text. It never invents a new category.
"""

from __future__ import annotations

import difflib
import logging
from collections import defaultdict
from typing import Any, Iterable, Optional

from utils.categories import (
    CATEGORIES,
    EXCLUDED_FROM_ALL_PRODUCTS,
    get_category_display_name,
    get_subcategory_display_name,
)
from utils.seo_terms import iter_mappings, normalize_term
from translations.language_config import SUPPORTED_LANGUAGES

try:
    from rapidfuzz import fuzz

    RAPIDFUZZ_AVAILABLE = True
except ImportError:  # pragma: no cover - production has rapidfuzz, local fallback is tiny
    RAPIDFUZZ_AVAILABLE = False


logger = logging.getLogger(__name__)


def _plain_label(value: str) -> str:
    return normalize_term(value)


def _score_text(query_norm: str, text: str) -> float:
    text_norm = normalize_term(text)
    if not query_norm or not text_norm:
        return 0.0
    if query_norm == text_norm:
        return 100.0
    if len(query_norm) >= 4 and query_norm in text_norm:
        return 94.0
    if len(text_norm) >= 4 and text_norm in query_norm:
        return 91.0

    if RAPIDFUZZ_AVAILABLE:
        return max(
            float(fuzz.ratio(query_norm, text_norm)),
            float(fuzz.partial_ratio(query_norm, text_norm)) - 4.0,
            float(fuzz.token_set_ratio(query_norm, text_norm)) - 6.0,
        )
    return difflib.SequenceMatcher(None, query_norm, text_norm).ratio() * 100


def _taxonomy_key(category: str, subcategory: str = "") -> tuple[str, str]:
    return (str(category or "").strip().upper(), str(subcategory or "").strip().upper())


def _build_taxonomy_profiles(products: Iterable[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    profiles: dict[tuple[str, str], dict[str, Any]] = {}
    language_codes = list(SUPPORTED_LANGUAGES)

    for category, subcategories in CATEGORIES.items():
        if category in EXCLUDED_FROM_ALL_PRODUCTS:
            continue
        category_terms = [category, get_category_display_name(category)]
        category_terms.extend(get_category_display_name(category, lang) for lang in language_codes)
        if not subcategories:
            profiles[_taxonomy_key(category)] = {
                "category": category,
                "subcategory": "",
                "terms": list(dict.fromkeys(category_terms)),
                "captions": [],
            }
            continue
        for subcategory in subcategories:
            subcategory_terms = [subcategory, get_subcategory_display_name(subcategory)]
            subcategory_terms.extend(get_subcategory_display_name(subcategory, lang) for lang in language_codes)
            profiles[_taxonomy_key(category, subcategory)] = {
                "category": category,
                "subcategory": subcategory,
                "terms": list(dict.fromkeys([*category_terms, *subcategory_terms])),
                "captions": [],
            }

    for mapping in iter_mappings():
        key = _taxonomy_key(mapping.get("category", ""), mapping.get("subcategory", ""))
        if key in profiles:
            profiles[key]["terms"].extend(mapping.get("terms", []))

    seen_captions: dict[tuple[str, str], set[str]] = defaultdict(set)
    for product in products or []:
        key = _taxonomy_key(product.get("category", ""), product.get("subcategory", ""))
        if key not in profiles:
            category_key = _taxonomy_key(product.get("category", ""))
            key = category_key if category_key in profiles else key
        if key not in profiles:
            continue
        caption = str(product.get("caption") or "").strip()
        if not caption:
            continue
        first_line = caption.splitlines()[0].strip()
        for value in (first_line, caption[:500]):
            normalized = normalize_term(value)
            if normalized and normalized not in seen_captions[key]:
                profiles[key]["captions"].append(value)
                seen_captions[key].add(normalized)

    return profiles


def infer_seo_query_mapping(
    query: str,
    products: Iterable[dict[str, Any]],
    *,
    min_score: int = 92,
) -> Optional[dict[str, Any]]:
    """Infer a safe category/subcategory mapping for one no-result query."""
    query_norm = normalize_term(query)
    if len(query_norm) < 3:
        return None

    profiles = _build_taxonomy_profiles(products)
    scored: list[dict[str, Any]] = []

    for profile in profiles.values():
        best_score = 0.0
        best_reason = ""

        for term in profile.get("terms", []):
            score = _score_text(query_norm, term)
            if score > best_score:
                best_score = score
                best_reason = "seo_term" if normalize_term(term) != _plain_label(profile["category"]) else "category"

        for caption in profile.get("captions", [])[:250]:
            score = min(_score_text(query_norm, caption), 96.0)
            if score > best_score:
                best_score = score
                best_reason = "catalog"

        if best_score >= min_score:
            scored.append({
                "term": query.strip()[:300],
                "category": profile["category"],
                "subcategory": profile["subcategory"],
                "score": round(best_score, 1),
                "reason": best_reason,
            })

    if not scored:
        return None

    scored.sort(key=lambda item: item["score"], reverse=True)
    best = scored[0]
    runner_up = scored[1] if len(scored) > 1 else None

    # Ambiguous near-ties are skipped. Exact approved SEO vocabulary is allowed
    # because it came from the explicit SEO dictionary.
    if runner_up and best["score"] < 99 and (best["score"] - runner_up["score"]) < 8:
        logger.debug(
            "[seo_auto_learn] ambiguous query=%r best=%s runner_up=%s",
            query,
            best,
            runner_up,
        )
        return None

    return best
