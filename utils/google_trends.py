"""Lightweight, bounded Google Trends signal collectors.

The seeded collector uses the same public JSON payloads as the Trends explore
page and rotates one catalog-relevant seed per locale each day.  The older
"Trending now" parser remains as a fallback because these public endpoints can
be rate-limited or changed independently of the application.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from typing import Any, Iterable

logger = logging.getLogger(__name__)

_TRENDS_PAGE_URL = "https://trends.google.com/trending?geo={geo}&hl={hl}"
_TRENDS_EXPLORE_URL = "https://trends.google.com/trends/api/explore"
_TRENDS_RELATED_URL = "https://trends.google.com/trends/api/widgetdata/relatedsearches"
_TRENDS_TIMELINE_URL = "https://trends.google.com/trends/api/widgetdata/multiline"
_TRENDS_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0 Safari/537.36"
)

_TREND_SEEDS = {
    "en": (
        "cannabis", "weed strains", "cannabis flower", "hash rosin",
        "live resin", "weed vapes", "cannabis edibles", "pre rolls",
        "magic mushrooms", "microdose mushrooms",
    ),
    "de": (
        "cannabis", "cannabis blüten", "gras sorten", "haschisch",
        "cannabis vape", "cannabis edibles", "magic mushrooms", "mikrodosierung pilze",
    ),
    "nl": (
        "cannabis", "wiet soorten", "wiet bloem", "hasj", "wiet vape",
        "cannabis edibles", "magic mushrooms", "microdosing paddo's",
    ),
    "fr": (
        "cannabis", "fleur de cannabis", "variétés cannabis", "résine cannabis",
        "vape cannabis", "comestibles cannabis", "champignons magiques", "microdose champignons",
    ),
    "it": (
        "cannabis", "fiore cannabis", "varietà cannabis", "hashish",
        "vape cannabis", "edibili cannabis", "funghi magici", "microdose funghi",
    ),
    "es": (
        "cannabis", "flor cannabis", "variedades marihuana", "hachís",
        "vape cannabis", "comestibles cannabis", "setas mágicas", "microdosis setas",
    ),
}

_TREND_GEO_SPECS = (
    {"geo": "US", "hl": "en-US", "language": "en"},
    {"geo": "CA", "hl": "en-CA", "language": "en"},
    {"geo": "CA", "hl": "fr-CA", "language": "fr"},
    {"geo": "GB", "hl": "en-GB", "language": "en"},
    {"geo": "DE", "hl": "de-DE", "language": "de"},
    {"geo": "NL", "hl": "nl-NL", "language": "nl"},
    {"geo": "BE", "hl": "nl-BE", "language": "nl"},
    {"geo": "BE", "hl": "fr-BE", "language": "fr"},
    {"geo": "IT", "hl": "it-IT", "language": "it"},
    {"geo": "ES", "hl": "es-ES", "language": "es"},
    {"geo": "FR", "hl": "fr-FR", "language": "fr"},
)


def iter_trend_geo_specs() -> tuple[dict[str, str], ...]:
    return _TREND_GEO_SPECS


def _clamp_score(value: Any, default: int = 0) -> int:
    try:
        return max(0, min(100, int(round(float(value)))))
    except (TypeError, ValueError):
        return default


def _strip_xssi_prefix(payload: str) -> str:
    payload = str(payload or "").lstrip()
    if payload.startswith(")]}'"):
        payload = payload[4:].lstrip("\r\n")
    return payload


def _fetch_json(
    url: str,
    params: dict[str, Any],
    *,
    hl: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    query = urllib.parse.urlencode(params)
    request = urllib.request.Request(
        f"{url}?{query}",
        headers={
            "User-Agent": _TRENDS_USER_AGENT,
            "Accept": "application/json,text/plain,*/*",
            "Accept-Language": f"{hl},en;q=0.8",
            "Referer": "https://trends.google.com/trends/explore",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            return json.loads(_strip_xssi_prefix(response.read().decode("utf-8", "ignore")))
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            logger.info("[google_trends] seeded_endpoint_rate_limited")
            return {"_rate_limited": True}
        logger.debug("[google_trends] json_fetch_failed url=%s status=%s", url, exc.code)
        return {}
    except (urllib.error.URLError, TimeoutError, ValueError, json.JSONDecodeError) as exc:
        logger.debug("[google_trends] json_fetch_failed url=%s error=%s", url, exc)
        return {}


def _explore_widgets(
    *,
    seed: str,
    geo: str,
    hl: str,
    timeout_seconds: int,
) -> list[dict[str, Any]] | None:
    request_payload = {
        "comparisonItem": [{"keyword": seed, "geo": geo, "time": "today 3-m"}],
        "category": 0,
        "property": "",
    }
    payload = _fetch_json(
        _TRENDS_EXPLORE_URL,
        {
            "hl": hl,
            "tz": "0",
            "req": json.dumps(request_payload, ensure_ascii=True, separators=(",", ":")),
        },
        hl=hl,
        timeout_seconds=timeout_seconds,
    )
    if payload.get("_rate_limited"):
        return None
    widgets = payload.get("widgets") or []
    return [widget for widget in widgets if isinstance(widget, dict)]


def _timeline_signal(
    widget: dict[str, Any],
    *,
    hl: str,
    timeout_seconds: int,
) -> tuple[int, int]:
    token = str(widget.get("token") or "")
    request_payload = widget.get("request") or {}
    if not token or not request_payload:
        return 0, 0
    payload = _fetch_json(
        _TRENDS_TIMELINE_URL,
        {
            "hl": hl,
            "tz": "0",
            "req": json.dumps(request_payload, ensure_ascii=True, separators=(",", ":")),
            "token": token,
        },
        hl=hl,
        timeout_seconds=timeout_seconds,
    )
    points = (payload.get("default") or {}).get("timelineData") or []
    values: list[float] = []
    for point in points:
        raw = (point or {}).get("value") or []
        try:
            values.append(float(raw[0]))
        except (IndexError, TypeError, ValueError):
            continue
    if not values:
        return 0, 0
    recent = values[-7:]
    previous = values[-28:-7] or values[:-7]
    recent_avg = sum(recent) / len(recent)
    previous_avg = (sum(previous) / len(previous)) if previous else recent_avg
    if previous_avg <= 0:
        momentum = 100 if recent_avg > 0 else 0
    else:
        momentum = max(-100.0, min(300.0, ((recent_avg - previous_avg) / previous_avg) * 100.0))
    return _clamp_score(recent_avg), int(round(momentum))


def _related_signals(
    widget: dict[str, Any],
    *,
    seed: str,
    geo: str,
    hl: str,
    language: str,
    limit: int,
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    token = str(widget.get("token") or "")
    request_payload = widget.get("request") or {}
    if not token or not request_payload:
        return []
    payload = _fetch_json(
        _TRENDS_RELATED_URL,
        {
            "hl": hl,
            "tz": "0",
            "req": json.dumps(request_payload, ensure_ascii=True, separators=(",", ":")),
            "token": token,
        },
        hl=hl,
        timeout_seconds=timeout_seconds,
    )
    ranked_lists = (payload.get("default") or {}).get("rankedList") or []
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for list_index, ranked in enumerate(ranked_lists[:2]):
        relation = "top" if list_index == 0 else "rising"
        for rank, item in enumerate((ranked or {}).get("rankedKeyword") or [], start=1):
            term = str((item or {}).get("query") or "").strip()
            key = re.sub(r"\s+", " ", term.lower())
            if not key or key in seen:
                continue
            seen.add(key)
            raw_value = (item or {}).get("value")
            if relation == "rising":
                try:
                    confidence = 65 + math.log10(max(1.0, float(raw_value))) * 8
                except (TypeError, ValueError):
                    confidence = 82
            else:
                confidence = 55 + _clamp_score(raw_value) * 0.4
            results.append({
                "term": term,
                "term_type": f"google_trends_seeded_{relation}",
                "provider": "google_trends",
                "source": f"{geo}:{hl}:seed={seed}",
                "language": language,
                "confidence": _clamp_score(confidence, 70),
                "evidence_count": max(1, _clamp_score(raw_value, 1)),
                "approved": False,
                "notes": {
                    "geo": geo,
                    "hl": hl,
                    "seed_term": seed,
                    "relation": relation,
                    "rank": rank,
                    "raw_value": raw_value,
                    "formatted_value": str((item or {}).get("formattedValue") or "")[:40],
                },
            })
            if len(results) >= limit:
                return results
    return results


def fetch_seeded_queries_for_supported_locales(
    *,
    limit_per_geo: int = 6,
    max_terms: int = 80,
    timeout_seconds: int = 12,
) -> list[dict[str, Any]]:
    """Collect a rotating daily sample of relevant regional related queries."""
    limit_per_geo = max(2, min(limit_per_geo, 12))
    max_terms = max(1, min(max_terms, 200))
    cycle_day = date.today().toordinal()
    results: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    for locale_index, spec in enumerate(_TREND_GEO_SPECS):
        language = spec["language"]
        seeds = _TREND_SEEDS.get(language) or _TREND_SEEDS["en"]
        seed = seeds[(cycle_day + locale_index) % len(seeds)]
        widgets = _explore_widgets(
            seed=seed,
            geo=spec["geo"],
            hl=spec["hl"],
            timeout_seconds=timeout_seconds,
        )
        if widgets is None:
            # One 429 is enough; the caller will use the low-cost fallback.
            break
        if not widgets:
            continue
        timeline_widget = next((item for item in widgets if item.get("id") == "TIMESERIES"), {})
        related_widget = next((item for item in widgets if item.get("id") == "RELATED_QUERIES"), {})
        interest, momentum = _timeline_signal(
            timeline_widget,
            hl=spec["hl"],
            timeout_seconds=timeout_seconds,
        )
        seed_confidence = _clamp_score(45 + interest * 0.35 + max(-20, momentum) * 0.15, 60)
        locale_rows = [{
            "term": seed,
            "term_type": "google_trends_seed",
            "provider": "google_trends",
            "source": f"{spec['geo']}:{spec['hl']}:seed={seed}",
            "language": language,
            "confidence": seed_confidence,
            "evidence_count": max(1, interest),
            "approved": False,
            "notes": {
                "geo": spec["geo"],
                "hl": spec["hl"],
                "seed_term": seed,
                "interest_score": interest,
                "momentum_percent": momentum,
                "cycle_day": cycle_day,
            },
        }]
        locale_rows.extend(_related_signals(
            related_widget,
            seed=seed,
            geo=spec["geo"],
            hl=spec["hl"],
            language=language,
            limit=max(1, limit_per_geo - 1),
            timeout_seconds=timeout_seconds,
        ))
        for row in locale_rows:
            key = (spec["geo"], language, re.sub(r"\s+", " ", str(row.get("term") or "").lower()))
            if not key[2] or key in seen:
                continue
            seen.add(key)
            results.append(row)
            if len(results) >= max_terms:
                return results
        # A small pause avoids turning the daily intelligence job into a burst.
        time.sleep(0.15)
    return results


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
    locale_count = len(_TREND_GEO_SPECS)
    base_quota, remainder = divmod(max_terms, locale_count)
    for locale_index, spec in enumerate(_TREND_GEO_SPECS):
        locale_quota = min(limit_per_geo, base_quota + (1 if locale_index < remainder else 0))
        if locale_quota <= 0:
            continue
        rows = fetch_trending_queries(
            geo=spec["geo"],
            hl=spec["hl"],
            limit=locale_quota,
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
