"""Build conservative, source-backed editorial guide drafts from catalog demand.

The worker deliberately does not scrape competitors or publish automatically.
It turns qualified first-party/search-console demand into a reviewable draft that
links terminology back to an existing catalog taxonomy.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from utils.seo_autolearn import has_search_audience_intent
from utils.seo_terms import normalize_term


CURATED_SOURCES: List[Dict[str, str]] = [
    {
        "url": "https://www.cdc.gov/marijuana",
        "title": "About Cannabis",
        "publisher": "Centers for Disease Control and Prevention",
    },
    {
        "url": "https://nida.nih.gov/research-topics/cannabis-marijuana",
        "title": "Cannabis (Marijuana) and Cannabinoids",
        "publisher": "National Institute on Drug Abuse",
    },
    {
        "url": "https://www.nccih.nih.gov/health/cannabis-marijuana-and-cannabinoids-what-you-need-to-know",
        "title": "Cannabis, Marijuana and Cannabinoids: What You Need To Know",
        "publisher": "National Center for Complementary and Integrative Health",
    },
    {
        "url": "https://www.fda.gov/consumers/consumer-updates/what-you-need-know-and-what-were-working-find-out-about-products-containing-cannabis-or-cannabis",
        "title": "What You Need to Know About Products Containing Cannabis",
        "publisher": "U.S. Food and Drug Administration",
    },
    {
        "url": "https://nida.nih.gov/research-topics/psilocybin-magic-mushrooms",
        "title": "Psilocybin (Magic Mushrooms)",
        "publisher": "National Institute on Drug Abuse",
    },
]


def sources_for_taxonomy(category: str, topic: str = "") -> List[Dict[str, str]]:
    mushroom_context = "shroom" in normalize_term(category) or any(
        token in normalize_term(topic) for token in ("mushroom", "shroom", "psilocybin", "microdose")
    )
    if mushroom_context:
        return [CURATED_SOURCES[-1]]
    return CURATED_SOURCES[:-1]


def enrich_seed_guide(page: Dict[str, Any]) -> Dict[str, Any]:
    """Give legacy glossary seeds useful editorial structure without changing their URLs."""
    existing = [str(value).strip() for value in page.get("sections") or [] if str(value).strip()]
    category = str(page.get("category") or "Catalog")
    subcategory = str(page.get("subcategory") or "")
    destination = _display(subcategory or category)
    title = str(page.get("title") or destination)
    sections: List[Dict[str, str]] = []
    headings = ["Overview", "Common terms", "Browse the live catalog"]
    for index, text in enumerate(existing[:3]):
        sections.append({"heading": headings[index], "text": text})
    sections.extend([
        {
            "heading": "How to compare related listings",
            "text": (
                "Names and community terms are useful starting points, but they do not guarantee that two listings are "
                "equivalent. Review the full description, product format, producer details, package information, and media. "
                f"The {destination} catalog page is updated as listings change, so it is the best source for current availability."
            ),
        },
        {
            "heading": "Responsible information",
            "text": (
                "Laws, age restrictions, labeling rules, and health guidance vary by location. Keep products away from "
                "children and pets, avoid driving while impaired, and ask a qualified professional about personal medical "
                "or legal questions. This guide explains catalog terminology and does not make medical claims."
            ),
        },
    ])
    return {
        **page,
        "title": title,
        "sections": sections,
        "author_name": page.get("author_name") or "Top Shelf Editorial Team",
        "quality_score": page.get("quality_score") or 80,
        "sources": page.get("sources") or sources_for_taxonomy(category, title),
    }


def _slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalize_term(value)).strip("-")[:96]


def _display(value: str) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    return value.title() if value else "Catalog"


def build_guide_draft(
    topic: str,
    *,
    category: str,
    subcategory: str = "",
    source: str = "first_party_demand",
    demand_score: float = 0,
    related_terms: Iterable[str] = (),
) -> Dict[str, Any] | None:
    """Create a reviewable educational draft for a qualified catalog topic."""
    topic = re.sub(r"\s+", " ", str(topic or "")).strip()[:180]
    if not topic or not category or not has_search_audience_intent(topic):
        return None

    category_label = _display(category)
    subcategory_label = _display(subcategory)
    destination = subcategory_label if subcategory else category_label
    terms = [topic]
    for value in related_terms:
        clean = re.sub(r"\s+", " ", str(value or "")).strip()
        if clean and normalize_term(clean) not in {normalize_term(item) for item in terms}:
            terms.append(clean[:120])
        if len(terms) >= 10:
            break

    title = f"{_display(topic)}: A Catalog Terminology Guide"
    sections = [
        {
            "heading": f"What does {topic} mean?",
            "text": (
                f"{_display(topic)} is a search phrase used around cannabis-related products and terminology. "
                f"Its exact meaning can vary by producer, format, region, and individual listing, so a product name alone "
                f"should not be treated as a guarantee of ingredients, strength, quality, or effects. This guide connects "
                f"the phrase to the most relevant part of this catalog: {destination}."
            ),
        },
        {
            "heading": "How to compare listings",
            "text": (
                "Read the complete listing rather than relying only on a familiar keyword. Compare the stated product "
                "format, producer information, package details, media, and description. Similar names can refer to "
                "different products, and availability can change as the catalog is updated."
            ),
        },
        {
            "heading": f"Where it appears in the {category_label} catalog",
            "text": (
                f"Current matches are organized under {destination}. The linked category is the authoritative place to "
                "check live availability and related products. Search variants such as "
                f"{', '.join(terms[:5])} may lead to the same catalog area when they describe the same intent."
            ),
        },
        {
            "heading": "Safety and responsible information",
            "text": (
                "Cannabis laws, age restrictions, labeling rules, and health guidance differ by location. Avoid driving "
                "or operating machinery while impaired, keep products away from children and pets, and consult a qualified "
                "health professional for personal medical questions. This page is educational catalog navigation, not "
                "medical or legal advice."
            ),
        },
        {
            "heading": "Sources and review",
            "text": (
                "The general safety context is supported by the public-health sources listed below. Product-specific facts "
                "come only from the live catalog listing. An administrator must review this draft before publication, and "
                "published guidance should be updated when terminology or the linked catalog changes."
            ),
        },
    ]
    sources = sources_for_taxonomy(category, topic)
    quality_score = min(92, 68 + len(sources) * 4 + min(8, int(float(demand_score or 0) // 5)))
    return {
        "slug": f"guide-{_slugify(topic)}",
        "title": title,
        "summary": (
            f"Understand the term {topic}, how to compare related listings, and where to browse matching "
            f"{destination} products in the current catalog."
        ),
        "sections": sections,
        "terms": terms,
        "category": str(category or "").strip().upper(),
        "subcategory": str(subcategory or "").strip().upper(),
        "author_name": "Top Shelf Editorial Team",
        "topic_source": source or "first_party_demand",
        "quality_score": quality_score,
        "sources": sources,
    }
