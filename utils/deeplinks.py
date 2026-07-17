"""Helpers for product deep links across bot, storefront, and Mini App surfaces."""

from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def build_bot_product_url(product_id: int, bot_username: str) -> Optional[str]:
    """Return the Telegram bot deep link for a product."""
    return f"https://t.me/{bot_username}?start=product_{product_id}" if bot_username else None


def build_bot_start_url(bot_username: str, payload: str = "start") -> Optional[str]:
    """Return a Telegram bot start deep link with a stable payload."""
    return f"https://t.me/{bot_username}?start={payload}" if bot_username else None


def normalize_webapp_url(web_app_url: str) -> Optional[str]:
    """Return the canonical storefront home URL."""
    if not web_app_url:
        return None

    parts = urlsplit(web_app_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))

    return urlunsplit((
        parts.scheme,
        parts.netloc,
        "/",
        urlencode(query),
        parts.fragment,
    ))


def build_webapp_shell_url(web_app_url: str, product_id: Optional[int] = None) -> Optional[str]:
    """Return the Telegram Mini App shell URL, optionally preloading a product."""
    base_url = normalize_webapp_url(web_app_url)
    if not base_url:
        return None

    parts = urlsplit(base_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    if product_id is not None:
        query["product_id"] = str(int(product_id))

    return urlunsplit((
        parts.scheme,
        parts.netloc,
        "/webapp/index.html",
        urlencode(query),
        parts.fragment,
    ))


def build_webapp_product_url(web_app_url: str, product_id: int) -> Optional[str]:
    """Return a storefront URL that opens directly to a product detail view."""
    if not web_app_url or product_id is None:
        return None

    base_url = normalize_webapp_url(web_app_url)
    if not base_url:
        return None

    parts = urlsplit(base_url)
    return urlunsplit((
        parts.scheme,
        parts.netloc,
        f"/product/{int(product_id)}",
        parts.query,
        parts.fragment,
    ))
