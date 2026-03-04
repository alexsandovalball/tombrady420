"""
Tests for the rate limiter and bot-worker concurrency features.

Covers:
- RateLimiter: basic acquire, burst consumption, blocking when no tokens
- Per-bot rate limiter instance management in NotificationService
- Global concurrency semaphore cap (MAX_CONCURRENT_BOT_WORKERS)
- New database methods: count_pending_notifications,
  count_pending_notifications_by_bot, count_pending_custom_messages_by_bot
"""
import asyncio
import time
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from utils.rate_limiter import RateLimiter
from tests.conftest import in_memory_db  # noqa: F401 – imported for fixture discovery


# ---------------------------------------------------------------------------
# RateLimiter unit tests
# ---------------------------------------------------------------------------

class TestRateLimiter:
    def test_invalid_rate_raises(self):
        with pytest.raises(ValueError):
            RateLimiter(rate_per_sec=0, burst=5)

    def test_invalid_burst_raises(self):
        with pytest.raises(ValueError):
            RateLimiter(rate_per_sec=1, burst=0)

    @pytest.mark.asyncio
    async def test_invalid_acquire_n_raises(self):
        limiter = RateLimiter(rate_per_sec=10, burst=10)
        with pytest.raises(ValueError):
            await limiter.acquire(n=0)

    @pytest.mark.asyncio
    async def test_burst_consumed_immediately(self):
        """Acquiring up to the burst size should not block."""
        limiter = RateLimiter(rate_per_sec=10, burst=5)
        start = time.monotonic()
        for _ in range(5):
            await limiter.acquire()
        elapsed = time.monotonic() - start
        # Should complete nearly instantly (well under 0.5 s even on slow CI)
        assert elapsed < 0.5

    @pytest.mark.asyncio
    async def test_exceeding_burst_causes_delay(self):
        """Acquiring more tokens than burst requires waiting for refill."""
        rate = 20  # tokens/second
        burst = 2
        limiter = RateLimiter(rate_per_sec=rate, burst=burst)
        # Drain the burst
        for _ in range(burst):
            await limiter.acquire()
        # One more token should require ~1/rate seconds to refill
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        min_expected = 1 / rate * 0.5  # allow 50% tolerance
        assert elapsed >= min_expected, f"Expected delay >= {min_expected:.3f}s, got {elapsed:.3f}s"

    @pytest.mark.asyncio
    async def test_tokens_refill_over_time(self):
        """After waiting, tokens should be available again."""
        limiter = RateLimiter(rate_per_sec=100, burst=1)
        await limiter.acquire()  # drain
        await asyncio.sleep(0.05)  # wait for 5 tokens worth of refill
        # Should be able to acquire again quickly
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_concurrent_acquires_are_serialised(self):
        """Concurrent acquire calls should not produce a race condition."""
        limiter = RateLimiter(rate_per_sec=1000, burst=50)
        results = []

        async def task():
            await limiter.acquire()
            results.append(1)

        await asyncio.gather(*[task() for _ in range(50)])
        assert len(results) == 50


# ---------------------------------------------------------------------------
# Per-bot rate limiter management in NotificationService
# ---------------------------------------------------------------------------

class TestNotificationServiceRateLimiterManagement:
    def _make_service(self):
        """Create a NotificationService with a minimal mock DB."""
        from utils.notifications import NotificationService
        db = MagicMock()
        return NotificationService(db)

    def test_get_bot_rate_limiter_creates_on_first_call(self):
        svc = self._make_service()
        limiter = svc._get_bot_rate_limiter("testbot")
        assert limiter is not None

    def test_get_bot_rate_limiter_returns_same_instance(self):
        svc = self._make_service()
        l1 = svc._get_bot_rate_limiter("testbot")
        l2 = svc._get_bot_rate_limiter("testbot")
        assert l1 is l2

    def test_get_bot_rate_limiter_normalises_at_prefix(self):
        svc = self._make_service()
        l1 = svc._get_bot_rate_limiter("testbot")
        l2 = svc._get_bot_rate_limiter("@testbot")
        assert l1 is l2

    def test_get_bot_rate_limiter_normalises_case(self):
        svc = self._make_service()
        l1 = svc._get_bot_rate_limiter("TestBot")
        l2 = svc._get_bot_rate_limiter("testbot")
        assert l1 is l2

    def test_get_bot_rate_limiter_none_uses_primary_key(self):
        svc = self._make_service()
        l1 = svc._get_bot_rate_limiter(None)
        assert 'primary' in svc._bot_rate_limiters

    def test_different_bots_get_different_limiters(self):
        svc = self._make_service()
        l1 = svc._get_bot_rate_limiter("botA")
        l2 = svc._get_bot_rate_limiter("botB")
        assert l1 is not l2


