"""Lightweight Google Trends signal collector.

Google Trends does not currently expose a broadly public, stable API for
general use in this project. This module therefore uses the public Trends
page payload as a narrow signal source and only returns a small, bounded set of
already-public trending queries.

The caller is still responsible for mapping those terms conservatively into
catalog taxonomy before they are promoted into SEO mappings.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_TRENDS_PAGE_URL = "https://trends.google.com/trending?geo={geo}&hl={hl}"
_TRENDS_USER_AGENT = "Mozilla/5.0 (compatible; tombrady420-seo-bot/1.0)"

_TREND_GEO_SPECS = (
    {"geo": "US", "hl": "en-US", "language": "en"},
    {"geo": "GB", "hl": "en-GB", "language": "en"},
    {"geo": "DE", "hl": "de-DE", "language": "de"},
    {"geo": "NL", "hl": "nl-NL", "language": "nl"},
    {"geo": "IT", "hl": "it-IT", "language": "it"},
    {"geo": "ES", "hl": "es-ES", "language": "es"},
    {"geo": "FR", "hl": "fr-FR", "language": "fr"},
)


def iter_trend_geo_specs() -> tuple[dict[str, str], ...]:
    return _TREND_GEO_SPECS


def _extract_init_data_payload(html: str) -> list[Any]:
    """Extract the ds:0 AF_initDataCallback payload from a Trends page HTML blob."""
    marker = "AF_initDataCallback({key: 'ds:0'"
    start = html.find(marker)
    if start < 0:
        return []
    data_start = html.find("data:", start)
    if data_start < 0:
        return []
    bracket_start = html.find("[", data_start)
    if bracket_start < 0:
        return []

    depth = 0
    in_string = False
    escape = False
    for idx in range(bracket_start, len(html)):
        ch = html[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                payload = html[bracket_start : idx + 1]
                return json.loads(payload)
    return []


def fetch_trending_queries(
    *,
    geo: str,
    hl: str,
    limit: int = 10,
    timeout_seconds: int = 20,
) -> list[dict[str, Any]]:
    """Return a small set of trending queries for one geo locale."""
    limit = max(1, min(limit, 25))
    url = _TRENDS_PAGE_URL.format(geo=geo, hl=hl)
    req = urllib.request.Request(url, headers={"User-Agent": _TRENDS_USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            html = response.read().decode("utf-8", "ignore")
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        logger.debug("[google_trends] fetch_failed geo=%s hl=%s error=%s", geo, hl, exc)
        return []

    payload = _extract_init_data_payload(html)
    if not payload or len(payload) < 2:
        return []

    items = payload[1]
    if not isinstance(items, list):
        return []

    results: list[dict[str, Any]] = []
    for rank, item in enumerate(items[:limit], start=1):
        if not isinstance(item, list) or not item:
            continue
        query = str(item[0] or "").strip()
        if not query:
            continue
        related_queries = []
        if len(item) > 9 and isinstance(item[9], list):
            related_queries = [str(value).strip() for value in item[9] if str(value).strip()]
        results.append(
            {
                "term": query,
                "term_type": "google_trends_trending",
                "provider": "google_trends",
                "source": f"{geo}:{hl}",
                "confidence": max(1, min(100, 100 - (rank - 1) * 4)),
                "evidence_count": max(1, 100 - (rank - 1) * 7),
                "approved": True,
                "notes": {
                    "geo": geo,
                    "hl": hl,
                    "rank": rank,
                    "related_queries": related_queries[:10],
                },
            }
        )
    return results


def fetch_trending_queries_for_supported_locales(
    *,
    limit_per_geo: int = 10,
    max_terms: int = 80,
) -> list[dict[str, Any]]:
    """Return deduplicated Google Trends queries across our supported locales."""
    max_terms = max(1, min(max_terms, 200))
    all_rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for spec in _TREND_GEO_SPECS:
        rows = fetch_trending_queries(
            geo=spec["geo"],
            hl=spec["hl"],
            limit=limit_per_geo,
        )
        for row in rows:
            key = re.sub(r"\s+", " ", str(row.get("term") or "").strip().lower())
            if not key or key in seen:
                continue
            seen.add(key)
            row["language"] = spec["language"]
            all_rows.append(row)
            if len(all_rows) >= max_terms:
                return all_rows
    return all_rows
