"""
Tests for broadcast reliability with retry and fallback logic.

Tests the send_with_retry_and_fallback functionality to ensure:
1. Permanent failures trigger immediate fallback
2. Transient failures trigger retries then fallback
3. Success on primary bot doesn't use fallback
"""
import os
import sys
import pytest
import pytest_asyncio
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.send_utils import (
    send_with_retry_and_fallback,
    SendAttemptResult,
    is_permanent_error,
    PERMANENT_ERROR_PATTERNS
)
from telegram.error import (
    Forbidden,
    BadRequest,
    TimedOut,
    NetworkError,
    RetryAfter,
    TelegramError
)


class TestIsPermanentError:
    """Test permanent vs transient error detection."""
    
    def test_forbidden_is_permanent(self):
        """Test that Forbidden errors are permanent."""
        error = Forbidden("Bot was blocked by the user")
        assert is_permanent_error(error) is True
    
    def test_bad_request_chat_not_found_is_permanent(self):
        """Test that chat not found errors are permanent."""
        error = BadRequest("Chat not found")
        assert is_permanent_error(error) is True
    
    def test_bad_request_user_deactivated_is_permanent(self):
        """Test that user deactivated errors are permanent."""
        error = BadRequest("User is deactivated")
        assert is_permanent_error(error) is True
    
    def test_timed_out_is_transient(self):
        """Test that TimedOut errors are transient."""
        error = TimedOut("Request timed out")
        assert is_permanent_error(error) is False
    
    def test_network_error_is_transient(self):
        """Test that NetworkError is transient."""
        error = NetworkError("Connection failed")
        assert is_permanent_error(error) is False
    
    def test_retry_after_is_transient(self):
        """Test that RetryAfter errors are transient."""
        error = RetryAfter(30)
        assert is_permanent_error(error) is False
    
    def test_rate_limit_is_transient(self):
        """Test that rate limit errors are transient."""
        error = TelegramError("Too Many Requests: retry after 10")
        assert is_permanent_error(error) is False
    
    def test_unknown_error_is_permanent(self):
        """Test that unknown errors default to permanent."""
        error = Exception("Unknown error")
        assert is_permanent_error(error) is True


class TestSendAttemptResult:
    """Test SendAttemptResult class."""
    
    def test_success_result(self):
        """Test creating a success result."""
        result = SendAttemptResult(
            success=True,
            user_id=12345,
            bot_username="test_bot",
            bot_key="BOT_TOKEN",
            retry_count=0
        )
        
        assert result.success is True
        assert result.user_id == 12345
        assert result.bot_username == "test_bot"
        assert result.bot_key == "BOT_TOKEN"
        assert result.error is None
        assert result.is_permanent is False
        assert result.retry_count == 0
    
    def test_failure_result(self):
        """Test creating a failure result."""
        result = SendAttemptResult(
            success=False,
            user_id=12345,
            bot_username="test_bot",
            bot_key="BOT_TOKEN",
            error="Chat not found",
            is_permanent=True,
            retry_count=0
        )
        
        assert result.success is False
        assert result.error == "Chat not found"
        assert result.is_permanent is True
    
    def test_to_dict(self):
        """Test converting result to dictionary."""
        timestamp = datetime(2024, 1, 1, 12, 0, 0)
        result = SendAttemptResult(
            success=True,
            user_id=12345,
            bot_username="test_bot",
            bot_key="BOT_TOKEN",
            retry_count=2,
            timestamp=timestamp
        )
        
        result_dict = result.to_dict()
        
        assert result_dict["success"] is True
        assert result_dict["user_id"] == 12345
        assert result_dict["bot_username"] == "test_bot"
        assert result_dict["bot_key"] == "BOT_TOKEN"
        assert result_dict["retry_count"] == 2
        assert result_dict["timestamp"] == "2024-01-01T12:00:00"


