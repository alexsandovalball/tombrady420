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

_CANNA_CATEGORY_TOKENS = {
    "flower", "cannabis", "weed", "bud", "buds", "exotic", "exotics", "indica", "sativa", "hybrid",
    "pre roll", "pre rolls", "preroll", "prerolls", "cart", "carts", "cartridge", "cartridges",
    "edible", "edibles", "gummies", "gummy", "concentrate", "concentrates", "hash", "kief",
    "rosin", "resin", "vape", "vapes", "pen", "pens", "tincture", "top shelf", "premium",
    "light dep", "light deps", "light deprivation", "moonrocks", "snowcaps", "infused", "solventless",
}

_CANNA_VARIANT_FAMILIES = {
    "flower": [
        "cannabis flower", "weed flower", "bud", "buds", "top shelf flower", "premium flower",
        "indoor flower", "greenhouse flower", "sun grown flower", "light dep flower", "light deprivation flower",
        "designer flower", "exotic flower", "exotic weed", "top shelf bud",
    ],
    "concentrates": [
        "concentrates", "extracts", "dabs", "live resin", "live rosin", "hash rosin", "rosin",
        "solventless", "solventless concentrate", "full melt", "bubble hash", "water hash", "hash",
        "kief", "dry sift", "sift hash", "terp sauce", "badder", "budder", "wax", "shatter",
    ],
    "cartridges": [
        "cartridge", "cartridges", "cart", "carts", "vape cart", "weed cart", "vape pen",
        "disposable vape", "thc vape", "live resin cart", "rosin cart", "cannabis vape",
    ],
    "edibles": [
        "edibles", "gummies", "gummy", "cannabis edibles", "thc edibles", "weed gummies",
        "infused candy", "chocolate edibles", "cookies", "brownies", "soft chews",
    ],
    "prerolls": [
        "pre rolls", "pre-rolls", "prerolls", "pre rolled joints", "joints", "blunts",
        "flower pre rolls", "infused prerolls", "infused pre rolls", "ready to smoke",
    ],
}

_CANNA_TREND_HINTS = {
    "flower", "cannabis", "weed", "bud", "buds", "exotic", "exotics", "indica", "sativa", "hybrid",
    "pre roll", "pre rolls", "preroll", "prerolls", "cart", "carts", "cartridge", "cartridges",
    "edible", "edibles", "gummies", "gummy", "concentrate", "concentrates", "extract", "extracts",
    "hash", "kief", "rosin", "resin", "vape", "vapes", "tincture", "top shelf", "premium",
    "light dep", "light deps", "light deprivation", "moonrocks", "snowcaps", "infused", "solventless",
    "static", "statico", "estatico", "estático", "filtered", "filter", "filt", "filtr",
    "filtre", "filtré", "filtree", "filtrée", "filtrado",
}

_COMMUNITY_INTENT_HINTS = _CANNA_TREND_HINTS | {
    "thc", "cbd", "terp", "terps", "terpene", "terpenes", "strain", "strains", "kush", "haze",
    "runtz", "gelato", "sherb", "sherbet", "zkittlez", "zkittles", "cookies", "og", "diesel",
    "amnesia", "gorilla", "glue", "zaza", "cali", "topshelf", "top shelf", "plug",
    "mushroom", "mushrooms", "shroom", "shrooms", "magic mushroom", "magic mushrooms",
    "psilocybin", "microdose", "microdosing", "capsule", "capsules", "spores", "mycology",
    "truffle", "truffles", "amanita", "cubensis", "golden teacher", "penis envy",
    "cannabis", "cannabisblüte", "blüte", "hasch", "haschisch", "gras", "wiet", "hasj",
    "fleur", "beuh", "herbe", "résine", "resine", "filtré", "filtre", "hachís", "hachis", "haschich",
    "marihuana", "marijuana", "hierba", "flor", "fiore", "erba", "fungo", "funghi",
}

_BAD_AUTO_SEO_TERMS = {
    "autre maman", "ostra mami", "altra mamma", "andere mama", "other mommy",
    "other mother", "another mother", "autre mère", "autre mere", "otra madre",
    "altra madre", "andere mutter", "andere moeder", "mommy", "maman", "mamma",
    "mami", "mama", "mother",
}


def has_search_audience_intent(term: str) -> bool:
    """Return true when a term looks relevant to cannabis/mushroom search intent."""
    normalized = normalize_term(term)
    if not normalized:
        return False
    return any(hint in normalized for hint in _COMMUNITY_INTENT_HINTS)


def is_bad_auto_seo_term(term: str) -> bool:
    """Block known translation noise that attracts the wrong audience."""
    normalized = normalize_term(term)
    if not normalized:
        return True
    if has_search_audience_intent(normalized):
        return False
    if normalized in _BAD_AUTO_SEO_TERMS:
        return True
    family_noise = {"mommy", "mother", "maman", "mamma", "mama", "mami", "madre", "mutter", "moeder"}
    qualifier_noise = {"other", "another", "autre", "otra", "ostras", "altra", "andere"}
    tokens = set(normalized.split())
    return bool(tokens & family_noise) and bool(tokens <= (family_noise | qualifier_noise))


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
        if category in _CANNA_VARIANT_FAMILIES:
            category_terms.extend(_CANNA_VARIANT_FAMILIES[normalize_term(category)])
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
            sub_key = normalize_term(subcategory)
            if sub_key in _CANNA_VARIANT_FAMILIES:
                subcategory_terms.extend(_CANNA_VARIANT_FAMILIES[sub_key])
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


def build_taxonomy_profiles(products: Iterable[dict[str, Any]]) -> dict[tuple[str, str], dict[str, Any]]:
    """Build reusable taxonomy profiles for a batch of SEO inference calls."""
    return _build_taxonomy_profiles(products)


def infer_seo_query_mapping(
    query: str,
    products: Iterable[dict[str, Any]],
    *,
    min_score: int = 92,
    profiles: Optional[dict[tuple[str, str], dict[str, Any]]] = None,
) -> Optional[dict[str, Any]]:
    """Infer a safe category/subcategory mapping for one no-result query."""
    query_norm = normalize_term(query)
    if len(query_norm) < 3:
        return None

    profiles = profiles or _build_taxonomy_profiles(products)
    scored: list[dict[str, Any]] = []
    query_tokens = set(query_norm.split())
    cannabisish = any(token in query_norm for token in _CANNA_TREND_HINTS)

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

        if cannabisish and best_score < 94:
            profile_terms = [normalize_term(term) for term in profile.get("terms", [])]
            overlap = len(query_tokens & set(" ".join(profile_terms).split()))
            if overlap:
                best_score = max(best_score, min(97.0, 74.0 + (overlap * 7.0)))
                if best_score > 0 and not best_reason:
                    best_reason = "cannabis_vocab"

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
    if runner_up and best["score"] < 99 and (best["score"] - runner_up["score"]) < 6:
        logger.debug(
            "[seo_auto_learn] ambiguous query=%r best=%s runner_up=%s",
            query,
            best,
            runner_up,
        )
        return None

    return best
