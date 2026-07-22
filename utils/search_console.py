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

SEARCH_CONSOLE_READ_SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
SEARCH_CONSOLE_WRITE_SCOPE = "https://www.googleapis.com/auth/webmasters"
SEARCH_CONSOLE_SCOPE = SEARCH_CONSOLE_READ_SCOPE


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
    if has_service_account:
        auth_source = "service_account"
    elif has_oauth:
        auth_source = "oauth"
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
    scopes: Optional[list[str]] = None,
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
        scopes=scopes or [SEARCH_CONSOLE_READ_SCOPE],
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
    scopes: Optional[list[str]] = None,
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
            scopes=scopes or [SEARCH_CONSOLE_READ_SCOPE],
        )
        credentials.refresh(GoogleAuthRequest())
    except Exception as exc:
        logger.warning("[search_console] service_account_auth_failed error=%s", exc)
        return None

    return credentials


def _load_search_console_credentials(
    *,
    service_account_json: Optional[str],
    service_account_file: Optional[str],
    oauth_client_json: Optional[str],
    oauth_client_file: Optional[str],
    oauth_token_json: Optional[str],
    oauth_token_file: Optional[str],
    scopes: list[str],
):
    """Load Search Console credentials, preferring service account over OAuth."""
    credentials = _load_service_account_credentials(
        service_account_json=service_account_json,
        service_account_file=service_account_file,
        scopes=scopes,
    )
    auth_source = "service_account"
    if credentials is None:
        credentials = _load_oauth_credentials(
            oauth_client_json=oauth_client_json,
            oauth_client_file=oauth_client_file,
            oauth_token_json=oauth_token_json,
            oauth_token_file=oauth_token_file,
            scopes=scopes,
        )
        auth_source = "oauth"
    return credentials, auth_source


def _search_console_site_candidates(site_url: str) -> list[str]:
    """Return domain property first, with URL-prefix property as a fallback."""
    normalized = str(site_url or "").strip()
    if not normalized:
        return []

    candidates: list[str] = []
    if not normalized.startswith("sc-domain:"):
        parsed = urllib.parse.urlparse(normalized)
        host = parsed.netloc or parsed.path
        host = host.split("@")[-1].split(":")[0].strip().lower()
        if host.startswith("www."):
            host = host[4:]
        if host:
            domain_property = f"sc-domain:{host}"
            candidates.append(domain_property)
    if normalized not in candidates:
        candidates.append(normalized)
    return candidates


def _query_search_analytics(
    requests_module,
    *,
    endpoint: str,
    token: str,
    start_date: str,
    end_date: str,
    dimensions: list[str],
    row_limit: int,
    search_type: str = "web",
) -> dict[str, Any]:
    """Fetch a bounded, paginated Search Analytics view."""
    rows: list[dict[str, Any]] = []
    requests_made = 0
    start_row = 0
    page_size = min(1000, row_limit)

    while len(rows) < row_limit:
        batch_limit = min(page_size, row_limit - len(rows))
        body = {
            "startDate": start_date,
            "endDate": end_date,
            "dimensions": dimensions,
            "type": search_type,
            "rowLimit": batch_limit,
            "startRow": start_row,
            "dataState": "final",
        }
        try:
            response = requests_module.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=20,
            )
        except requests_module.RequestException as exc:
            return {
                "rows": rows,
                "requests": requests_made,
                "error": str(exc),
                "http_status": None,
            }

        requests_made += 1
        if response.status_code >= 400:
            return {
                "rows": rows,
                "requests": requests_made,
                "error": response.text[:500] or f"HTTP {response.status_code}",
                "http_status": int(response.status_code or 0),
            }

        try:
            payload = response.json()
        except (TypeError, ValueError) as exc:
            return {
                "rows": rows,
                "requests": requests_made,
                "error": f"invalid JSON response: {exc}",
                "http_status": int(response.status_code or 0),
            }
        if not isinstance(payload, dict):
            return {
                "rows": rows,
                "requests": requests_made,
                "error": "invalid JSON response: expected an object",
                "http_status": int(response.status_code or 0),
            }
        batch = payload.get("rows") or []
        for item in batch:
            keys = item.get("keys") or []
            if len(keys) != len(dimensions):
                continue
            keyed = dict(zip(dimensions, keys))
            rows.append({
                "date": str(keyed.get("date") or ""),
                "query": str(keyed.get("query") or ""),
                "page": str(keyed.get("page") or ""),
                "country": str(keyed.get("country") or ""),
                "device": str(keyed.get("device") or ""),
                "search_type": search_type,
                "clicks": int(item.get("clicks") or 0),
                "impressions": int(item.get("impressions") or 0),
                "ctr": float(item.get("ctr") or 0),
                "position": float(item.get("position") or 0),
            })
        if len(batch) < batch_limit:
            break
        start_row += len(batch)

    return {"rows": rows, "requests": requests_made, "error": "", "http_status": 200}