class TestSendWithRetryAndFallback:
    """Test send_with_retry_and_fallback function."""
    
    @pytest.mark.asyncio
    async def test_success_on_primary_no_fallback(self):
        """Test successful send on primary bot doesn't use fallback."""
        # Create mock bots
        primary_bot = AsyncMock()
        primary_bot.username = "primary_bot"
        primary_bot.send_message = AsyncMock(return_value=Mock(message_id=123))
        
        fallback_bot = AsyncMock()
        fallback_bot.username = "fallback_bot"
        fallback_bot.send_message = AsyncMock()
        
        # Send message
        result = await send_with_retry_and_fallback(
            user_id=12345,
            message_text="Test message",
            primary_bot=primary_bot,
            primary_bot_key="BOT_TOKEN",
            fallback_bots=[(fallback_bot, "BOT_TOKEN_1")]
        )
        
        # Verify success on primary
        assert result.success is True
        assert result.bot_username == "primary_bot"
        assert result.bot_key == "BOT_TOKEN"
        assert result.retry_count == 0
        
        # Verify primary was called
        primary_bot.send_message.assert_called_once()
        
        # Verify fallback was NOT called
        fallback_bot.send_message.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_permanent_failure_immediate_fallback(self):
        """Test permanent failure on primary triggers immediate fallback."""
        # Primary bot fails with permanent error
        primary_bot = AsyncMock()
        primary_bot.username = "primary_bot"
        primary_bot.send_message = AsyncMock(
            side_effect=Forbidden("Bot was blocked by the user")
        )
        
        # Fallback bot succeeds
        fallback_bot = AsyncMock()
        fallback_bot.username = "fallback_bot"
        fallback_bot.send_message = AsyncMock(return_value=Mock(message_id=456))
        
        # Send message
        result = await send_with_retry_and_fallback(
            user_id=12345,
            message_text="Test message",
            primary_bot=primary_bot,
            primary_bot_key="BOT_TOKEN",
            fallback_bots=[(fallback_bot, "BOT_TOKEN_1")],
            max_retries=3
        )
        
        # Verify success on fallback
        assert result.success is True
        assert result.bot_username == "fallback_bot"
        assert result.bot_key == "BOT_TOKEN_1"
        
        # Verify primary was called only once (no retries for permanent error)
        assert primary_bot.send_message.call_count == 1
        
        # Verify fallback was called once
        assert fallback_bot.send_message.call_count == 1
    
    @pytest.mark.asyncio
    async def test_transient_failure_retry_then_success(self):
        """Test transient failure triggers retry then succeeds."""
        # Primary bot fails twice, then succeeds
        primary_bot = AsyncMock()
        primary_bot.username = "primary_bot"
        primary_bot.send_message = AsyncMock(
            side_effect=[
                TimedOut("Timed out"),
                TimedOut("Timed out"),
                Mock(message_id=789)
            ]
        )
        
        fallback_bot = AsyncMock()
        fallback_bot.username = "fallback_bot"
        fallback_bot.send_message = AsyncMock()
        
        # Send message
        result = await send_with_retry_and_fallback(
            user_id=12345,
            message_text="Test message",
            primary_bot=primary_bot,
            primary_bot_key="BOT_TOKEN",
            fallback_bots=[(fallback_bot, "BOT_TOKEN_1")],
            max_retries=3,
            retry_base_delay=0.01  # Fast retry for testing
        )
        
        # Verify success on primary after retries
        assert result.success is True
        assert result.bot_username == "primary_bot"
        assert result.bot_key == "BOT_TOKEN"
        assert result.retry_count == 2  # Failed twice, succeeded on third
        
        # Verify primary was called 3 times (initial + 2 retries)
        assert primary_bot.send_message.call_count == 3
        
        # Verify fallback was NOT called
        fallback_bot.send_message.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_transient_failure_max_retries_then_fallback(self):
        """Test transient failure exhausts retries then uses fallback."""
        # Primary bot always fails with transient error
        primary_bot = AsyncMock()
        primary_bot.username = "primary_bot"
        primary_bot.send_message = AsyncMock(
            side_effect=TimedOut("Timed out")
        )
        
        # Fallback bot succeeds
        fallback_bot = AsyncMock()
        fallback_bot.username = "fallback_bot"
        fallback_bot.send_message = AsyncMock(return_value=Mock(message_id=999))
        
        # Send message
        result = await send_with_retry_and_fallback(
            user_id=12345,
            message_text="Test message",
            primary_bot=primary_bot,
            primary_bot_key="BOT_TOKEN",
            fallback_bots=[(fallback_bot, "BOT_TOKEN_1")],
            max_retries=2,
            retry_base_delay=0.01  # Fast retry for testing
        )
        
        # Verify success on fallback
        assert result.success is True
        assert result.bot_username == "fallback_bot"
        assert result.bot_key == "BOT_TOKEN_1"
        
        # Verify primary was called max_retries + 1 times
        assert primary_bot.send_message.call_count == 3  # initial + 2 retries
        
        # Verify fallback was called once
        assert fallback_bot.send_message.call_count == 1
    
    @pytest.mark.asyncio
    async def test_all_bots_fail(self):
        """Test when all bots (primary + fallbacks) fail."""
        # All bots fail
        primary_bot = AsyncMock()
        primary_bot.username = "primary_bot"
        primary_bot.send_message = AsyncMock(
            side_effect=Forbidden("Blocked")
        )
        
        fallback_bot1 = AsyncMock()
        fallback_bot1.username = "fallback_bot1"
        fallback_bot1.send_message = AsyncMock(
            side_effect=Forbidden("Blocked")
        )
        
        fallback_bot2 = AsyncMock()
        fallback_bot2.username = "fallback_bot2"
        fallback_bot2.send_message = AsyncMock(
            side_effect=Forbidden("Blocked")
        )
        
        # Send message
        result = await send_with_retry_and_fallback(
            user_id=12345,
            message_text="Test message",
            primary_bot=primary_bot,
            primary_bot_key="BOT_TOKEN",
            fallback_bots=[
                (fallback_bot1, "BOT_TOKEN_1"),
                (fallback_bot2, "BOT_TOKEN_2")
            ]
        )
        
        # Verify failure
        assert result.success is False
        assert "All bots failed" in result.error
        
        # Verify all bots were tried
        primary_bot.send_message.assert_called_once()
        fallback_bot1.send_message.assert_called_once()
        fallback_bot2.send_message.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_exponential_backoff(self):
        """Test that retries use exponential backoff."""
        # Primary bot fails with transient errors
        primary_bot = AsyncMock()
        primary_bot.username = "primary_bot"
        primary_bot.send_message = AsyncMock(
            side_effect=[
                NetworkError("Network error"),
                NetworkError("Network error"),
                NetworkError("Network error"),
                Mock(message_id=111)
            ]
        )
        
        # Track sleep calls
        with patch('asyncio.sleep') as mock_sleep:
            mock_sleep.return_value = asyncio.Future()
            mock_sleep.return_value.set_result(None)
            
            result = await send_with_retry_and_fallback(
                user_id=12345,
                message_text="Test message",
                primary_bot=primary_bot,
                primary_bot_key="BOT_TOKEN",
                max_retries=3,
                retry_base_delay=1.0
            )
            
            # Verify exponential backoff delays
            # First retry: 1.0s, Second retry: 2.0s, Third retry: 4.0s
            assert mock_sleep.call_count == 3
            calls = [call[0][0] for call in mock_sleep.call_args_list]
            assert calls[0] == 1.0  # 2^0 * 1.0
            assert calls[1] == 2.0  # 2^1 * 1.0
            assert calls[2] == 4.0  # 2^2 * 1.0
    
    @pytest.mark.asyncio
    async def test_max_retry_delay_cap(self):
        """Test that retry delay is capped at max_retry_delay."""
        # Primary bot fails with transient errors
        primary_bot = AsyncMock()
        primary_bot.username = "primary_bot"
        primary_bot.send_message = AsyncMock(side_effect=NetworkError("Network error"))
        
        # Track sleep calls
        with patch('asyncio.sleep') as mock_sleep:
            mock_sleep.return_value = asyncio.Future()
            mock_sleep.return_value.set_result(None)
            
            result = await send_with_retry_and_fallback(
                user_id=12345,
                message_text="Test message",
                primary_bot=primary_bot,
                primary_bot_key="BOT_TOKEN",
                max_retries=10,  # Many retries
                retry_base_delay=10.0,
                max_retry_delay=15.0  # Cap at 15 seconds
            )
            
            # Verify all delays are capped at max_retry_delay
            calls = [call[0][0] for call in mock_sleep.call_args_list]
            assert all(delay <= 15.0 for delay in calls)
    
    @pytest.mark.asyncio
    async def test_rate_limit_error_treated_as_transient(self):
        """Test that rate limit errors are treated as transient and retried."""
        # Primary bot rate limited, then succeeds
        primary_bot = AsyncMock()
        primary_bot.username = "primary_bot"
        primary_bot.send_message = AsyncMock(
            side_effect=[
                TelegramError("Too Many Requests: retry after 5"),
                Mock(message_id=222)
            ]
        )
        
        result = await send_with_retry_and_fallback(
            user_id=12345,
            message_text="Test message",
            primary_bot=primary_bot,
            primary_bot_key="BOT_TOKEN",
            max_retries=2,
            retry_base_delay=0.01
        )
        
        # Verify success after retry
        assert result.success is True
        assert result.retry_count == 1
        assert primary_bot.send_message.call_count == 2
    
    @pytest.mark.asyncio
    async def test_message_content_preserved(self):
        """Test that message content and formatting are preserved."""
        primary_bot = AsyncMock()
        primary_bot.username = "primary_bot"
        primary_bot.send_message = AsyncMock(return_value=Mock(message_id=333))
        
        message_text = "**Bold** and _italic_ text with [link](https://example.com)"
        
        result = await send_with_retry_and_fallback(
            user_id=12345,
            message_text=message_text,
            primary_bot=primary_bot,
            primary_bot_key="BOT_TOKEN",
            parse_mode="Markdown"
        )
        
        # Verify send_message was called with correct parameters
        primary_bot.send_message.assert_called_once_with(
            chat_id=12345,
            text=message_text,
            parse_mode="Markdown",
            reply_markup=None
        )
    
    @pytest.mark.asyncio
    async def test_reply_markup_preserved(self):
        """Test that reply markup is preserved across retries."""
        from telegram import InlineKeyboardMarkup, InlineKeyboardButton
        
        primary_bot = AsyncMock()
        primary_bot.username = "primary_bot"
        primary_bot.send_message = AsyncMock(return_value=Mock(message_id=444))
        
        markup = InlineKeyboardMarkup([[
            InlineKeyboardButton("Button 1", callback_data="btn1")
        ]])
        
        result = await send_with_retry_and_fallback(
            user_id=12345,
            message_text="Test with buttons",
            primary_bot=primary_bot,
            primary_bot_key="BOT_TOKEN",
            reply_markup=markup
        )
        
        # Verify reply_markup was passed
        call_kwargs = primary_bot.send_message.call_args[1]
        assert call_kwargs['reply_markup'] == markup
