"""
Fuzzy search utilities for product search.
"""
import re
import unicodedata
from typing import List, Dict, Any
try:
    from rapidfuzz import process, fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    import difflib


# Domain-specific synonym/alias map for product searches
# Can be extended as needed
SEARCH_SYNONYMS = {
    "cart": "cartridge",
    "carts": "cartridge",
    "vape": "cartridge",
    "vapes": "cartridge",
    "pen": "cartridge",
    "edible": "edibles",
    "gummy": "edibles",
    "gummies": "edibles",
    "chocolate": "edibles",
    "concentrate": "concentrates",
    "wax": "concentrates",
    "dab": "concentrates",
    "dabs": "concentrates",
    "shatter": "concentrates",
    "preroll": "pre-roll",
    "prerolls": "pre-roll",
    "joint": "pre-roll",
    "joints": "pre-roll",
    "blunt": "pre-roll",
    "blunts": "pre-roll",
    "mushroom": "shroom",
    "mushrooms": "shroom",
    "shrooms": "shroom",
    "bud": "flower",
    "buds": "flower",
    "weed": "flower",
}


def normalize_text(text: str) -> str:
    """
    Normalize text for consistent searching.

    - Lowercase
    - Collapse whitespace
    - Remove punctuation (except alphanumeric and spaces)

    Args:
        text: Text to normalize

    Returns:
        Normalized text
    """
    if not text:
        return ""

    # Normalize Unicode and lowercase so bot/WebApp searches compare the same text.
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()

    # Remove punctuation (keep alphanumeric and spaces)
    text = re.sub(r'[^a-z0-9\s]', ' ', text)

    # Collapse whitespace
    text = ' '.join(text.split())

    return text


def expand_query_with_synonyms(query: str, include_seo_terms: bool = True) -> List[str]:
    """
    Expand query with synonyms/aliases.

    Args:
        query: Search query

    Returns:
        List of query variants (original + synonyms)
    """
    normalized = normalize_text(query)
    variants = [normalized]

    # Check for synonyms
    words = normalized.split()
    for word in words:
        if word in SEARCH_SYNONYMS:
            # Add variant with synonym
            synonym_query = normalized.replace(word, SEARCH_SYNONYMS[word])
            if synonym_query not in variants:
                variants.append(synonym_query)

    if include_seo_terms:
        try:
            from utils.seo_terms import build_query_variants
            for variant in build_query_variants(query, limit=8):
                normalized_variant = normalize_text(variant)
                if normalized_variant and normalized_variant not in variants:
                    variants.append(normalized_variant)
        except Exception:
            # Search must keep working even if the optional SEO dictionary is invalid.
            pass

    return variants


def fuzzy_search_products(
    products: List[Dict[str, Any]],
    query: str,
    score_cutoff: int = 75,
    limit: int = None,
    include_seo_terms: bool = True,
    rank_by_score: bool = False,
) -> List[Dict[str, Any]]:
    """
    Perform fuzzy search on products with two-stage ranking:
    1. Exact/substring matches (boosted)
    2. Fuzzy matches (fallback)

    Args:
        products: List of product dictionaries
        query: Search query string
        score_cutoff: Minimum similarity score (0-100)
        limit: Maximum number of results
        include_seo_terms: Whether to expand the query with broad SEO terms
        rank_by_score: When true, rank every candidate by closeness instead of
            preserving exact-match insertion order

    Returns:
        List of matching products ranked by relevance
    """
    if not products or not query:
        return []

    if limit is not None and limit <= 0:
        return []

    # Expand query with synonyms
    query_variants = expand_query_with_synonyms(query, include_seo_terms=include_seo_terms)
    primary_query = query_variants[0]

    if rank_by_score:
        return _rank_products_by_score(products, query_variants, score_cutoff, limit)

    # Stage 1: Exact and substring matches
    exact_matches = []
    exact_match_indices = set()

    for idx, product in enumerate(products):
        caption = normalize_text(product.get("caption", "") or "")
        category = normalize_text(product.get("category", "") or "")

        if not caption:
            continue

        # Check for exact substring match in caption or category
        for variant in query_variants:
            if variant in caption or variant in category:
                exact_matches.append((product, idx))
                exact_match_indices.add(idx)
                break

    # Stage 2: Fuzzy search on remaining products
    remaining_products = [
        (p, idx) for idx, p in enumerate(products)
        if idx not in exact_match_indices
    ]

    fuzzy_matches = []

    fuzzy_limit = len(remaining_products)
    if limit is not None:
        fuzzy_limit = max(limit - len(exact_matches), 0)

    if RAPIDFUZZ_AVAILABLE and fuzzy_limit > 0:
        # Use rapidfuzz for better performance
        remaining_captions = [
            normalize_text(p[0].get("caption", "") or "")
            for p in remaining_products
        ]

        results = process.extract(
            primary_query,
            remaining_captions,
            scorer=fuzz.partial_ratio,
            limit=fuzzy_limit,
            score_cutoff=score_cutoff
        )

        for caption, score, search_idx in results:
            original_product, original_idx = remaining_products[search_idx]
            fuzzy_matches.append(original_product)
    elif fuzzy_limit > 0:
        # Fallback to difflib
        for product, idx in remaining_products:
            caption = normalize_text(product.get("caption", "") or "")
            if not caption:
                continue

            # Calculate similarity using SequenceMatcher
            similarity = difflib.SequenceMatcher(None, primary_query, caption).ratio() * 100

            if similarity >= score_cutoff:
                fuzzy_matches.append(product)

        # Sort fuzzy matches by caption length (shorter is better)
        fuzzy_matches.sort(key=lambda p: len(p.get("caption", "") or ""))

    # Combine results: exact matches first, then fuzzy matches
    all_matches = [p for p, idx in exact_matches] + fuzzy_matches

    if limit:
        all_matches = all_matches[:limit]

    return all_matches


def _score_product(product: Dict[str, Any], query_variants: List[str]) -> float:
    caption = normalize_text(product.get("caption", "") or "")
    category = normalize_text(product.get("category", "") or "")
    subcategory = normalize_text(product.get("subcategory", "") or "")
    first_line = normalize_text((product.get("caption", "") or "").splitlines()[0] if product.get("caption") else "")
    searchable = " ".join(part for part in (caption, category, subcategory) if part)

    if not searchable:
        return 0.0

    best = 0.0
    for variant in query_variants:
        if not variant:
            continue

        if first_line == variant:
            best = max(best, 110.0)
        elif first_line.startswith(variant):
            best = max(best, 106.0)
        elif variant in caption:
            best = max(best, 102.0)
        elif variant in category or variant in subcategory:
            best = max(best, 92.0)

        if RAPIDFUZZ_AVAILABLE:
            best = max(
                best,
                float(fuzz.partial_ratio(variant, caption)),
                float(fuzz.partial_ratio(variant, searchable)) - 2.0,
                float(fuzz.token_set_ratio(variant, searchable)) - 4.0,
            )
        else:
            best = max(best, difflib.SequenceMatcher(None, variant, searchable).ratio() * 100)

    return best


def _rank_products_by_score(
    products: List[Dict[str, Any]],
    query_variants: List[str],
    score_cutoff: int,
    limit: int = None,
) -> List[Dict[str, Any]]:
    scored = []
    for idx, product in enumerate(products):
        score = _score_product(product, query_variants)
        if score >= score_cutoff:
            caption_len = len(product.get("caption", "") or "")
            scored.append((score, -caption_len, -idx, product))

    scored.sort(reverse=True)
    matches = [product for _, _, _, product in scored]
    return matches[:limit] if limit else matches

