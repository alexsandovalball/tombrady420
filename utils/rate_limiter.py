"""
Async token-bucket rate limiter for per-bot message sending.

Each RateLimiter instance tracks a token bucket keyed to a single bot.
Tokens refill at `rate_per_sec` per second up to a maximum of `burst`.
`await limiter.acquire()` blocks until a token is available.
"""
import asyncio
import time


class RateLimiter:
    """
    Async token-bucket rate limiter.

    Args:
        rate_per_sec: Number of tokens added per second (steady-state rate).
        burst: Maximum number of tokens that can accumulate (burst capacity).
    """

    def __init__(self, rate_per_sec: float, burst: int) -> None:
        if rate_per_sec <= 0:
            raise ValueError("rate_per_sec must be positive")
        if burst < 1:
            raise ValueError("burst must be at least 1")
        self._rate = rate_per_sec
        self._burst = burst
        self._tokens: float = float(burst)
        self._last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        """Add tokens based on elapsed time (called while holding the lock)."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._burst, self._tokens + elapsed * self._rate)
        self._last_refill = now

    async def acquire(self, n: int = 1) -> None:
        """
        Block until `n` tokens are available, then consume them.

        The lock is released while sleeping so that other coroutines can make
        progress.  After waking, the token count is always rechecked (the
        `while True` loop) to handle the case where another waiter consumed
        tokens during the sleep interval.

        Args:
            n: Number of tokens to consume (default 1).
        """
        if n < 1:
            raise ValueError("n must be at least 1")
        async with self._lock:
            while True:
                self._refill()
                if self._tokens >= n:
                    self._tokens -= n
                    return
                # Calculate how long until enough tokens are available
                deficit = n - self._tokens
                wait = deficit / self._rate
                # Release lock while sleeping so other coroutines can progress.
                # Re-acquire before looping back to recheck the token count.
                self._lock.release()
                try:
                    await asyncio.sleep(wait)
                finally:
                    await self._lock.acquire()
