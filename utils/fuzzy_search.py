"""
Fuzzy search utilities for product search.
"""
import re
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

    # Lowercase
    text = text.lower()

    # Remove punctuation (keep alphanumeric and spaces)
    text = re.sub(r'[^a-z0-9\s]', ' ', text)

    # Collapse whitespace
    text = ' '.join(text.split())

    return text


def expand_query_with_synonyms(query: str) -> List[str]:
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

    return variants


def fuzzy_search_products(
    products: List[Dict[str, Any]],
    query: str,
    score_cutoff: int = 75,
    limit: int = None
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

    Returns:
        List of matching products ranked by relevance
    """
    if not products or not query:
        return []

    # Expand query with synonyms
    query_variants = expand_query_with_synonyms(query)
    primary_query = query_variants[0]

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

    if RAPIDFUZZ_AVAILABLE:
        # Use rapidfuzz for better performance
        remaining_captions = [
            normalize_text(p[0].get("caption", "") or "")
            for p in remaining_products
        ]

        results = process.extract(
            primary_query,
            remaining_captions,
            scorer=fuzz.partial_ratio,
            limit=len(remaining_captions),
            score_cutoff=score_cutoff
        )

        for caption, score, search_idx in results:
            original_product, original_idx = remaining_products[search_idx]
            fuzzy_matches.append(original_product)
    else:
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

