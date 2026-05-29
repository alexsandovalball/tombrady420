"""
Global rate limiter middleware with malicious probe auto-blocking.

Provides a ``GlobalRateLimiter`` middleware that:

  * Allows unlimited requests for normal traffic (no token bucket rate limiting)
  * Exempt: ``/webhook`` and ``/webhook/*`` paths (handled separately)
  * Auto-blocks IPs that probe for malicious paths (wp-login, wp-admin, .env, etc.)

Auto-blocking:
  An IP that attempts to access any malicious probe path is immediately blocked
  for ``AUTO_BLOCK_DURATION`` seconds (default: 1 hour). Previously auto-blocked
  IPs are rejected on all subsequent requests until the block expires.

All durations are configurable via environment variables.
"""

import asyncio
import logging
import os
import time as _time
from collections import OrderedDict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger_security = logging.getLogger("security")

# ---------------------------------------------------------------------------
# Configuration – read from env vars with sensible defaults
# ---------------------------------------------------------------------------

_RATE_LIMIT_REQUESTS: int = int(os.getenv("RATE_LIMIT_REQUESTS", "30"))
_RATE_LIMIT_WINDOW: int = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
_RATE_LIMIT_BLOCK_DURATION: int = int(os.getenv("RATE_LIMIT_BLOCK_DURATION", "900"))

_ASSETS_RATE_LIMIT_REQUESTS: int = int(os.getenv("ASSETS_RATE_LIMIT_REQUESTS", "60"))

_AUTO_BLOCK_THRESHOLD: int = int(os.getenv("AUTO_BLOCK_THRESHOLD", "3"))
_AUTO_BLOCK_WINDOW: int = int(os.getenv("AUTO_BLOCK_WINDOW", "600"))  # 10 minutes
_AUTO_BLOCK_DURATION: int = int(os.getenv("AUTO_BLOCK_DURATION", "3600"))  # 1 hour

_MAX_BUCKETS: int = 10_000  # LRU eviction threshold

# Malicious probe path patterns - paths commonly probed by bots/scrapers
_MALICIOUS_PROBE_PATTERNS = {
    "wp-login", "wp-admin", ".env", ".git", ".php", "setup.php",
    "config.json", "phpinfo", "admin.php", "xmlrpc.php", "wp-config"
}

# ---------------------------------------------------------------------------
# Persistent IP block registry
# ---------------------------------------------------------------------------
# Module-level set of permanently blocked IP addresses.  Populated at startup
# from the ``blocked_ips`` DB table and updated at runtime whenever a user is
# blocked/unblocked via the admin command.  Checked by GlobalRateLimiter on
# every non-exempt request.

_permanent_ip_blocks: set = set()


