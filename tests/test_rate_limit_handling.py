"""
Unit tests for rate limit handling and retry logic.

Tests:
- RetryAfter handling with shared pause state
- Transient error retry with exponential backoff  
- Recent-start retry heuristic
- Telemetry with rate_limit_info
"""
import os
import sys
import pytest
import pytest_asyncio
import asyncio
import tempfile
import time
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timedelta
from telegram.error import RetryAfter, TimedOut, NetworkError, Forbidden, BadRequest

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from utils.notifications import send_message_with_rate_handling, _mask_token
from utils import rate_limiter
from configs.config import Config


@pytest_asyncio.fixture
async def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name
    
    # Temporarily override Config.DB_PATH for rate_limiter
    original_db_path = Config.DB_PATH
    Config.DB_PATH = db_path
    
    db = Database(db_path)
    await db.init_db()
    
    yield db
    
    # Restore original config
    Config.DB_PATH = original_db_path
    
    # Cleanup
    try:
        os.unlink(db_path)
    except OSError:
        pass


class TestTokenMasking:
    """Test token masking for secure logging."""
    
    def test_mask_token_normal(self):
        """Test masking a normal token."""
        token = "1234567890ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        masked = _mask_token(token)
        assert masked == "12345678...STUVWXYZ"
        assert "..." in masked
    
    def test_mask_token_short(self):
        """Test masking a short token."""
        token = "short"
        masked = _mask_token(token)
        assert masked == "***"
    
    def test_mask_token_empty(self):
        """Test masking an empty token."""
        token = ""
        masked = _mask_token(token)
        assert masked == "***"


class TestRateLimiter:
    """Test SQLite-backed rate limiter."""
    
    @pytest.mark.asyncio
    async def test_get_paused_until_not_paused(self, temp_db):
        """Test getting pause state when not paused."""
        paused_until = await rate_limiter.get_paused_until("test_bot")
        assert paused_until is None
    
    @pytest.mark.asyncio
    async def test_set_and_get_paused_until(self, temp_db):
        """Test setting and getting pause state."""
        bot_key = "test_bot"
        future_time = time.time() + 30
        
        await rate_limiter.set_paused_until(bot_key, future_time)
        paused_until = await rate_limiter.get_paused_until(bot_key)
        
        assert paused_until is not None
        assert abs(paused_until - future_time) < 1  # Allow small timing difference
    
    @pytest.mark.asyncio
    async def test_paused_until_expires(self, temp_db):
        """Test that expired pause state is automatically cleaned up."""
        bot_key = "test_bot"
        past_time = time.time() - 10  # 10 seconds in the past
        
        await rate_limiter.set_paused_until(bot_key, past_time)
        paused_until = await rate_limiter.get_paused_until(bot_key)
        
        # Should return None because it's expired
        assert paused_until is None
    
    @pytest.mark.asyncio
    async def test_get_remaining_pause_seconds(self, temp_db):
        """Test getting remaining pause duration."""
        bot_key = "test_bot"
        future_time = time.time() + 30
        
        await rate_limiter.set_paused_until(bot_key, future_time)
        remaining = await rate_limiter.get_remaining_pause_seconds(bot_key)
        
        assert 25 < remaining <= 30  # Allow for execution time
    
    @pytest.mark.asyncio
    async def test_clear_pause(self, temp_db):
        """Test clearing pause state."""
        bot_key = "test_bot"
        future_time = time.time() + 30
        
        await rate_limiter.set_paused_until(bot_key, future_time)
        await rate_limiter.clear_pause(bot_key)
        
        paused_until = await rate_limiter.get_paused_until(bot_key)
        assert paused_until is None


