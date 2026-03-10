"""
Telegram server IP ranges for webhook source verification.

These CIDR blocks are published by Telegram and represent the addresses from
which Telegram will POST webhook updates to your server.

Reference: https://core.telegram.org/bots/webhooks#the-short-version
           https://core.telegram.org/resources/cidr.txt

Update procedure:
  Telegram occasionally expands or changes its IP ranges. Check the CIDR list
  at the URL above periodically and update TELEGRAM_IP_NETWORKS below.
  No environment variable change or restart is required — edit this file and
  redeploy (or wait for a live-reload cycle if hot-reload is enabled).

Usage in code:
  from configs.telegram_ip_whitelist import is_telegram_ip

  if is_telegram_ip(client_ip):
      # Request originates from Telegram – skip external rate limiting
      pass
"""

import ipaddress
import logging
from typing import Sequence

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known Telegram CIDR ranges (IPv4 and IPv6)
# Source: https://core.telegram.org/resources/cidr.txt  (as of 2026-01)
# ---------------------------------------------------------------------------
_TELEGRAM_CIDR_STRINGS: Sequence[str] = (
    # IPv4 – primary Telegram server subnets
    "149.154.160.0/20",
    "91.108.4.0/22",
    "91.108.8.0/22",
    "91.108.12.0/22",
    "91.108.16.0/22",
    "91.108.20.0/22",
    "91.108.56.0/22",
    "95.161.64.0/20",
    "5.28.176.0/20",
    # IPv6 – Telegram dual-stack ranges
    "2001:b28:f23d::/48",
    "2001:b28:f23f::/48",
    "2001:67c:4e8::/48",
    "2001:b28:f23c::/48",
    "2a0a:f280::/32",
)

# Pre-compile networks for fast lookups
_TELEGRAM_NETWORKS: tuple = ()

try:
    _TELEGRAM_NETWORKS = tuple(
        ipaddress.ip_network(cidr, strict=False) for cidr in _TELEGRAM_CIDR_STRINGS
    )
    logger.debug(
        "Loaded %d Telegram IP network(s) for whitelist", len(_TELEGRAM_NETWORKS)
    )
except ValueError as exc:
    logger.error("Failed to parse Telegram IP networks: %s", exc)


def is_telegram_ip(ip: str) -> bool:
    """Return True if *ip* falls within a known Telegram server CIDR range.

    Unknown / unparseable addresses return False (fail-open for rate limiting,
    not fail-closed for security — real webhook payloads are still verified by
    the X-Telegram-Bot-Api-Secret-Token header).

    Args:
        ip: IPv4 or IPv6 address string (without port).

    Returns:
        True if the address is within a Telegram network, False otherwise.
    """
    if not ip or ip in ("unknown", ""):
        return False
    try:
        addr = ipaddress.ip_address(ip)
        return any(addr in network for network in _TELEGRAM_NETWORKS)
    except ValueError:
        logger.debug("is_telegram_ip: unparseable address %r — treating as non-Telegram", ip)
        return False
