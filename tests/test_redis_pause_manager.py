"""
Unit tests for Redis-backed pause manager.

Tests the shared pause state mechanism that prevents multiple workers
from repeatedly hitting rate limits.
"""
import os
import sys
import pytest
import pytest_asyncio
import asyncio
import time
import json
from unittest.mock import AsyncMock, MagicMock, patch

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils import redis_pause_manager
from configs.config import Config


@pytest_asyncio.fixture
async def redis_client():
    """
    Create a fake Redis client for testing.
    
    Uses fakeredis to simulate Redis without needing a real Redis server.
    """
    import fakeredis.aioredis
    
    # Create a fake Redis instance
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    
    # Patch the get_redis_client function to return our fake Redis
    original_get_redis_client = redis_pause_manager.get_redis_client
    
    def mock_get_redis_client():
        return fake_redis
    
    redis_pause_manager.get_redis_client = mock_get_redis_client
    
    yield fake_redis
    
    # Cleanup
    await fake_redis.flushall()
    await fake_redis.aclose()
    
    # Restore original function
    redis_pause_manager.get_redis_client = original_get_redis_client


class TestPauseKeyFormat:
    """Test the Redis key format for pause state."""
    
    def test_get_pause_key_format(self):
        """Test that pause key has correct format."""
        key = redis_pause_manager.get_pause_key("test_bot")
        assert key == "notifications:pause:test_bot"
    
    def test_get_pause_key_with_special_chars(self):
        """Test pause key with special characters in bot name."""
        key = redis_pause_manager.get_pause_key("bot_with_underscore")
        assert key == "notifications:pause:bot_with_underscore"


class TestPauseStateBasicOperations:
    """Test basic pause state operations."""
    
    @pytest.mark.asyncio
    async def test_get_pause_state_when_not_set(self, redis_client):
        """Test getting pause state when none is set."""
        state = await redis_pause_manager.get_pause_state("test_bot")
        assert state is None
    
    @pytest.mark.asyncio
    async def test_set_and_get_pause_state(self, redis_client):
        """Test setting and retrieving pause state."""
        bot_username = "test_bot"
        retry_after_seconds = 30.0
        
        # Set pause state
        success = await redis_pause_manager.set_pause_state(
            bot_username=bot_username,
            retry_after_seconds=retry_after_seconds,
            reason="rate_limit_429",
            set_by="test",
            retry_after_header="30"
        )
        
        assert success is True
        
        # Get pause state
        state = await redis_pause_manager.get_pause_state(bot_username)
        
        assert state is not None
        assert "paused_until" in state
        assert state["reason"] == "rate_limit_429"
        assert state["set_by"] == "test"
        assert state["retry_after_header"] == "30"
        
        # Verify paused_until is in the future
        current_time = time.time()
        assert state["paused_until"] > current_time
    
    @pytest.mark.asyncio
    async def test_is_paused(self, redis_client):
        """Test checking if bot is paused."""
        bot_username = "test_bot"
        
        # Initially not paused
        assert await redis_pause_manager.is_paused(bot_username) is False
        
        # Set pause
        await redis_pause_manager.set_pause_state(
            bot_username=bot_username,
            retry_after_seconds=10.0,
            reason="test",
            set_by="test"
        )
        
        # Now paused
        assert await redis_pause_manager.is_paused(bot_username) is True
    
    @pytest.mark.asyncio
    async def test_get_remaining_pause_seconds(self, redis_client):
        """Test getting remaining pause duration."""
        bot_username = "test_bot"
        
        # Initially no pause
        remaining = await redis_pause_manager.get_remaining_pause_seconds(bot_username)
        assert remaining == 0.0
        
        # Set pause for 30 seconds
        await redis_pause_manager.set_pause_state(
            bot_username=bot_username,
            retry_after_seconds=30.0,
            reason="test",
            set_by="test",
            buffer_seconds=5.0
        )
        
        # Get remaining time (should be ~35 seconds: 30 + 5 buffer)
        remaining = await redis_pause_manager.get_remaining_pause_seconds(bot_username)
        assert 30.0 <= remaining <= 40.0  # Allow some variance
    
    @pytest.mark.asyncio
    async def test_clear_pause_state(self, redis_client):
        """Test clearing pause state."""
        bot_username = "test_bot"
        
        # Set pause
        await redis_pause_manager.set_pause_state(
            bot_username=bot_username,
            retry_after_seconds=30.0,
            reason="test",
            set_by="test"
        )
        
        # Verify paused
        assert await redis_pause_manager.is_paused(bot_username) is True
        
        # Clear pause
        await redis_pause_manager.clear_pause_state(bot_username)
        
        # Verify not paused
        assert await redis_pause_manager.is_paused(bot_username) is False