class TestSendMessageWithRateHandling:
    """Test rate-aware message sending."""
    
    @pytest.mark.asyncio
    async def test_successful_send(self, temp_db):
        """Test successful message send."""
        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock()
        
        result = await send_message_with_rate_handling(
            bot=mock_bot,
            chat_id=12345,
            text="Test message",
            bot_key="test_bot"
        )
        
        assert result["success"] is True
        assert result["error"] is None
        assert result["retry_after"] is None
        mock_bot.send_message.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_retry_after_handling(self, temp_db):
        """Test RetryAfter exception handling and pause state."""
        mock_bot = AsyncMock()
        
        # Use actual RetryAfter exception from side_effect
        call_count = [0]
        def side_effect_func(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # First call: raise RetryAfter
                raise RetryAfter(retry_after=10)
            # Second call: success
            return None
        
        mock_bot.send_message = AsyncMock(side_effect=side_effect_func)
        
        bot_key = "test_bot"
        
        # Should succeed after retry
        result = await send_message_with_rate_handling(
            bot=mock_bot,
            chat_id=12345,
            text="Test message",
            bot_key=bot_key,
            max_retries=2,
            base_backoff=0.1
        )
        
        assert result["success"] is True
        assert mock_bot.send_message.call_count == 2
    
    @pytest.mark.asyncio
    async def test_retry_after_max_retries(self, temp_db):
        """Test RetryAfter exceeding max retries."""
        mock_bot = AsyncMock()
        
        # Always raise RetryAfter
        mock_bot.send_message = AsyncMock(side_effect=RetryAfter(retry_after=10))
        
        result = await send_message_with_rate_handling(
            bot=mock_bot,
            chat_id=12345,
            text="Test message",
            bot_key="test_bot",
            max_retries=1,
            base_backoff=0.1
        )
        
        assert result["success"] is False
        assert "RetryAfter" in result["error"]
        assert result["retry_after"] == 10
    
    @pytest.mark.asyncio
    async def test_timed_out_with_retry(self, temp_db):
        """Test TimedOut with exponential backoff."""
        mock_bot = AsyncMock()
        
        # First call times out, second succeeds
        mock_bot.send_message = AsyncMock(side_effect=[TimedOut(), None])
        
        result = await send_message_with_rate_handling(
            bot=mock_bot,
            chat_id=12345,
            text="Test message",
            bot_key="test_bot",
            max_retries=2,
            base_backoff=0.1
        )
        
        assert result["success"] is True
        assert mock_bot.send_message.call_count == 2
    
    @pytest.mark.asyncio
    async def test_network_error_with_retry(self, temp_db):
        """Test NetworkError with exponential backoff."""
        mock_bot = AsyncMock()
        
        # First call has network error, second succeeds
        mock_bot.send_message = AsyncMock(side_effect=[NetworkError("Connection lost"), None])
        
        result = await send_message_with_rate_handling(
            bot=mock_bot,
            chat_id=12345,
            text="Test message",
            bot_key="test_bot",
            max_retries=2,
            base_backoff=0.1
        )
        
        assert result["success"] is True
        assert mock_bot.send_message.call_count == 2
    
    @pytest.mark.asyncio
    async def test_forbidden_no_retry(self, temp_db):
        """Test Forbidden error doesn't retry."""
        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock(side_effect=Forbidden("Bot was blocked"))
        
        result = await send_message_with_rate_handling(
            bot=mock_bot,
            chat_id=12345,
            text="Test message",
            bot_key="test_bot",
            max_retries=3
        )
        
        assert result["success"] is False
        assert "Forbidden" in result["error"] or "blocked" in result["error"]
        # Should only call once, no retries for permanent errors
        assert mock_bot.send_message.call_count == 1
    
    @pytest.mark.asyncio
    async def test_bad_request_no_retry(self, temp_db):
        """Test BadRequest error doesn't retry."""
        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock(side_effect=BadRequest("Message too long"))
        
        result = await send_message_with_rate_handling(
            bot=mock_bot,
            chat_id=12345,
            text="Test message",
            bot_key="test_bot",
            max_retries=3
        )
        
        assert result["success"] is False
        assert "Message too long" in result["error"]
        # Should only call once, no retries for permanent errors
        assert mock_bot.send_message.call_count == 1
    
    @pytest.mark.asyncio
    async def test_respects_pause_state(self, temp_db):
        """Test that send respects existing pause state."""
        mock_bot = AsyncMock()
        mock_bot.send_message = AsyncMock()
        
        bot_key = "test_bot"
        
        # Set pause state for 1 second
        await rate_limiter.set_paused_until(bot_key, time.time() + 1)
        
        start_time = time.time()
        result = await send_message_with_rate_handling(
            bot=mock_bot,
            chat_id=12345,
            text="Test message",
            bot_key=bot_key
        )
        elapsed_time = time.time() - start_time
        
        # Should have waited at least 0.9 seconds (allow some timing variance)
        assert elapsed_time >= 0.9
        assert result["success"] is True


class TestDatabaseHelpers:
    """Test new database helper methods."""
    
    @pytest.mark.asyncio
    async def test_get_user(self, temp_db):
        """Test get_user method."""
        user_id = 12345
        await temp_db.track_user(user_id, "testuser", "Test", "User", "testbot")
        
        user = await temp_db.get_user(user_id)
        assert user is not None
        assert user["user_id"] == user_id
        assert user["username"] == "testuser"
        assert "started_at" in user  # Should have started_at set
    
    @pytest.mark.asyncio
    async def test_get_user_not_found(self, temp_db):
        """Test get_user for non-existent user."""
        user = await temp_db.get_user(99999)
        assert user is None
    
    @pytest.mark.asyncio
    async def test_increment_custom_message_retry_count(self, temp_db):
        """Test incrementing retry count for a message."""
        user_id = 12345
        await temp_db.track_user(user_id, "testuser", "Test", "User", "testbot")
        
        # Queue a message
        await temp_db.queue_custom_message(user_id, "Test message")
        
        # Get the message
        messages = await temp_db.get_pending_custom_messages(limit=1)
        assert len(messages) == 1
        message_id = messages[0]["id"]
        
        # Initial retry_count should be 0
        assert messages[0]["retry_count"] == 0
        
        # Increment retry count
        await temp_db.increment_custom_message_retry_count(message_id)
        
        # Check it was incremented
        messages = await temp_db.get_pending_custom_messages(limit=1)
        assert messages[0]["retry_count"] == 1
        
        # Increment again
        await temp_db.increment_custom_message_retry_count(message_id)
        messages = await temp_db.get_pending_custom_messages(limit=1)
        assert messages[0]["retry_count"] == 2
    
    @pytest.mark.asyncio
    async def test_queued_message_has_enqueued_at(self, temp_db):
        """Test that queued messages have enqueued_at set."""
        user_id = 12345
        await temp_db.track_user(user_id, "testuser", "Test", "User", "testbot")
        
        # Queue a message
        await temp_db.queue_custom_message(user_id, "Test message")
        
        # Get the message
        messages = await temp_db.get_pending_custom_messages(limit=1)
        assert len(messages) == 1
        assert messages[0]["enqueued_at"] is not None
        assert messages[0]["retry_count"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
