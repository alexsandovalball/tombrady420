"""
Unit tests for the custom message background processor.

Tests the background processor that periodically processes queued custom messages
to ensure broadcasts and product notifications are delivered by all bot instances.
"""
import os
import sys
import pytest
import pytest_asyncio
import asyncio
import tempfile
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from types import SimpleNamespace

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from utils.notifications import (
    NotificationService,
    BotContext,
    start_custom_message_processor_in_background,
    _custom_message_processor_loop
)


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
    app.bot.send_message = AsyncMock()
    app.bot_data = {}
    return app


class TestBotContext:
    """Test the BotContext wrapper class."""
    
    def test_bot_context_provides_bot_attribute(self, mock_application):
        """Test that BotContext exposes the bot attribute."""
        context = BotContext(mock_application)
        
        assert hasattr(context, 'bot')
        assert context.bot == mock_application.bot
        assert context.bot.username == "test_bot"
    
    def test_bot_context_provides_bot_data(self, mock_application):
        """Test that BotContext exposes bot_data."""
        mock_application.bot_data['test_key'] = 'test_value'
        context = BotContext(mock_application)
        
        assert hasattr(context, 'bot_data')
        assert context.bot_data == mock_application.bot_data
        assert context.bot_data['test_key'] == 'test_value'


class TestCustomMessageProcessorStartup:
    """Test starting the background processor."""
    
    @pytest.mark.asyncio
    async def test_start_processor_creates_task(self, mock_application, temp_db):
        """Test that starting the processor creates an asyncio task."""
        task = start_custom_message_processor_in_background(mock_application, temp_db, interval_seconds=0.1)
        
        assert task is not None
        assert isinstance(task, asyncio.Task)
        
        # Clean up
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    
    @pytest.mark.asyncio
    async def test_processor_calls_process_custom_message_queue(self, mock_application, temp_db, monkeypatch):
        """Test that the processor calls NotificationService.process_custom_message_queue."""
        call_count = 0
        
        async def mock_process_queue(self, context, process_all_bots=False):
            nonlocal call_count
            call_count += 1
            return {
                "sent_count": 0,
                "failed_count": 0,
                "invalidated_count": 0,
                "failure_reasons": {},
                "failed_user_ids": []
            }
        
        # Monkeypatch the process method
        monkeypatch.setattr(
            NotificationService,
            "process_custom_message_queue",
            mock_process_queue
        )
        
        # Start processor with very short interval
        task = start_custom_message_processor_in_background(mock_application, temp_db, interval_seconds=0.05)
        
        # Wait for at least one call
        await asyncio.sleep(0.2)
        
        # Clean up
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        
        # Verify the method was called at least once
        assert call_count >= 1
    
    @pytest.mark.asyncio
    async def test_processor_handles_exceptions_gracefully(self, mock_application, temp_db, monkeypatch):
        """Test that the processor continues running after exceptions."""
        call_count = 0
        
        async def mock_process_queue_with_error(self, context, process_all_bots=False):
            nonlocal call_count
            call_count += 1
            
            # Raise error on first call, succeed on subsequent calls
            if call_count == 1:
                raise Exception("Test exception")
            
            return {
                "sent_count": 0,
                "failed_count": 0,
                "invalidated_count": 0,
                "failure_reasons": {},
                "failed_user_ids": []
            }
        
        # Monkeypatch the process method
        monkeypatch.setattr(
            NotificationService,
            "process_custom_message_queue",
            mock_process_queue_with_error
        )
        
        # Start processor
        task = start_custom_message_processor_in_background(mock_application, temp_db, interval_seconds=0.05)
        
        # Wait for multiple calls
        await asyncio.sleep(0.2)
        
        # Clean up
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        
        # Verify the method was called multiple times (continued after error)
        assert call_count >= 2
    
    @pytest.mark.asyncio
    async def test_processor_logs_summary(self, mock_application, temp_db, monkeypatch, caplog):
        """Test that the processor logs processing summary."""
        import logging
        
        async def mock_process_queue(self, context, process_all_bots=False):
            return {
                "sent_count": 5,
                "failed_count": 2,
                "invalidated_count": 1,
                "failure_reasons": {"blocked": 1, "rate_limited": 1},
                "failed_user_ids": []
            }
        
        # Monkeypatch the process method
        monkeypatch.setattr(
            NotificationService,
            "process_custom_message_queue",
            mock_process_queue
        )
        
        # Start processor with logging
        with caplog.at_level(logging.INFO):
            task = start_custom_message_processor_in_background(mock_application, temp_db, interval_seconds=0.05)
            
            # Wait for processing
            await asyncio.sleep(0.15)
            
            # Clean up
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        
        # Check that summary was logged
        log_text = caplog.text
        assert "CustomMessageProcessor" in log_text
        assert "Processed:" in log_text or "sent=" in log_text