class TestAtomicPauseExtension:
    """Test atomic pause set/extend semantics."""
    
    @pytest.mark.asyncio
    async def test_extend_pause_with_longer_duration(self, redis_client):
        """Test that longer pause extends existing pause."""
        bot_username = "test_bot"
        
        # Set initial pause for 10 seconds
        await redis_pause_manager.set_pause_state(
            bot_username=bot_username,
            retry_after_seconds=10.0,
            reason="initial",
            set_by="test1",
            buffer_seconds=0.0
        )
        
        initial_state = await redis_pause_manager.get_pause_state(bot_username)
        initial_paused_until = initial_state["paused_until"]
        
        # Try to set pause for 30 seconds (longer)
        await asyncio.sleep(0.1)  # Small delay to ensure time difference
        success = await redis_pause_manager.set_pause_state(
            bot_username=bot_username,
            retry_after_seconds=30.0,
            reason="extended",
            set_by="test2",
            buffer_seconds=0.0
        )
        
        assert success is True
        
        # Verify pause was extended
        new_state = await redis_pause_manager.get_pause_state(bot_username)
        assert new_state["paused_until"] > initial_paused_until
        assert new_state["reason"] == "extended"
        assert new_state["set_by"] == "test2"
    
    @pytest.mark.asyncio
    async def test_do_not_shorten_pause_with_shorter_duration(self, redis_client):
        """Test that shorter pause does not override longer existing pause."""
        bot_username = "test_bot"
        
        # Set initial pause for 60 seconds
        await redis_pause_manager.set_pause_state(
            bot_username=bot_username,
            retry_after_seconds=60.0,
            reason="initial_long",
            set_by="test1",
            buffer_seconds=0.0
        )
        
        initial_state = await redis_pause_manager.get_pause_state(bot_username)
        initial_paused_until = initial_state["paused_until"]
        
        # Try to set pause for 10 seconds (shorter)
        await asyncio.sleep(0.1)
        success = await redis_pause_manager.set_pause_state(
            bot_username=bot_username,
            retry_after_seconds=10.0,
            reason="attempted_short",
            set_by="test2",
            buffer_seconds=0.0
        )
        
        # Pause should NOT be set (shorter duration)
        # The Lua script returns 0 when not updating
        # In practice, success will be False
        
        # Verify pause was NOT changed
        current_state = await redis_pause_manager.get_pause_state(bot_username)
        assert abs(current_state["paused_until"] - initial_paused_until) < 1.0  # Should be same
        assert current_state["reason"] == "initial_long"  # Not changed
        assert current_state["set_by"] == "test1"  # Not changed


class TestPauseExpiry:
    """Test automatic pause expiry."""
    
    @pytest.mark.asyncio
    async def test_expired_pause_returns_none(self, redis_client):
        """Test that expired pause returns None."""
        bot_username = "test_bot"
        
        # Set pause for very short duration (1 second)
        await redis_pause_manager.set_pause_state(
            bot_username=bot_username,
            retry_after_seconds=0.5,
            reason="short",
            set_by="test",
            buffer_seconds=0.0
        )
        
        # Verify paused
        assert await redis_pause_manager.is_paused(bot_username) is True
        
        # Wait for expiry
        await asyncio.sleep(1.0)
        
        # Verify not paused
        assert await redis_pause_manager.is_paused(bot_username) is False
        state = await redis_pause_manager.get_pause_state(bot_username)
        assert state is None


