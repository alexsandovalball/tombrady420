"""Optional Google Search Console import helpers.

This module intentionally uses only the official Search Console API. It does
not scrape Google result pages, and it is inactive unless the deployment
provides explicit Search Console credentials.
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

SEARCH_CONSOLE_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"


def _load_json_secret(
    json_value: Optional[str],
    file_value: Optional[str],
    *,
    label: str,
) -> Optional[dict[str, Any]]:
    if json_value:
        try:
            return json.loads(json_value)
        except json.JSONDecodeError as exc:
            logger.warning("[search_console] invalid_%s_json error=%s", label, exc)
            return None

    if file_value:
        path = Path(file_value)
        if not path.exists():
            logger.debug("[search_console] %s_file_missing path=%s", label, file_value)
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("[search_console] %s_file_unreadable path=%s error=%s", label, file_value, exc)
            return None

    return None


def _load_service_account_info(
    service_account_json: Optional[str],
    service_account_file: Optional[str],
) -> Optional[dict[str, Any]]:
    return _load_json_secret(service_account_json, service_account_file, label="service_account")


def _oauth_section(payload: dict[str, Any]) -> dict[str, Any]:
    if "installed" in payload and isinstance(payload["installed"], dict):
        return payload["installed"]
    if "web" in payload and isinstance(payload["web"], dict):
        return payload["web"]
    return payload


def is_search_console_configured(
    *,
    enabled: bool,
    site_url: Optional[str],
    service_account_json: Optional[str],
    service_account_file: Optional[str],
    oauth_client_json: Optional[str] = None,
    oauth_client_file: Optional[str] = None,
    oauth_token_json: Optional[str] = None,
    oauth_token_file: Optional[str] = None,
) -> bool:
    has_service_account = service_account_json or service_account_file
    has_oauth = (oauth_client_json or oauth_client_file) and (oauth_token_json or oauth_token_file)
    return bool(enabled and site_url and (has_service_account or has_oauth))


def describe_search_console_setup(
    *,
    enabled: bool,
    site_url: Optional[str],
    service_account_json: Optional[str],
    service_account_file: Optional[str],
    oauth_client_json: Optional[str] = None,
    oauth_client_file: Optional[str] = None,
    oauth_token_json: Optional[str] = None,
    oauth_token_file: Optional[str] = None,
) -> dict[str, Any]:
    """Return non-secret Search Console setup details for logs/status checks."""
    service_account_file_exists = bool(service_account_file and Path(service_account_file).exists())
    oauth_client_file_exists = bool(oauth_client_file and Path(oauth_client_file).exists())
    oauth_token_file_exists = bool(oauth_token_file and Path(oauth_token_file).exists())
    has_service_account = bool(service_account_json or service_account_file_exists)
    has_oauth = bool((oauth_client_json or oauth_client_file_exists) and (oauth_token_json or oauth_token_file_exists))
    configured = bool(enabled and site_url and (has_service_account or has_oauth))
    if has_oauth:
        auth_source = "oauth"
    elif has_service_account:
        auth_source = "service_account"
    else:
        auth_source = "none"
    return {
        "enabled": bool(enabled),
        "site_url": site_url,
        "configured": configured,
        "auth_source": auth_source,
        "has_service_account": has_service_account,
        "has_oauth": has_oauth,
        "service_account_file_exists": service_account_file_exists,
        "oauth_client_file_exists": oauth_client_file_exists,
        "oauth_token_file_exists": oauth_token_file_exists,
    }


def _load_oauth_credentials(
    *,
    oauth_client_json: Optional[str],
    oauth_client_file: Optional[str],
    oauth_token_json: Optional[str],
    oauth_token_file: Optional[str],
):
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request as GoogleAuthRequest
    except ImportError as exc:  # pragma: no cover - depends on Render install state
        logger.warning("[search_console] dependency_missing error=%s", exc)
        return None

    client_payload = _load_json_secret(oauth_client_json, oauth_client_file, label="oauth_client")
    token_payload = _load_json_secret(oauth_token_json, oauth_token_file, label="oauth_token")
    if not client_payload or not token_payload:
        return None

    client_info = _oauth_section(client_payload)
    refresh_token = token_payload.get("refresh_token")
    if not refresh_token:
        logger.warning("[search_console] oauth_token_missing_refresh_token")
        return None

    credentials = Credentials(
        token=token_payload.get("access_token"),
        refresh_token=refresh_token,
        token_uri=token_payload.get("token_uri") or client_info.get("token_uri") or "https://oauth2.googleapis.com/token",
        client_id=token_payload.get("client_id") or client_info.get("client_id"),
        client_secret=token_payload.get("client_secret") or client_info.get("client_secret"),
        scopes=[SEARCH_CONSOLE_SCOPE],
    )

    try:
        if not credentials.valid:
            credentials.refresh(GoogleAuthRequest())
    except Exception as exc:
        logger.warning("[search_console] oauth_refresh_failed error=%s", exc)
        return None

    return credentials


def _load_service_account_credentials(
    *,
    service_account_json: Optional[str],
    service_account_file: Optional[str],
):
    try:
        from google.auth.transport.requests import Request as GoogleAuthRequest
        from google.oauth2 import service_account
    except ImportError as exc:  # pragma: no cover - depends on Render install state
        logger.warning("[search_console] dependency_missing error=%s", exc)
        return None

    info = _load_service_account_info(service_account_json, service_account_file)
    if not info:
        return None

    try:
        credentials = service_account.Credentials.from_service_account_info(
            info,
            scopes=[SEARCH_CONSOLE_SCOPE],
        )
        credentials.refresh(GoogleAuthRequest())
    except Exception as exc:
        logger.warning("[search_console] service_account_auth_failed error=%s", exc)
        return None

    return credentials


def _search_console_site_candidates(site_url: str) -> list[str]:
    """Return the configured property plus a matching domain property fallback."""
    normalized = str(site_url or "").strip()
    if not normalized:
        return []

    candidates = [normalized]
    if not normalized.startswith("sc-domain:"):
        parsed = urllib.parse.urlparse(normalized)
        host = parsed.netloc or parsed.path
        host = host.split("@")[-1].split(":")[0].strip().lower()
        if host.startswith("www."):
            host = host[4:]
        if host:
            domain_property = f"sc-domain:{host}"
            if domain_property not in candidates:
                candidates.append(domain_property)
    return candidates


def _fetch_search_console_rows_sync(
    *,
    site_url: str,
    service_account_json: Optional[str],
    service_account_file: Optional[str],
    oauth_client_json: Optional[str],
    oauth_client_file: Optional[str],
    oauth_token_json: Optional[str],
    oauth_token_file: Optional[str],
    lookback_days: int,
    row_limit: int,
) -> list[dict[str, Any]]:
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - depends on Render install state
        logger.warning("[search_console] dependency_missing error=%s", exc)
        return []

    credentials = _load_oauth_credentials(
        oauth_client_json=oauth_client_json,
        oauth_client_file=oauth_client_file,
        oauth_token_json=oauth_token_json,
        oauth_token_file=oauth_token_file,
    )
    auth_source = "oauth"
    if credentials is None:
        credentials = _load_service_account_credentials(
            service_account_json=service_account_json,
            service_account_file=service_account_file,
        )
        auth_source = "service_account"
    if credentials is None:
        return []

    end_date = (datetime.utcnow().date() - timedelta(days=1))
    start_date = end_date - timedelta(days=max(0, lookback_days - 1))
    body = {
        "startDate": start_date.isoformat(),
        "endDate": end_date.isoformat(),
        "dimensions": ["date", "query", "page", "country", "device"],
        "rowLimit": max(10, min(2500, int(row_limit or 250))),
    }

    last_rows: list[dict[str, Any]] = []
    for candidate_site_url in _search_console_site_candidates(site_url):
        encoded_site = urllib.parse.quote(candidate_site_url, safe="")
        endpoint = f"https://searchconsole.googleapis.com/webmasters/v3/sites/{encoded_site}/searchAnalytics/query"

        try:
            response = requests.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {credentials.token}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=20,
            )
        except requests.RequestException as exc:
            logger.warning("[search_console] request_failed site_url=%s error=%s", candidate_site_url, exc)
            continue

        if response.status_code >= 400:
            logger.warning(
                "[search_console] api_error auth=%s site_url=%s status=%s body=%s",
                auth_source,
                candidate_site_url,
                response.status_code,
                response.text[:500],
            )
            continue

        payload = response.json()
        rows: list[dict[str, Any]] = []
        for item in payload.get("rows") or []:
            keys = item.get("keys") or []
            if len(keys) < 5:
                continue
            rows.append({
                "date": keys[0],
                "query": keys[1],
                "page": keys[2],
                "country": keys[3],
                "device": keys[4],
                "clicks": int(item.get("clicks") or 0),
                "impressions": int(item.get("impressions") or 0),
                "ctr": float(item.get("ctr") or 0),
                "position": float(item.get("position") or 0),
                "property_url": candidate_site_url,
            })
        if rows:
            if candidate_site_url != site_url:
                logger.info("[search_console] fallback_property_used site_url=%s rows=%d", candidate_site_url, len(rows))
            return rows
        last_rows = rows

    return last_rows


async def fetch_search_console_rows(
    *,
    enabled: bool,
    site_url: Optional[str],
    service_account_json: Optional[str],
    service_account_file: Optional[str],
    oauth_client_json: Optional[str] = None,
    oauth_client_file: Optional[str] = None,
    oauth_token_json: Optional[str] = None,
    oauth_token_file: Optional[str] = None,
    lookback_days: int = 7,
    row_limit: int = 250,
) -> list[dict[str, Any]]:
    """Fetch Search Console rows, returning [] when disabled or unavailable."""
    if not is_search_console_configured(
        enabled=enabled,
        site_url=site_url,
        service_account_json=service_account_json,
        service_account_file=service_account_file,
        oauth_client_json=oauth_client_json,
        oauth_client_file=oauth_client_file,
        oauth_token_json=oauth_token_json,
        oauth_token_file=oauth_token_file,
    ):
        return []

    return await asyncio.to_thread(
        _fetch_search_console_rows_sync,
        site_url=str(site_url),
        service_account_json=service_account_json,
        service_account_file=service_account_file,
        oauth_client_json=oauth_client_json,
        oauth_client_file=oauth_client_file,
        oauth_token_json=oauth_token_json,
        oauth_token_file=oauth_token_file,
        lookback_days=lookback_days,
        row_limit=row_limit,
    )