def _fetch_search_console_data_sync(
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
) -> dict[str, Any]:
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - depends on Render install state
        logger.warning("[search_console] dependency_missing error=%s", exc)
        return {"status": "dependency_error", "rows": [], "media_rows": [], "error": str(exc)}

    credentials, auth_source = _load_search_console_credentials(
        service_account_json=service_account_json,
        service_account_file=service_account_file,
        oauth_client_json=oauth_client_json,
        oauth_client_file=oauth_client_file,
        oauth_token_json=oauth_token_json,
        oauth_token_file=oauth_token_file,
        scopes=[SEARCH_CONSOLE_READ_SCOPE],
    )
    if credentials is None:
        return {
            "status": "auth_error",
            "rows": [],
            "media_rows": [],
            "auth_source": auth_source,
            "error": "credentials unavailable or refresh failed",
        }

    end_date = (datetime.utcnow().date() - timedelta(days=1))
    start_date = end_date - timedelta(days=max(0, lookback_days - 1))
    total_limit = max(10, min(10000, int(row_limit or 2500)))
    last_error = ""
    last_http_status = None
    successful_empty_property = ""
    total_requests = 0
    selected_property = ""
    selected_rows: list[dict[str, Any]] = []
    for candidate_site_url in _search_console_site_candidates(site_url):
        encoded_site = urllib.parse.quote(candidate_site_url, safe="")
        endpoint = f"https://searchconsole.googleapis.com/webmasters/v3/sites/{encoded_site}/searchAnalytics/query"
        result = _query_search_analytics(
            requests,
            endpoint=endpoint,
            token=credentials.token,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            dimensions=["date", "query", "page"],
            row_limit=total_limit,
            search_type="web",
        )
        total_requests += int(result.get("requests") or 0)
        if result.get("error"):
            last_error = str(result.get("error") or "")
            last_http_status = result.get("http_status")
            logger.warning(
                "[search_console] api_error auth=%s site_url=%s status=%s error=%s",
                auth_source,
                candidate_site_url,
                last_http_status,
                last_error,
            )
            continue
        rows = result.get("rows") or []
        if rows:
            selected_property = candidate_site_url
            selected_rows = rows
            if candidate_site_url != site_url:
                logger.info(
                    "[search_console] alternate_property_used configured_site_url=%s site_url=%s rows=%d",
                    site_url,
                    candidate_site_url,
                    len(rows),
                )
            break
        if not successful_empty_property:
            successful_empty_property = candidate_site_url

    if not selected_property:
        selected_property = successful_empty_property
    if not selected_property:
        return {
            "status": "api_error",
            "rows": [],
            "media_rows": [],
            "auth_source": auth_source,
            "property_url": "",
            "requests": total_requests,
            "http_status": last_http_status,
            "error": last_error or "all Search Console properties failed",
        }

    for row in selected_rows:
        row["property_url"] = selected_property

    media_rows: list[dict[str, Any]] = []
    encoded_site = urllib.parse.quote(selected_property, safe="")
    endpoint = f"https://searchconsole.googleapis.com/webmasters/v3/sites/{encoded_site}/searchAnalytics/query"
    media_limit = min(500, total_limit)
    media_errors: list[str] = []
    for search_type in ("image", "video"):
        result = _query_search_analytics(
            requests,
            endpoint=endpoint,
            token=credentials.token,
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            dimensions=["date", "query", "page"],
            row_limit=media_limit,
            search_type=search_type,
        )
        total_requests += int(result.get("requests") or 0)
        if result.get("error"):
            media_errors.append(f"{search_type}: {result.get('error')}")
            continue
        for row in result.get("rows") or []:
            row["property_url"] = selected_property
            media_rows.append(row)

    return {
        "status": "ok" if selected_rows else "ok_empty",
        "rows": selected_rows,
        "media_rows": media_rows,
        "auth_source": auth_source,
        "property_url": selected_property,
        "requests": total_requests,
        "http_status": 200,
        "partial": bool(media_errors),
        "error": "; ".join(media_errors),
    }