def get_client_ip(request: Request) -> str:
    """Extract the real client IP from a Starlette Request."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return (
        request.headers.get("X-Real-IP")
        or (request.client.host if request.client else "unknown")
    )


def add_permanent_ip_block(ip: str) -> None:
    """Add an IP address to the in-memory permanent block set."""
    _permanent_ip_blocks.add(ip)


def remove_permanent_ip_block(ip: str) -> None:
    """Remove an IP address from the in-memory permanent block set."""
    _permanent_ip_blocks.discard(ip)


def load_permanent_ip_blocks(ip_list: list) -> None:
    """Bulk-load a collection of IP addresses into the permanent block set (used at startup)."""
    _permanent_ip_blocks.update(ip_list)


class GlobalRateLimiter(BaseHTTPMiddleware):
    """Malicious probe detector and auto-blocker middleware for non-webhook traffic.

    This middleware allows unlimited requests for normal traffic but immediately
    auto-blocks IPs attempting to access malicious probe paths (e.g., wp-login,
    wp-admin, .env, .git, .php files, etc.) commonly used by bots and scrapers.

    Webhook paths (``/webhook``, ``/webhook/*``) are exempt and handled separately.

    Auto-blocked IPs are rejected on all subsequent requests for the configured
    AUTO_BLOCK_DURATION (default: 1 hour).

    Rate-limited responses return HTTP 429 with a ``Retry-After`` header.
    """

    def __init__(self, app, *, max_requests: int = _RATE_LIMIT_REQUESTS,
                 window_seconds: int = _RATE_LIMIT_WINDOW,
                 block_duration: int = _RATE_LIMIT_BLOCK_DURATION,
                 assets_max_requests: int = _ASSETS_RATE_LIMIT_REQUESTS,
                 auto_block_threshold: int = _AUTO_BLOCK_THRESHOLD,
                 auto_block_window: int = _AUTO_BLOCK_WINDOW,
                 auto_block_duration: int = _AUTO_BLOCK_DURATION) -> None:
        super().__init__(app)
        self._disabled = max_requests <= 0
        self._rate = max_requests / window_seconds if not self._disabled else 0
        self._burst = max_requests
        self._block_duration = block_duration

        self._assets_rate = assets_max_requests / window_seconds if not self._disabled else 0
        self._assets_burst = assets_max_requests

        self._auto_block_threshold = auto_block_threshold
        self._auto_block_window = auto_block_window
        self._auto_block_duration = auto_block_duration

        # Per-layer token buckets: ip -> [tokens: float, last_refill: float]
        self._buckets: OrderedDict = OrderedDict()
        self._assets_buckets: OrderedDict = OrderedDict()

        # Per-layer short-term blocks: ip -> expiry (monotonic)
        self._api_blocked: OrderedDict = OrderedDict()
        self._assets_blocked: OrderedDict = OrderedDict()

        # Cross-layer violation timestamps for auto-blocking: ip -> [timestamps]
        self._violations: OrderedDict = OrderedDict()

        # Global auto-blocks: ip -> expiry (monotonic)
        self._auto_blocked: OrderedDict = OrderedDict()

        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_client_ip(self, request: Request) -> str:
        return get_client_ip(request)

    def _is_exempt(self, path: str) -> bool:
        """Return True for paths that should bypass global rate limiting."""
        return path == "/webhook" or path.startswith("/webhook/")

    def _is_malicious_probe(self, path: str) -> bool:
        """Return True if the path contains known malicious probe patterns."""
        path_lower = path.lower()
        return any(pattern in path_lower for pattern in _MALICIOUS_PROBE_PATTERNS)

    def _check_token_bucket(
        self,
        buckets: OrderedDict,
        ip: str,
        rate: float,
        burst: int,
    ) -> bool:
        """Non-blocking token-bucket check.  Returns True if allowed.

        Tokens accumulate at ``rate`` per second up to a maximum of ``burst``
        (the ``min(burst, ...)`` clamp bounds accumulation during idle periods,
        so the maximum number of requests any IP can burst is always ≤
        ``burst``).
        """
        now = _time.monotonic()
        if ip not in buckets:
            if len(buckets) >= _MAX_BUCKETS:
                buckets.popitem(last=False)
            buckets[ip] = [float(burst) - 1, now]
            return True
        tokens, last_refill = buckets[ip]
        elapsed = now - last_refill
        tokens = min(burst, tokens + elapsed * rate)
        buckets.move_to_end(ip)
        if tokens >= 1:
            buckets[ip] = [tokens - 1, now]
            return True
        buckets[ip] = [tokens, now]
        return False

    def _record_violation(self, ip: str, now: float) -> bool:
        """Record a rate-limit violation; return True if auto-block threshold reached."""
        if ip not in self._violations:
            if len(self._violations) >= _MAX_BUCKETS:
                self._violations.popitem(last=False)
            self._violations[ip] = []
        self._violations.move_to_end(ip)
        cutoff = now - self._auto_block_window
        self._violations[ip] = [t for t in self._violations[ip] if t > cutoff]
        self._violations[ip].append(now)
        return len(self._violations[ip]) >= self._auto_block_threshold

    def _apply_auto_block(self, ip: str, now: float, path: str) -> None:
        """Promote IP to auto-block and log the event."""
        if len(self._auto_blocked) >= _MAX_BUCKETS:
            self._auto_blocked.popitem(last=False)
        self._auto_blocked[ip] = now + self._auto_block_duration
        logger_security.warning(
            f"Auto-blocking IP {ip} for {self._auto_block_duration}s "
            f"after {self._auto_block_threshold} violations – path={path}"
        )

    def _rate_limited_response(self, ip: str, path: str, retry_after: int,
                                reason: str = "rate limit") -> Response:
        logger_security.warning(
            f"Rate limit ({reason}) for IP {ip}: path={path} "
            f"retry_after={retry_after}s"
        )
        return Response(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content=b"Too Many Requests",
        )

    # ------------------------------------------------------------------
    # Middleware dispatch
    # ------------------------------------------------------------------

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if self._is_exempt(path):
            return await call_next(request)

        ip = self._get_client_ip(request)
        now = _time.monotonic()

        # 1. Check permanent IP blocks (admin-issued, persisted to DB)
        if ip in _permanent_ip_blocks:
            logger_security.warning(
                "[IP_PERM_BLOCKED] Permanently blocked IP %s attempted access: path=%s",
                ip, path,
            )
            return Response(status_code=403, content=b"Access denied.")

        if self._disabled:
            return await call_next(request)

        async with self._lock:
            # 2. Check global auto-block (applies to all layers)
            if ip in self._auto_blocked:
                if now < self._auto_blocked[ip]:
                    remaining = int(self._auto_blocked[ip] - now)
                    return self._rate_limited_response(ip, path, remaining, "auto-blocked")
                del self._auto_blocked[ip]

            # 3. Check for malicious probe patterns - immediately auto-block
            if self._is_malicious_probe(path):
                self._apply_auto_block(ip, now, path)
                return self._rate_limited_response(ip, path, self._auto_block_duration, "malicious probe")

        # 4. Allow all other requests (bypass token bucket and layer blocking)
        return await call_next(request)