class TestMetrics:
    """Test pause metrics."""
    
    @pytest.mark.asyncio
    async def test_metrics_increment_on_pause_set(self, redis_client):
        """Test that metrics increment when pause is set."""
        # Reset metrics
        redis_pause_manager._metrics["pause_set_count"] = 0
        
        initial_count = redis_pause_manager.get_metrics()["pause_set_count"]
        
        # Set pause
        await redis_pause_manager.set_pause_state(
            bot_username="test_bot",
            retry_after_seconds=30.0,
            reason="test",
            set_by="test"
        )
        
        # Verify metric incremented
        new_count = redis_pause_manager.get_metrics()["pause_set_count"]
        assert new_count == initial_count + 1
    
    @pytest.mark.asyncio
    async def test_metrics_increment_on_pause_skipped(self, redis_client):
        """Test that metrics increment when processing is skipped."""
        # Reset metrics
        redis_pause_manager._metrics["pause_skipped_count"] = 0
        
        initial_count = redis_pause_manager.get_metrics()["pause_skipped_count"]
        
        # Increment skipped metric
        await redis_pause_manager.increment_pause_skipped_metric("test_bot")
        
        # Verify metric incremented
        new_count = redis_pause_manager.get_metrics()["pause_skipped_count"]
        assert new_count == initial_count + 1


class TestHeaderParsing:
    """Test parsing of Retry-After and X-RateLimit-Reset headers."""
    
    def test_parse_retry_after_integer(self):
        """Test parsing integer seconds from Retry-After."""
        result = redis_pause_manager.parse_retry_after(30)
        assert result == 30.0
        
        result = redis_pause_manager.parse_retry_after("60")
        assert result == 60.0
    
    def test_parse_retry_after_http_date(self):
        """Test parsing HTTP-date format from Retry-After."""
        # Create a date 30 seconds in the future
        from email.utils import formatdate
        future_date = formatdate(timeval=time.time() + 30, usegmt=True)
        
        result = redis_pause_manager.parse_retry_after(future_date)
        assert result is not None
        assert 25 <= result <= 35  # Should be around 30 seconds
    
    def test_parse_retry_after_invalid(self):
        """Test parsing invalid Retry-After value."""
        result = redis_pause_manager.parse_retry_after("invalid")
        assert result is None
        
        result = redis_pause_manager.parse_retry_after(None)
        assert result is None
    
    def test_parse_ratelimit_reset_timestamp(self):
        """Test parsing Unix timestamp from X-RateLimit-Reset."""
        # Create timestamp 30 seconds in the future
        future_timestamp = time.time() + 30
        
        result = redis_pause_manager.parse_ratelimit_reset(future_timestamp)
        assert result is not None
        assert 25 <= result <= 35  # Should be around 30 seconds
        
        result = redis_pause_manager.parse_ratelimit_reset(str(future_timestamp))
        assert result is not None
        assert 25 <= result <= 35
    
    def test_parse_ratelimit_reset_invalid(self):
        """Test parsing invalid X-RateLimit-Reset value."""
        result = redis_pause_manager.parse_ratelimit_reset("invalid")
        assert result is None
        
        result = redis_pause_manager.parse_ratelimit_reset(None)
        assert result is None


class TestConcurrentAccess:
    """Test concurrent access to pause state (race conditions)."""
    
    @pytest.mark.asyncio
    async def test_concurrent_pause_set_atomic(self, redis_client):
        """Test that concurrent pause sets are atomic."""
        bot_username = "test_bot"
        
        # Simulate multiple workers trying to set pause concurrently
        async def set_pause_worker(duration, worker_id):
            await redis_pause_manager.set_pause_state(
                bot_username=bot_username,
                retry_after_seconds=duration,
                reason=f"worker_{worker_id}",
                set_by=f"worker_{worker_id}",
                buffer_seconds=0.0
            )
        
        # Start multiple workers with different durations
        tasks = [
            set_pause_worker(10.0, 1),
            set_pause_worker(30.0, 2),
            set_pause_worker(20.0, 3),
            set_pause_worker(50.0, 4),
        ]
        
        await asyncio.gather(*tasks)
        
        # The final state should have the longest pause (50 seconds)
        state = await redis_pause_manager.get_pause_state(bot_username)
        assert state is not None
        
        # Calculate expected paused_until (approximately current_time + 50)
        current_time = time.time()
        expected_min = current_time + 45
        expected_max = current_time + 55
        
        assert expected_min <= state["paused_until"] <= expected_max
        # The reason should be from the worker that set the longest pause
        # Due to atomic operations, it should be worker_4
        assert state["reason"] == "worker_4" or state["set_by"] == "worker_4"
