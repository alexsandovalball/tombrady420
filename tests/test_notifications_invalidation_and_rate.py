"""
Unit tests for notification invalidation and rate limiting behavior.

Tests:
- User with recent_count >= MAX_CUSTOM_MESSAGES_PER_HOUR gets rate_limited failure
- mark_user_invalid creates bot-scoped invalidation (not global)
- Atomic mark helpers detect race conditions
"""
import os
import sys
import pytest
import pytest_asyncio
import asyncio
import tempfile
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime, timedelta

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from utils.notifications import NotificationService, BotContext, MAX_CUSTOM_MESSAGES_PER_HOUR
from configs.config import Config


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
def mock_application():
    """Create a mock Telegram Application for testing."""
    app = MagicMock()
    app.bot = MagicMock()
    app.bot.username = "test_bot"
    app.bot.token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    return app


@pytest_asyncio.fixture
def mock_context(mock_application):
    """Create a mock context for testing."""
    context = MagicMock()
    context.bot = mock_application.bot
    context.bot_data = {}
    return context


class TestRateLimitBehavior:
    """Test rate limiting behavior for custom messages."""
    
    @pytest.mark.asyncio
    async def test_user_exceeds_rate_limit_gets_rate_limited_failure(self, temp_db, mock_context):
        """Test that user exceeding rate limit gets rate_limited failure reason."""
        user_id = 12345
        bot_username = "test_bot"
        
        # Register user
        await temp_db.track_user(user_id, username="test_user", first_name="Test User", 
                                 bot_username=bot_username)
        
        # Create MAX_CUSTOM_MESSAGES_PER_HOUR + 5 sent messages in the last hour
        for i in range(MAX_CUSTOM_MESSAGES_PER_HOUR + 5):
            await temp_db.queue_custom_message(
                user_id=user_id,
                message_text=f"Past message {i}",
                target_bot_key="BOT_TOKEN"
            )
            # Mark them as sent
            message = (await temp_db.get_pending_custom_messages(limit=1))[0]
            await temp_db.mark_custom_message_sent(message["id"])
        
        # Verify recent count
        recent_count = await temp_db.get_recent_custom_messages_count(user_id, minutes=60)
        assert recent_count >= MAX_CUSTOM_MESSAGES_PER_HOUR
        
        # Queue a new message
        await temp_db.queue_custom_message(
            user_id=user_id,
            message_text="New message that should be rate limited",
            target_bot_key="BOT_TOKEN"
        )
        
        # Create notification service and process queue
        service = NotificationService(temp_db)
        
        # Mock get_bot_env_key to return consistent value
        with patch('utils.notifications.get_bot_env_key', return_value='BOT_TOKEN'):
            result = await service.process_custom_message_queue(mock_context)
        
        # Verify the message was rate limited
        assert result["failed_count"] > 0
        assert "rate_limited" in result["failure_reasons"]
        assert result["failure_reasons"]["rate_limited"] >= 1
        assert user_id in result["failed_user_ids"]
    
    @pytest.mark.asyncio
    async def test_user_within_rate_limit_processes_normally(self, temp_db, mock_context):
        """Test that user within rate limit gets processed normally."""
        user_id = 12346
        bot_username = "test_bot"
        
        # Register user
        await temp_db.track_user(user_id, username="test_user2", first_name="Test User 2",
                                 bot_username=bot_username)
        
        # Create only half the rate limit in sent messages
        for i in range(MAX_CUSTOM_MESSAGES_PER_HOUR // 2):
            await temp_db.queue_custom_message(
                user_id=user_id,
                message_text=f"Past message {i}",
                target_bot_key="BOT_TOKEN"
            )
            message = (await temp_db.get_pending_custom_messages(limit=1))[0]
            await temp_db.mark_custom_message_sent(message["id"])
        
        # Verify recent count is below limit
        recent_count = await temp_db.get_recent_custom_messages_count(user_id, minutes=60)
        assert recent_count < MAX_CUSTOM_MESSAGES_PER_HOUR
        
        # Queue a new message
        await temp_db.queue_custom_message(
            user_id=user_id,
            message_text="New message that should be processed",
            target_bot_key="BOT_TOKEN"
        )
        
        # Create notification service
        service = NotificationService(temp_db)
        
        # Mock send to succeed
        with patch('utils.notifications.send_message_with_rate_handling', 
                   new_callable=AsyncMock) as mock_send:
            mock_send.return_value = {"success": True}
            
            with patch('utils.notifications.get_bot_env_key', return_value='BOT_TOKEN'):
                result = await service.process_custom_message_queue(mock_context)
        
        # Verify the message was attempted (not rate limited)
        # It should have been sent or at least attempted
        assert result["failed_count"] == 0 or "rate_limited" not in result.get("failure_reasons", {})


class TestBotScopedInvalidation:
    """Test bot-scoped user invalidation."""
    
    @pytest.mark.asyncio
    async def test_mark_user_invalid_creates_bot_scoped_record(self, temp_db):
        """Test that mark_user_invalid with bot_id creates bot-scoped invalidation."""
        user_id = 12347
        bot_id_1 = "BOT_TOKEN"
        bot_id_2 = "BOT_TOKEN_2"
        
        # Mark user invalid for bot 1
        await temp_db.mark_user_invalid(user_id, bot_id=bot_id_1, reason="chat_not_found")
        
        # Verify user is invalid for bot 1
        is_invalid_bot1 = await temp_db.is_user_invalid_for_bot(user_id, bot_id_1)
        assert is_invalid_bot1 is True
        
        # Verify user is NOT invalid for bot 2
        is_invalid_bot2 = await temp_db.is_user_invalid_for_bot(user_id, bot_id_2)
        assert is_invalid_bot2 is False
        
        # Verify user is NOT globally blocked
        is_blocked = await temp_db.is_user_blocked(user_id)
        assert is_blocked is False
    
    @pytest.mark.asyncio
    async def test_get_user_invalidation_record_returns_details(self, temp_db):
        """Test that get_user_invalidation_record returns invalidation details."""
        user_id = 12348
        bot_id = "BOT_TOKEN"
        reason = "chat_not_found"
        
        # Mark user invalid
        await temp_db.mark_user_invalid(user_id, bot_id=bot_id, reason=reason)
        
        # Get invalidation record
        record = await temp_db.get_user_invalidation_record(user_id, bot_id=bot_id)
        
        # Verify record details
        assert record is not None
        assert record["user_id"] == user_id
        assert record["bot_id"] == bot_id
        assert record["reason"] == reason
        assert record["is_active"] == 1
        assert "created_at" in record
    
    @pytest.mark.asyncio
    async def test_mark_user_invalid_without_bot_id_creates_global_block(self, temp_db):
        """Test that mark_user_invalid without bot_id creates global block (backward compat)."""
        user_id = 12349
        
        # First track the user so they exist in bot_users table
        await temp_db.track_user(user_id, username="test_user_global", 
                                 first_name="Test Global", bot_username="test_bot")
        
        # Mark user invalid without bot_id
        await temp_db.mark_user_invalid(user_id, bot_id=None, reason="test_global_block")
        
        # Verify user is globally blocked
        is_blocked = await temp_db.is_user_blocked(user_id)
        assert is_blocked is True
    
    @pytest.mark.asyncio
    async def test_blocked_user_logs_invalidation_details(self, temp_db, mock_context, caplog):
        """Test that blocked user processing logs invalidation details."""
        import logging
        caplog.set_level(logging.INFO)
        
        user_id = 12350
        bot_id = "BOT_TOKEN"
        bot_username = "test_bot"
        
        # Register user
        await temp_db.track_user(user_id, username="test_user_log", first_name="Test User",
                                 bot_username=bot_username)
        
        # Mark user invalid with a reason
        await temp_db.mark_user_invalid(user_id, bot_id=bot_id, reason="chat_not_found")
        
        # Queue a message for this user
        await temp_db.queue_custom_message(
            user_id=user_id,
            message_text="Test message",
            target_bot_key=bot_id
        )
        
        # Mark user as globally blocked for this test
        await temp_db.block_user(user_id)
        
        # Create notification service and process
        service = NotificationService(temp_db)
        
        with patch('utils.notifications.get_bot_env_key', return_value=bot_id):
            result = await service.process_custom_message_queue(mock_context)
        
        # Verify blocked user was logged with invalidation details
        # Check that the log contains the user_id and bot_id
        log_messages = [record.message for record in caplog.records]
        has_invalidation_log = any(
            str(user_id) in msg and "blocked" in msg.lower()
            for msg in log_messages
        )
        assert has_invalidation_log


class TestAtomicMarkHelpers:
    """Test atomic mark helpers detect race conditions."""
    
    @pytest.mark.asyncio
    async def test_mark_custom_message_sent_atomic_returns_true_first_time(self, temp_db):
        """Test that atomic mark returns True on first marking."""
        user_id = 12351
        
        # Queue a message
        await temp_db.queue_custom_message(
            user_id=user_id,
            message_text="Test message",
            target_bot_key="BOT_TOKEN"
        )
        
        # Get the message
        messages = await temp_db.get_pending_custom_messages(limit=1)
        assert len(messages) == 1
        message_id = messages[0]["id"]
        
        # Mark as sent atomically - should succeed
        success = await temp_db.mark_custom_message_sent_atomic(message_id)
        assert success is True
    
    @pytest.mark.asyncio
    async def test_mark_custom_message_sent_atomic_returns_false_second_time(self, temp_db):
        """Test that atomic mark returns False on second marking (race detected)."""
        user_id = 12352
        
        # Queue a message
        await temp_db.queue_custom_message(
            user_id=user_id,
            message_text="Test message",
            target_bot_key="BOT_TOKEN"
        )
        
        # Get the message
        messages = await temp_db.get_pending_custom_messages(limit=1)
        assert len(messages) == 1
        message_id = messages[0]["id"]
        
        # Mark as sent atomically first time - should succeed
        success1 = await temp_db.mark_custom_message_sent_atomic(message_id)
        assert success1 is True
        
        # Try to mark as sent atomically again - should fail (already marked)
        success2 = await temp_db.mark_custom_message_sent_atomic(message_id)
        assert success2 is False
    
    @pytest.mark.asyncio
    async def test_mark_notification_sent_atomic_returns_true_first_time(self, temp_db):
        """Test that atomic mark returns True on first marking for notifications."""
        user_id = 12353
        product_id = 999  # Dummy product ID
        
        # Queue a notification (requires product_id)
        await temp_db.queue_notification(
            user_id=user_id,
            product_id=product_id,
            target_bot_key="BOT_TOKEN"
        )
        
        # Get the notification
        notifications = await temp_db.get_pending_notifications(limit=1)
        assert len(notifications) == 1
        notification_id = notifications[0]["id"]
        
        # Mark as sent atomically - should succeed
        success = await temp_db.mark_notification_sent_atomic(notification_id)
        assert success is True
    
    @pytest.mark.asyncio
    async def test_mark_notification_sent_atomic_returns_false_second_time(self, temp_db):
        """Test that atomic mark returns False on second marking for notifications."""
        user_id = 12354
        product_id = 1000  # Dummy product ID
        
        # Queue a notification (requires product_id)
        await temp_db.queue_notification(
            user_id=user_id,
            product_id=product_id,
            target_bot_key="BOT_TOKEN"
        )
        
        # Get the notification
        notifications = await temp_db.get_pending_notifications(limit=1)
        assert len(notifications) == 1
        notification_id = notifications[0]["id"]
        
        # Mark as sent atomically first time - should succeed
        success1 = await temp_db.mark_notification_sent_atomic(notification_id)
        assert success1 is True
        
        # Try to mark as sent atomically again - should fail (already marked)
        success2 = await temp_db.mark_notification_sent_atomic(notification_id)
        assert success2 is False
    
    @pytest.mark.asyncio
    async def test_concurrent_atomic_marks_only_one_succeeds(self, temp_db):
        """Test that concurrent atomic marks only allow one to succeed."""
        user_id = 12355
        
        # Queue a message
        await temp_db.queue_custom_message(
            user_id=user_id,
            message_text="Test message",
            target_bot_key="BOT_TOKEN"
        )
        
        # Get the message
        messages = await temp_db.get_pending_custom_messages(limit=1)
        assert len(messages) == 1
        message_id = messages[0]["id"]
        
        # Simulate concurrent marking from two bot instances
        results = await asyncio.gather(
            temp_db.mark_custom_message_sent_atomic(message_id),
            temp_db.mark_custom_message_sent_atomic(message_id),
            temp_db.mark_custom_message_sent_atomic(message_id)
        )
        
        # Only one should have succeeded
        success_count = sum(1 for r in results if r is True)
        assert success_count == 1
        
        # The other two should have failed
        failure_count = sum(1 for r in results if r is False)
        assert failure_count == 2
