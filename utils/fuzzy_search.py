"""
Fuzzy search utilities for product search.
"""
from typing import List, Dict, Any
try:
    from rapidfuzz import process, fuzz
    RAPIDFUZZ_AVAILABLE = True
except ImportError:
    RAPIDFUZZ_AVAILABLE = False
    import difflib


def fuzzy_search_products(
    products: List[Dict[str, Any]],
    query: str,
    score_cutoff: int = 75,
    limit: int = None
) -> List[Dict[str, Any]]:
    """
    Perform fuzzy search on products.
    
    Args:
        products: List of product dictionaries
        query: Search query string
        score_cutoff: Minimum similarity score (0-100)
        limit: Maximum number of results
    
    Returns:
        List of matching products with similarity scores
    """
    if not products or not query:
        return []
    
    query_lower = query.lower().strip()
    
    if RAPIDFUZZ_AVAILABLE:
        # Use rapidfuzz for better performance
        results = process.extract(
            query_lower,
            [p.get("caption", "") or "" for p in products],
            scorer=fuzz.partial_ratio,
            limit=limit or len(products),
            score_cutoff=score_cutoff
        )
        
        # Map results back to products
        matched_products = []
        seen_indices = set()
        for caption, score, index in results:
            if index not in seen_indices:
                matched_products.append(products[index])
                seen_indices.add(index)
        
        return matched_products
    else:
        # Fallback to difflib
        matched_products = []
        for product in products:
            caption = (product.get("caption", "") or "").lower()
            if not caption:
                continue
            
            # Calculate similarity using SequenceMatcher
            similarity = difflib.SequenceMatcher(None, query_lower, caption).ratio() * 100
            
            # Also check for partial matches
            if query_lower in caption or any(word in caption for word in query_lower.split()):
                similarity = max(similarity, 80)
            
            if similarity >= score_cutoff:
                matched_products.append(product)
        
        # Sort by relevance (simple heuristic: shorter captions with query words first)
        matched_products.sort(
            key=lambda p: (
                query_lower not in (p.get("caption", "") or "").lower(),
                len(p.get("caption", "") or "")
            )
        )
        
        if limit:
            matched_products = matched_products[:limit]
        
        return matched_products