# ---------------------------------------------------------------------------
# Global concurrency semaphore
# ---------------------------------------------------------------------------

class TestBotWorkerSemaphore:
    def _make_service(self):
        from utils.notifications import NotificationService, MAX_CONCURRENT_BOT_WORKERS
        db = MagicMock()
        svc = NotificationService(db)
        return svc, MAX_CONCURRENT_BOT_WORKERS

    def test_semaphore_is_initialised_with_constant(self):
        svc, cap = self._make_service()
        # asyncio.Semaphore stores initial value as _value
        assert svc._bot_worker_semaphore._value == cap

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self):
        """Verify that no more than MAX_CONCURRENT_BOT_WORKERS coroutines run at once."""
        from utils.notifications import MAX_CONCURRENT_BOT_WORKERS
        svc, cap = self._make_service()
        sem = svc._bot_worker_semaphore

        concurrency_peak = 0
        current = 0
        lock = asyncio.Lock()

        async def worker():
            nonlocal concurrency_peak, current
            async with sem:
                async with lock:
                    current += 1
                    concurrency_peak = max(concurrency_peak, current)
                await asyncio.sleep(0.01)
                async with lock:
                    current -= 1

        num_bots = cap * 3
        await asyncio.gather(*[worker() for _ in range(num_bots)])
        assert concurrency_peak <= cap


# ---------------------------------------------------------------------------
# Database: pending count methods
# ---------------------------------------------------------------------------

class TestPendingCounts:
    @pytest.mark.asyncio
    async def test_count_pending_notifications_empty(self, in_memory_db):
        count = await in_memory_db.count_pending_notifications()
        assert count == 0

    @pytest.mark.asyncio
    async def test_count_pending_notifications_after_queue(self, in_memory_db):
        # Add a user and product so we can queue a notification
        await in_memory_db.track_user(user_id=1, username="u1", first_name="U", bot_username="bot1")
        pid = await in_memory_db.add_product(
            file_id="f1", file_type="photo", caption="c", message_id=1, chat_id=-1
        )
        await in_memory_db.queue_notification(user_id=1, product_id=pid)
        count = await in_memory_db.count_pending_notifications()
        assert count == 1

    @pytest.mark.asyncio
    async def test_count_pending_custom_messages_empty(self, in_memory_db):
        count = await in_memory_db.count_pending_custom_messages()
        assert count == 0

    @pytest.mark.asyncio
    async def test_count_pending_custom_messages_after_queue(self, in_memory_db):
        await in_memory_db.track_user(user_id=2, username="u2", first_name="U2", bot_username="bot1")
        await in_memory_db.queue_custom_message(user_id=2, message_text="hello")
        count = await in_memory_db.count_pending_custom_messages()
        assert count == 1

    @pytest.mark.asyncio
    async def test_count_pending_notifications_by_bot(self, in_memory_db):
        await in_memory_db.track_user(user_id=10, username="u10", first_name="A", bot_username="botA")
        await in_memory_db.track_user(user_id=11, username="u11", first_name="B", bot_username="botB")
        pid = await in_memory_db.add_product(
            file_id="f10", file_type="photo", caption="c", message_id=10, chat_id=-1
        )
        pid2 = await in_memory_db.add_product(
            file_id="f11", file_type="photo", caption="c2", message_id=11, chat_id=-1
        )
        await in_memory_db.queue_notification(user_id=10, product_id=pid)
        await in_memory_db.queue_notification(user_id=11, product_id=pid2)
        rows = await in_memory_db.count_pending_notifications_by_bot(top_n=5)
        bot_names = {r["bot_username"] for r in rows}
        assert "botA" in bot_names
        assert "botB" in bot_names
        for r in rows:
            assert r["pending"] >= 1

    @pytest.mark.asyncio
    async def test_count_pending_custom_messages_by_bot(self, in_memory_db):
        await in_memory_db.track_user(user_id=20, username="u20", first_name="X", bot_username="botX")
        await in_memory_db.track_user(user_id=21, username="u21", first_name="Y", bot_username="botY")
        await in_memory_db.queue_custom_message(user_id=20, message_text="msg1")
        await in_memory_db.queue_custom_message(user_id=21, message_text="msg2")
        rows = await in_memory_db.count_pending_custom_messages_by_bot(top_n=5)
        bot_names = {r["bot_username"] for r in rows}
        assert "botX" in bot_names
        assert "botY" in bot_names
