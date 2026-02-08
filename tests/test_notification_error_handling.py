"""
Unit tests for notification error handling.

Tests the error handling for:
- Chat not found (BadRequest) errors
- Forbidden errors (user blocked bot)
- Rate limiting with retry logic
- User marking as invalid/blocked
"""
import os
import sys
import pytest
import pytest_asyncio
import asyncio
import tempfile
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from telegram.error import BadRequest, Forbidden, TelegramError

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from utils.notifications import NotificationService


@pytest_asyncio.fixture
async def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name
    
    db = Database(db_path)
    await db.init_db()
    
    yield db
    
    # Cleanup
    try:
        os.unlink(db_path)
    except OSError:
        pass


@pytest_asyncio.fixture
async def notification_service(temp_db):
    """Create a notification service instance."""
    return NotificationService(temp_db)


class TestChatNotFoundHandling:
    """Test handling of chat not found errors."""
    
    @pytest.mark.asyncio
    async def test_mark_user_invalid_global(self, temp_db):
        """Test that mark_user_invalid without bot_id does global blocking (backward compatibility)."""
        # Create a test user
        user_id = 12345
        await temp_db.track_user(user_id, "testuser", "Test", "User", "testbot")
        
        # Enable notifications first
        await temp_db.set_user_notifications(user_id, True)
        
        # Verify user is not blocked initially
        is_blocked = await temp_db.is_user_blocked(user_id)
        assert not is_blocked
        
        # Mark user as invalid WITHOUT bot_id (global block)
        await temp_db.mark_user_invalid(user_id)
        
        # Verify user is now globally blocked
        is_blocked = await temp_db.is_user_blocked(user_id)
        assert is_blocked
        
        # Verify notifications are disabled
        import aiosqlite
        async with aiosqlite.connect(temp_db.db_path) as db:
            async with db.execute(
                "SELECT notifications_enabled FROM bot_users WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                assert row is not None
                assert row[0] == 0  # notifications_enabled should be 0 (False)
    
    @pytest.mark.asyncio
    async def test_mark_user_invalid_bot_specific(self, temp_db):
        """Test that mark_user_invalid with bot_id only blocks for that bot."""
        # Create a test user
        user_id = 12346
        await temp_db.track_user(user_id, "testuser2", "Test", "User2", "testbot")
        
        # Mark user as invalid for specific bot
        await temp_db.mark_user_invalid(user_id, bot_id="BOT_TOKEN_1", reason="chat_not_found")
        
        # Verify user is NOT globally blocked
        is_blocked = await temp_db.is_user_blocked(user_id)
        assert not is_blocked
        
        # Verify user IS invalid for BOT_TOKEN_1
        is_invalid = await temp_db.is_user_invalid_for_bot(user_id, "BOT_TOKEN_1")
        assert is_invalid
        
        # Verify user is NOT invalid for other bots
        is_invalid_other = await temp_db.is_user_invalid_for_bot(user_id, "BOT_TOKEN")
        assert not is_invalid_other
    
    @pytest.mark.asyncio
    async def test_mark_user_valid_revalidation(self, temp_db):
        """Test that mark_user_valid removes bot-specific invalidation."""
        # Create a test user
        user_id = 12347
        await temp_db.track_user(user_id, "testuser3", "Test", "User3", "testbot")
        
        # Mark user as invalid for specific bot
        await temp_db.mark_user_invalid(user_id, bot_id="BOT_TOKEN", reason="chat_not_found")
        
        # Verify user is invalid
        is_invalid = await temp_db.is_user_invalid_for_bot(user_id, "BOT_TOKEN")
        assert is_invalid
        
        # Revalidate user
        await temp_db.mark_user_valid(user_id, "BOT_TOKEN")
        
        # Verify user is no longer invalid
        is_invalid = await temp_db.is_user_invalid_for_bot(user_id, "BOT_TOKEN")
        assert not is_invalid
    
    @pytest.mark.asyncio
    async def test_custom_message_chat_not_found_marks_invalid_bot_specific(self, temp_db, notification_service):
        """Test that chat not found errors mark user as invalid for specific bot only."""
        # Create a test user
        user_id = 12348
        await temp_db.track_user(user_id, "testuser4", "Test", "User4", "testbot")
        
        # Queue a custom message
        await temp_db.queue_custom_message(user_id, "Test message")
        
        # Get the queued message
        messages = await temp_db.get_pending_custom_messages(limit=1)
        assert len(messages) == 1
        message = messages[0]
        
        # Create mock context
        mock_context = MagicMock()
        mock_bot = MagicMock()
        mock_bot.username = "testbot"
        mock_bot.token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"  # Mock token
        mock_bot.send_message = AsyncMock(
            side_effect=BadRequest("Chat not found")
        )
        mock_context.bot = mock_bot
        
        # Mock get_bot_env_key to return a specific bot key
        with patch('utils.notifications.get_bot_env_key', return_value='BOT_TOKEN'):
            # Send the message (should handle error)
            result = await notification_service._send_single_custom_message(
                mock_context, message
            )
        
        # Verify failure with correct reason
        assert not result["success"]
        assert result["reason"] == "chat_not_found"
        
        # Verify user was marked as invalid for BOT_TOKEN specifically
        is_invalid = await temp_db.is_user_invalid_for_bot(user_id, "BOT_TOKEN")
        assert is_invalid
        
        # Verify user is NOT globally blocked
        is_blocked = await temp_db.is_user_blocked(user_id)
        assert not is_blocked
    
    @pytest.mark.asyncio
    async def test_notification_chat_not_found_marks_invalid_bot_specific(self, temp_db, notification_service):
        """Test that chat not found in notification batch marks user as invalid for specific bot only."""
        # Create a test user
        user_id = 12349
        await temp_db.track_user(user_id, "testuser5", "Test", "User5", "testbot")
        
        # Create a test product
        product_id = await temp_db.add_product(
            file_id="test_file",
            file_type="photo",
            message_id=999,
            chat_id=-100,
            caption="Test product"
        )
        
        # Set product category
        await temp_db.update_product_category(product_id, "Electronics", None)
        
        # Queue a notification
        await temp_db.queue_notification(user_id, product_id)
        
        # Get the notification
        notifications = await temp_db.get_pending_notifications(limit=1)
        assert len(notifications) == 1
        
        # Create mock context with BadRequest error
        mock_context = MagicMock()
        mock_bot = MagicMock()
        mock_bot.username = "testbot"
        mock_bot.token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"  # Mock token
        mock_bot.send_message = AsyncMock(
            side_effect=BadRequest("User not found")
        )
        mock_context.bot = mock_bot
        
        # Mock get_bot_env_key to return a specific bot key
        with patch('utils.notifications.get_bot_env_key', return_value='BOT_TOKEN_1'):
            # Send the notification batch
            sent_count = await notification_service._send_notification_batch(
                mock_context, notifications
            )
        
        # Verify no messages were sent
        assert sent_count == 0
        
        # Verify user was marked as invalid for BOT_TOKEN_1 specifically
        is_invalid = await temp_db.is_user_invalid_for_bot(user_id, "BOT_TOKEN_1")
        assert is_invalid
        
        # Verify user is NOT globally blocked
        is_blocked = await temp_db.is_user_blocked(user_id)
        assert not is_blocked


class TestForbiddenHandling:
    """Test handling of Forbidden errors (user blocked bot)."""
    
    @pytest.mark.asyncio
    async def test_custom_message_forbidden_blocks_user(self, temp_db, notification_service):
        """Test that Forbidden errors block the user."""
        # Create a test user
        user_id = 12348
        await temp_db.track_user(user_id, "testuser4", "Test", "User4", "testbot")
        
        # Queue a custom message
        await temp_db.queue_custom_message(user_id, "Test message")
        messages = await temp_db.get_pending_custom_messages(limit=1)
        message = messages[0]
        
        # Create mock context with Forbidden error
        mock_context = MagicMock()
        mock_bot = MagicMock()
        mock_bot.username = "testbot"
        mock_bot.send_message = AsyncMock(side_effect=Forbidden("User blocked bot"))
        mock_context.bot = mock_bot
        
        # Send the message
        result = await notification_service._send_single_custom_message(
            mock_context, message
        )
        
        # Verify failure
        assert not result["success"]
        assert result["reason"] == "forbidden"
        
        # Verify user was blocked
        is_blocked = await temp_db.is_user_blocked(user_id)
        assert is_blocked
    
    @pytest.mark.asyncio
    async def test_notification_forbidden_disables_notifications(self, temp_db, notification_service):
        """Test that Forbidden in notifications disables user notifications."""
        # Create a test user with notifications enabled
        user_id = 12349
        await temp_db.track_user(user_id, "testuser5", "Test", "User5", "testbot")
        await temp_db.set_user_notifications(user_id, True)
        
        # Create a test product
        product_id = await temp_db.add_product(
            file_id="test_file2",
            file_type="photo",
            message_id=1000,
            chat_id=-100,
            caption="Test product 2"
        )
        await temp_db.update_product_category(product_id, "Books", None)
        
        # Queue a notification
        await temp_db.queue_notification(user_id, product_id)
        notifications = await temp_db.get_pending_notifications(limit=1)
        
        # Create mock context with Forbidden error
        mock_context = MagicMock()
        mock_bot = MagicMock()
        mock_bot.username = "testbot"
        mock_bot.send_message = AsyncMock(side_effect=Forbidden("Bot was blocked"))
        mock_context.bot = mock_bot
        
        # Send the notification
        sent_count = await notification_service._send_notification_batch(
            mock_context, notifications
        )
        
        # Verify no messages sent
        assert sent_count == 0
        
        # Verify notifications were disabled (check database directly)
        import aiosqlite
        async with aiosqlite.connect(temp_db.db_path) as db:
            async with db.execute(
                "SELECT notifications_enabled FROM bot_users WHERE user_id = ?",
                (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                assert row is not None
                assert row[0] == 0  # Should be disabled


class TestRateLimitRetry:
    """Test rate limit error handling with retry logic."""
    
    @pytest.mark.asyncio
    async def test_rate_limit_retry_with_exponential_backoff(self, temp_db, notification_service):
        """Test that rate limit errors trigger retry with exponential backoff."""
        user_id = 12350
        await temp_db.track_user(user_id, "testuser6", "Test", "User6", "testbot")
        
        # Queue a custom message
        await temp_db.queue_custom_message(user_id, "Test message")
        messages = await temp_db.get_pending_custom_messages(limit=1)
        message = messages[0]
        
        # Create mock that fails twice with rate limit, then succeeds
        call_count = 0
        async def mock_send(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise TelegramError("Too Many Requests: retry after 5")
            return MagicMock()  # Success on third try
        
        mock_context = MagicMock()
        mock_bot = MagicMock()
        mock_bot.username = "testbot"
        mock_bot.send_message = mock_send
        mock_context.bot = mock_bot
        
        # Send the message (should retry and eventually succeed)
        result = await notification_service._send_single_custom_message(
            mock_context, message
        )
        
        # Verify success after retries
        assert result["success"]
        assert call_count == 3  # Should have tried 3 times
    
    @pytest.mark.asyncio
    async def test_rate_limit_max_retries_exceeded(self, temp_db, notification_service):
        """Test that max retries limit is respected."""
        user_id = 12351
        await temp_db.track_user(user_id, "testuser7", "Test", "User7", "testbot")
        
        # Queue a custom message
        await temp_db.queue_custom_message(user_id, "Test message")
        messages = await temp_db.get_pending_custom_messages(limit=1)
        message = messages[0]
        
        # Create mock that always fails with rate limit
        mock_context = MagicMock()
        mock_bot = MagicMock()
        mock_bot.username = "testbot"
        mock_bot.send_message = AsyncMock(
            side_effect=TelegramError("Too Many Requests: retry after 5")
        )
        mock_context.bot = mock_bot
        
        # Send the message (should fail after max retries)
        result = await notification_service._send_single_custom_message(
            mock_context, message
        )
        
        # Verify failure after max retries
        assert not result["success"]
        assert result["reason"] == "rate_limit_max_retries"


class TestOtherBadRequestErrors:
    """Test handling of other BadRequest errors (non-chat-not-found)."""
    
    @pytest.mark.asyncio
    async def test_bad_request_other_error_marks_sent(self, temp_db, notification_service):
        """Test that other BadRequest errors (e.g., message too long) mark message as sent."""
        user_id = 12352
        await temp_db.track_user(user_id, "testuser8", "Test", "User8", "testbot")
        
        # Queue a custom message
        await temp_db.queue_custom_message(user_id, "Test message")
        messages = await temp_db.get_pending_custom_messages(limit=1)
        message = messages[0]
        
        # Create mock with other BadRequest error
        mock_context = MagicMock()
        mock_bot = MagicMock()
        mock_bot.username = "testbot"
        mock_bot.send_message = AsyncMock(
            side_effect=BadRequest("Message is too long")
        )
        mock_context.bot = mock_bot
        
        # Send the message
        result = await notification_service._send_single_custom_message(
            mock_context, message
        )
        
        # Verify failure with bad_request reason
        assert not result["success"]
        assert result["reason"] == "bad_request"
        
        # Verify user was NOT marked as blocked (different from chat not found)
        is_blocked = await temp_db.is_user_blocked(user_id)
        assert not is_blocked


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
