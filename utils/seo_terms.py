"""SEO/search term intelligence helpers.

The file-backed dictionary is intentionally explicit and approved. It expands
searches and page copy without creating doorway pages or hidden keyword dumps.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Optional

from utils.categories import get_category_display_name, get_subcategory_display_name

SEO_TERMS_PATH = Path(__file__).resolve().parent.parent / "configs" / "seo_terms.json"


def normalize_term(text: str) -> str:
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode("ascii").lower()
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return " ".join(text.split())


def _dedupe(items: Iterable[str], *, limit: Optional[int] = None) -> list[str]:
    seen = set()
    result = []
    for item in items:
        clean = " ".join(str(item or "").split())
        key = normalize_term(clean)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(clean)
        if limit and len(result) >= limit:
            break
    return result


@lru_cache(maxsize=1)
def load_seo_terms() -> dict[str, Any]:
    if not SEO_TERMS_PATH.exists():
        return {"version": 1, "mappings": []}
    with SEO_TERMS_PATH.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("mappings", [])
    return data


def clear_seo_terms_cache() -> None:
    load_seo_terms.cache_clear()


def _iter_file_mappings() -> Iterable[dict[str, Any]]:
    for mapping in load_seo_terms().get("mappings", []):
        localized_terms = mapping.get("localized_terms") or {}
        terms = _dedupe([
            *mapping.get("terms", []),
            *(
                term
                for values in localized_terms.values()
                if isinstance(values, list)
                for term in values
            ),
        ])
        if not terms:
            continue
        yield {
            "term": terms[0],
            "terms": terms,
            "category": mapping.get("category") or "",
            "subcategory": mapping.get("subcategory") or "",
            "source": "file",
        }


def _iter_db_mappings(db_mappings: Optional[Iterable[dict[str, Any]]]) -> Iterable[dict[str, Any]]:
    for row in db_mappings or []:
        term = row.get("term") or row.get("query") or ""
        if not term:
            continue
        yield {
            "term": term,
            "terms": [term],
            "category": row.get("category") or "",
            "subcategory": row.get("subcategory") or "",
            "source": row.get("source") or "db",
        }


def iter_mappings(db_mappings: Optional[Iterable[dict[str, Any]]] = None) -> Iterable[dict[str, Any]]:
    yield from _iter_file_mappings()
    yield from _iter_db_mappings(db_mappings)


def _mapping_score(query_norm: str, mapping: dict[str, Any]) -> int:
    if not query_norm:
        return 0
    query_tokens = set(query_norm.split())
    best = 0
    for term in mapping.get("terms", []):
        term_norm = normalize_term(term)
        if not term_norm:
            continue
        if query_norm == term_norm:
            best = max(best, 100)
        elif query_norm in term_norm or term_norm in query_norm:
            best = max(best, 90)
        else:
            term_tokens = set(term_norm.split())
            overlap = len(query_tokens & term_tokens)
            if overlap:
                best = max(best, int(55 + 35 * (overlap / max(len(query_tokens), len(term_tokens)))))
    return best


def match_query_mappings(
    query: str,
    *,
    db_mappings: Optional[Iterable[dict[str, Any]]] = None,
    min_score: int = 70,
    limit: int = 5,
) -> list[dict[str, Any]]:
    query_norm = normalize_term(query)
    scored = []
    seen = set()
    for mapping in iter_mappings(db_mappings):
        score = _mapping_score(query_norm, mapping)
        if score < min_score:
            continue
        key = (
            normalize_term(mapping.get("category", "")),
            normalize_term(mapping.get("subcategory", "")),
            normalize_term(mapping.get("term", "")),
        )
        if key in seen:
            continue
        seen.add(key)
        item = dict(mapping)
        item["score"] = score
        scored.append(item)
    scored.sort(key=lambda item: item.get("score", 0), reverse=True)
    return scored[:limit]


def build_query_variants(
    query: str,
    *,
    db_mappings: Optional[Iterable[dict[str, Any]]] = None,
    limit: int = 10,
) -> list[str]:
    variants = [query]
    for mapping in match_query_mappings(query, db_mappings=db_mappings, limit=5):
        variants.extend(mapping.get("terms", [])[:4])
        variants.append(mapping.get("category", ""))
        variants.append(mapping.get("subcategory", ""))
        if mapping.get("category"):
            variants.append(get_category_display_name(mapping["category"]))
        if mapping.get("subcategory"):
            variants.append(get_subcategory_display_name(mapping["subcategory"]))
    return _dedupe(variants, limit=limit)


def get_terms_for_taxonomy(
    category: str,
    subcategory: str = "",
    *,
    language: str = "en",
    limit: int = 18,
) -> list[str]:
    category_norm = normalize_term(category)
    subcategory_norm = normalize_term(subcategory)
    language = str(language or "en").split("-", 1)[0].lower()
    terms = []
    for mapping in load_seo_terms().get("mappings", []):
        if normalize_term(mapping.get("category", "")) != category_norm:
            continue
        if subcategory_norm and normalize_term(mapping.get("subcategory", "")) != subcategory_norm:
            continue
        localized = (mapping.get("localized_terms") or {}).get(language, [])
        if language != "en" and isinstance(localized, list):
            terms.extend(localized)
        elif language == "en":
            terms.extend(mapping.get("terms", []))
    return _dedupe(terms, limit=limit)


def build_taxonomy_intro(category: str, subcategory: str = "", *, total: int = 0) -> str:
    terms = get_terms_for_taxonomy(category, subcategory, limit=6)
    display_category = get_category_display_name(category) if category else "products"
    display_subcategory = get_subcategory_display_name(subcategory) if subcategory else ""
    label = f"{display_subcategory} {display_category}".strip() if display_subcategory else display_category
    if terms:
        return (
            f"Browse {total} {label} listing{'s' if total != 1 else ''}, including searches related to "
            f"{', '.join(terms[:5])}."
        )
    return f"Browse {total} {label} listing{'s' if total != 1 else ''}."