async def fetch_search_console_data(
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
    row_limit: int = 2500,
) -> dict[str, Any]:
    """Fetch learning and media views with structured connection health."""
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
        return {"status": "not_configured", "rows": [], "media_rows": [], "error": "not configured"}

    return await asyncio.to_thread(
        _fetch_search_console_data_sync,
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


async def fetch_search_console_rows(**kwargs) -> list[dict[str, Any]]:
    """Backward-compatible list-only wrapper."""
    return list((await fetch_search_console_data(**kwargs)).get("rows") or [])


def submit_sitemap_sync(
    *,
    site_url: str,
    sitemap_url: str,
    service_account_json: Optional[str],
    service_account_file: Optional[str],
    oauth_client_json: Optional[str] = None,
    oauth_client_file: Optional[str] = None,
    oauth_token_json: Optional[str] = None,
    oauth_token_file: Optional[str] = None,
) -> dict[str, Any]:
    """Submit a sitemap URL to Search Console using the official Sitemaps API."""
    try:
        import requests
    except ImportError as exc:  # pragma: no cover - depends on Render install state
        logger.warning("[search_console] dependency_missing error=%s", exc)
        return {"ok": False, "error": f"dependency missing: {exc}", "auth_source": "none"}

    credentials, auth_source = _load_search_console_credentials(
        service_account_json=service_account_json,
        service_account_file=service_account_file,
        oauth_client_json=oauth_client_json,
        oauth_client_file=oauth_client_file,
        oauth_token_json=oauth_token_json,
        oauth_token_file=oauth_token_file,
        scopes=[SEARCH_CONSOLE_WRITE_SCOPE],
    )
    if credentials is None:
        return {"ok": False, "error": "credentials unavailable", "auth_source": "none"}

    clean_sitemap_url = str(sitemap_url or "").strip()
    if not clean_sitemap_url.startswith(("https://", "http://")):
        return {"ok": False, "error": "invalid sitemap URL", "auth_source": auth_source}

    last_error = ""
    for candidate_site_url in _search_console_site_candidates(site_url):
        encoded_site = urllib.parse.quote(candidate_site_url, safe="")
        encoded_sitemap = urllib.parse.quote(clean_sitemap_url, safe="")
        endpoint = f"https://www.googleapis.com/webmasters/v3/sites/{encoded_site}/sitemaps/{encoded_sitemap}"
        try:
            response = requests.put(
                endpoint,
                headers={"Authorization": f"Bearer {credentials.token}"},
                timeout=20,
            )
        except requests.RequestException as exc:
            last_error = str(exc)
            logger.warning("[search_console] sitemap_submit_request_failed site_url=%s error=%s", candidate_site_url, exc)
            continue

        status_code = int(response.status_code or 0)
        if 200 <= status_code < 300:
            if candidate_site_url != site_url:
                logger.info(
                    "[search_console] sitemap_submit_alternate_property configured_site_url=%s site_url=%s sitemap=%s",
                    site_url,
                    candidate_site_url,
                    clean_sitemap_url,
                )
            return {
                "ok": True,
                "status": status_code,
                "site_url": candidate_site_url,
                "sitemap_url": clean_sitemap_url,
                "auth_source": auth_source,
            }

        last_error = response.text[:500] or f"HTTP {status_code}"
        logger.warning(
            "[search_console] sitemap_submit_api_error auth=%s site_url=%s sitemap=%s status=%s body=%s",
            auth_source,
            candidate_site_url,
            clean_sitemap_url,
            status_code,
            last_error,
        )

    return {
        "ok": False,
        "status": None,
        "site_url": site_url,
        "sitemap_url": clean_sitemap_url,
        "auth_source": auth_source,
        "error": last_error or "all Search Console properties failed",
    }


async def submit_sitemap(
    *,
    enabled: bool,
    site_url: Optional[str],
    sitemap_url: Optional[str],
    service_account_json: Optional[str],
    service_account_file: Optional[str],
    oauth_client_json: Optional[str] = None,
    oauth_client_file: Optional[str] = None,
    oauth_token_json: Optional[str] = None,
    oauth_token_file: Optional[str] = None,
) -> dict[str, Any]:
    """Submit the sitemap when Search Console is connected; otherwise explain why not."""
    if not enabled:
        return {"ok": False, "skipped": True, "error": "disabled"}
    if not site_url:
        return {"ok": False, "skipped": True, "error": "site URL missing"}
    if not sitemap_url:
        return {"ok": False, "skipped": True, "error": "sitemap URL missing"}
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
        return {"ok": False, "skipped": True, "error": "not configured"}

    return await asyncio.to_thread(
        submit_sitemap_sync,
        site_url=str(site_url),
        sitemap_url=str(sitemap_url),
        service_account_json=service_account_json,
        service_account_file=service_account_file,
        oauth_client_json=oauth_client_json,
        oauth_client_file=oauth_client_file,
        oauth_token_json=oauth_token_json,
        oauth_token_file=oauth_token_file,
    )
