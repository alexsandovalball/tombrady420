"""Helpers for product deep links across bot and WebApp surfaces."""

from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


def build_bot_product_url(product_id: int, bot_username: str) -> Optional[str]:
    """Return the Telegram bot deep link for a product."""
    return f"https://t.me/{bot_username}?start=product_{product_id}" if bot_username else None


def build_webapp_product_url(web_app_url: str, product_id: int) -> Optional[str]:
    """Return a WebApp URL that opens directly to a product detail view."""
    if not web_app_url or product_id is None:
        return None

    parts = urlsplit(web_app_url)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["product_id"] = str(product_id)
    return urlunsplit((
        parts.scheme,
        parts.netloc,
        parts.path,
        urlencode(query),
        parts.fragment,
    ))