class TestProcessorIntegration:
    """Integration tests for the processor with real database."""
    
    @pytest.mark.asyncio
    async def test_processor_with_queued_messages(self, mock_application, temp_db, monkeypatch):
        """Test processor processes real queued messages from database."""
        # Track what was processed
        processed_messages = []
        
        async def mock_send_message(chat_id, text, **kwargs):
            """Mock bot.send_message to track sends."""
            processed_messages.append((chat_id, text))
            return MagicMock()  # Return a mock message object
        
        mock_application.bot.send_message = mock_send_message
        
        # Add a test user to the database
        user_id = 123456
        await temp_db.track_user(user_id, "testuser", "Test", "User", "test_bot")
        await temp_db.set_user_notifications(user_id, True)
        
        # Queue a custom message with target_bot_key matching the current bot
        # We need to monkeypatch get_bot_env_key to return a known value
        def mock_get_bot_env_key():
            return "BOT_TOKEN"
        
        monkeypatch.setattr("utils.bot_key.get_bot_env_key", mock_get_bot_env_key)
        
        # Queue the message with matching target_bot_key
        await temp_db.queue_custom_message(
            user_id=user_id,
            message_text="Test broadcast message",
            target_bot_key="BOT_TOKEN"
        )
        
        # Start processor
        task = start_custom_message_processor_in_background(mock_application, temp_db, interval_seconds=0.05)
        
        # Wait for processing
        await asyncio.sleep(0.2)
        
        # Clean up
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        
        # Verify message was sent
        assert len(processed_messages) >= 1
        assert any(user_id == msg[0] for msg in processed_messages)
    
    @pytest.mark.asyncio
    async def test_processor_respects_bot_key_filtering(self, mock_application, temp_db, monkeypatch):
        """Test that processor only processes messages for its bot."""
        processed_count = 0
        
        async def counting_process_queue(self, context, process_all_bots=False):
            nonlocal processed_count
            
            # Call the real method to test filtering
            result = await NotificationService.process_custom_message_queue.__wrapped__(
                self, context, process_all_bots
            )
            
            processed_count += result.get("sent_count", 0)
            return result
        
        # Queue messages for different bots
        user1 = 111111
        user2 = 222222
        await temp_db.track_user(user1, "user1", "User", "One", "bot1")
        await temp_db.track_user(user2, "user2", "User", "Two", "bot2")
        
        # Message for BOT_TOKEN (our bot)
        await temp_db.queue_custom_message(user1, "Message 1", target_bot_key="BOT_TOKEN")
        
        # Message for different bot
        await temp_db.queue_custom_message(user2, "Message 2", target_bot_key="BOT_TOKEN_2")
        
        # Mock get_bot_env_key to return BOT_TOKEN
        def mock_get_bot_env_key():
            return "BOT_TOKEN"
        
        monkeypatch.setattr("utils.bot_key.get_bot_env_key", mock_get_bot_env_key)
        
        # The processor should only process messages for BOT_TOKEN
        # For this test, we'll verify by checking the filtering in process_custom_message_queue
        service = NotificationService(temp_db)
        context = BotContext(mock_application)
        
        result = await service.process_custom_message_queue(context, process_all_bots=False)
        
        # With proper filtering, only messages for BOT_TOKEN should be in the pending list
        # The result should reflect that only 1 message was attempted (for user1)
        # Note: The message might not be sent due to missing bot setup, but it should be attempted
        total_attempted = result.get("sent_count", 0) + result.get("failed_count", 0)
        
        # Should have attempted to send 1 message (for BOT_TOKEN), not 2
        assert total_attempted <= 1  # May be 0 if send failed, but shouldn't be 2
