"""
Global rate limiter middleware with layered rules and auto-blocking.

Provides a ``GlobalRateLimiter`` middleware that applies different rate limits
depending on the request path:

  * Layer 1 – Aggressive limiter for API/storefront routes (``/api/*`` and ``/*``):
      configurable via env vars, defaults to 30 req / 60 s per IP.
      Exempt: ``/webhook`` and ``/webhook/*`` paths.

  * Layer 2 – Strict limiter for static assets (``/assets/*``):
      defaults to 60 req / 60 s per IP.

  * Layer 3 – Webhook-specific rate limiting already handled upstream by
      ``WebhookIPRateLimiter`` inside each ``/webhook`` handler; not
      duplicated here.

Auto-blocking:
  An IP that exceeds the rate limit ``AUTO_BLOCK_THRESHOLD`` times within
  ``AUTO_BLOCK_WINDOW`` seconds is automatically blocked for
  ``AUTO_BLOCK_DURATION`` seconds (applies to all layers).

All limits and durations are configurable via environment variables.
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


class GlobalRateLimiter(BaseHTTPMiddleware):
    """Layered per-IP rate limiter middleware applied to all non-webhook traffic.

    Layer 1: API / storefront routes (``/api/*``, ``/*``) – aggressive limit.
    Layer 2: Static assets (``/assets/*``) – strict limit.
    Webhook paths (``/webhook``, ``/webhook/*``) are exempt.

    The two layers maintain independent token buckets and short-term block
    states per IP, so asset scraping and API abuse are tracked separately.
    A global auto-block kicks in when an IP accumulates repeated violations
    across any layer within the auto-block window.

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
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            return forwarded_for.split(",")[0].strip()
        return (
            request.headers.get("X-Real-IP")
            or (request.client.host if request.client else "unknown")
        )

    def _is_exempt(self, path: str) -> bool:
        """Return True for paths that should bypass global rate limiting."""
        return path == "/webhook" or path.startswith("/webhook/")

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
        if self._disabled:
            return await call_next(request)

        path = request.url.path

        if self._is_exempt(path):
            return await call_next(request)

        ip = self._get_client_ip(request)
        now = _time.monotonic()
        is_assets = path.startswith("/assets/")

        async with self._lock:
            # 1. Check global auto-block (applies to all layers)
            if ip in self._auto_blocked:
                if now < self._auto_blocked[ip]:
                    remaining = int(self._auto_blocked[ip] - now)
                    return self._rate_limited_response(ip, path, remaining, "auto-blocked")
                del self._auto_blocked[ip]

            # 2. Select per-layer state
            layer_blocked = self._assets_blocked if is_assets else self._api_blocked
            layer_buckets = self._assets_buckets if is_assets else self._buckets
            layer_rate = self._assets_rate if is_assets else self._rate
            layer_burst = self._assets_burst if is_assets else self._burst

            # 3. Check per-layer short-term block
            if ip in layer_blocked:
                if now < layer_blocked[ip]:
                    remaining = int(layer_blocked[ip] - now)
                    # Record violation while persisting in block (helps trigger auto-block)
                    if self._record_violation(ip, now):
                        self._apply_auto_block(ip, now, path)
                        retry_after = self._auto_block_duration
                    else:
                        retry_after = remaining
                    return self._rate_limited_response(ip, path, retry_after, "blocked")
                del layer_blocked[ip]

            # 4. Apply token-bucket check
            if not self._check_token_bucket(layer_buckets, ip, layer_rate, layer_burst):
                should_auto_block = self._record_violation(ip, now)
                if should_auto_block:
                    self._apply_auto_block(ip, now, path)
                    retry_after = self._auto_block_duration
                else:
                    if len(layer_blocked) >= _MAX_BUCKETS:
                        layer_blocked.popitem(last=False)
                    layer_blocked[ip] = now + self._block_duration
                    retry_after = self._block_duration
                return self._rate_limited_response(ip, path, retry_after)

        return await call_next(request)
