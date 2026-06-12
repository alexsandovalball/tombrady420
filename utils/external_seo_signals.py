"""Load optional external SEO signal exports.

The expected JSON can be either a list or an object with a ``signals`` key:

[
  {"term": "solventless hash", "category": "CONCENTRATES",
   "subcategory": "SOLVENTLESS", "provider": "weedmaps_api"}
]

These files should contain short taxonomy terms from authorized sources or
operator-approved exports, not copied third-party page descriptions.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)


def _iter_raw_items(payload: Any) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            if isinstance(item, dict):
                yield item
        return

    if isinstance(payload, dict):
        for key in ("signals", "terms", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        yield item
                return


def load_external_seo_signal_file(path_value: Optional[str]) -> list[dict[str, Any]]:
    if not path_value:
        return []

    path = Path(path_value)
    if not path.exists():
        logger.debug("[external_seo] signal_file_missing path=%s", path_value)
        return []

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[external_seo] signal_file_unreadable path=%s error=%s", path_value, exc)
        return []

    signals = []
    for item in _iter_raw_items(payload):
        term = str(item.get("term") or item.get("query") or item.get("name") or "").strip()
        if not term:
            continue
        signals.append({
            "term": term,
            "term_type": str(item.get("term_type") or item.get("type") or "taxonomy")[:40],
            "provider": str(item.get("provider") or item.get("source") or "external_file")[:80],
            "source": str(item.get("source") or path.name)[:120],
            "category": str(item.get("category") or "")[:80],
            "subcategory": str(item.get("subcategory") or "")[:120],
            "confidence": int(item.get("confidence") or 0),
            "approved": bool(item.get("approved", False)),
            "evidence_count": max(1, int(item.get("evidence_count") or item.get("count") or 1)),
            "notes": item.get("notes") or {},
        })
    return signals
