"""Conservative request classification for privacy-aware visitor analytics."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class TrafficIdentity:
    traffic_class: str = "human"
    crawler_name: str = ""

    @property
    def is_crawler(self) -> bool:
        return self.traffic_class != "human"


_SEARCH_CRAWLERS = (
    ("googlebot", "Googlebot"),
    ("google-inspectiontool", "Google Inspection Tool"),
    ("googleother", "GoogleOther"),
    ("bingbot", "Bingbot"),
    ("bingpreview", "Bing Preview"),
    ("applebot", "Applebot"),
    ("duckduckbot", "DuckDuckBot"),
    ("yandexbot", "YandexBot"),
    ("baiduspider", "Baiduspider"),
    ("petalbot", "PetalBot"),
)

_SOCIAL_PREVIEWS = (
    ("facebookexternalhit", "Facebook Preview"),
    ("facebot", "Facebook Preview"),
    ("whatsapp", "WhatsApp Preview"),
    ("telegrambot", "Telegram Preview"),
    ("twitterbot", "X/Twitter Preview"),
    ("linkedinbot", "LinkedIn Preview"),
    ("discordbot", "Discord Preview"),
    ("slackbot", "Slack Preview"),
)

_SEO_TOOLS = (
    ("ahrefsbot", "AhrefsBot"),
    ("semrushbot", "SemrushBot"),
    ("dotbot", "DotBot"),
    ("mj12bot", "MJ12bot"),
    ("screaming frog", "Screaming Frog"),
    ("dataforseo", "DataForSEO"),
)

_AUTOMATION = (
    ("headlesschrome", "Headless Chrome"),
    ("python-requests", "Python Requests"),
    ("python-urllib", "Python urllib"),
    ("curl/", "curl"),
    ("wget/", "wget"),
    ("httpclient", "HTTP client"),
    ("uptimerobot", "UptimeRobot"),
    ("render/", "Render monitor"),
)

_GENERIC_BOT_RE = re.compile(r"(?:^|[\s;/_(])(?:bot|crawler|spider)(?:$|[\s;/_)])", re.I)


def classify_user_agent(user_agent: str) -> TrafficIdentity:
    """Classify known automated clients without guessing from user behavior.

    Unknown and empty user agents remain human so privacy browsers and embedded
    clients are not silently removed from the real-user dashboard.
    """
    ua = str(user_agent or "").strip().lower()
    if not ua:
        return TrafficIdentity()

    for token, name in _SEARCH_CRAWLERS:
        if token in ua:
            return TrafficIdentity("search_crawler", name)
    for token, name in _SOCIAL_PREVIEWS:
        if token in ua:
            return TrafficIdentity("social_preview", name)
    for token, name in _SEO_TOOLS:
        if token in ua:
            return TrafficIdentity("seo_tool", name)
    for token, name in _AUTOMATION:
        if token in ua:
            return TrafficIdentity("automation", name)
    if _GENERIC_BOT_RE.search(ua):
        return TrafficIdentity("automation", "Generic bot")
    return TrafficIdentity()


def is_crawler_user_agent(user_agent: str) -> bool:
    return classify_user_agent(user_agent).is_crawler
