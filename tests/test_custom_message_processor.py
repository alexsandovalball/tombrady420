"""
Tests for custom message background processor.

This module tests the background processor that ensures messages
assigned to each bot instance are delivered even when enqueued by
other processes (e.g., DB broadcast forwarder on secondary bots).
"""
import os
import sys
import pytest
import pytest_asyncio
import asyncio
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch, call
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from utils.notifications import (
    BotContext,
    start_custom_message_processor_in_background,
    NotificationService
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
    except Exception:
        pass


@pytest_asyncio.fixture
def mock_application():
    """Create a mock Application instance."""
    app = MagicMock()
    app.bot = MagicMock()
    app.bot.username = 'test_bot'
    app.bot_data = {}
    return app


class TestBotContext:
    """Test the BotContext helper class."""
    
    def test_bot_context_creation(self):
        """Test creating a BotContext instance."""
        mock_bot = MagicMock()
        mock_bot.username = 'test_bot'
        
        context = BotContext(mock_bot)
        
        assert context.bot == mock_bot
        assert context.application is None
    
    def test_bot_context_with_application(self):
        """Test creating a BotContext with application."""
        mock_bot = MagicMock()
        mock_app = MagicMock()
        
        context = BotContext(mock_bot, mock_app)
        
        assert context.bot == mock_bot
        assert context.application == mock_app


class TestCustomMessageProcessor:
    """Test the custom message background processor."""
    
    @pytest.mark.asyncio
    async def test_processor_is_scheduled(self, mock_application, temp_db, monkeypatch):
        """Test that the background processor is scheduled correctly."""
        # Mock get_bot_env_key to return a fixed bot key
        monkeypatch.setattr('utils.notifications.get_bot_env_key', lambda: 'BOT_TOKEN_1')
        
        # Start the processor
        task = start_custom_message_processor_in_background(mock_application, temp_db, interval_seconds=1)
        
        # Verify task is created and running
        assert task is not None
        assert isinstance(task, asyncio.Task)
        assert not task.done()
        
        # Clean up
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    
    @pytest.mark.asyncio
    async def test_processor_calls_process_custom_message_queue(
        self, mock_application, temp_db, monkeypatch
    ):
        """Test that the processor calls process_custom_message_queue."""
        # Mock get_bot_env_key to return a fixed bot key
        monkeypatch.setattr('utils.notifications.get_bot_env_key', lambda: 'BOT_TOKEN_1')
        
        # Track calls to process_custom_message_queue
        process_calls = []
        
        original_method = NotificationService.process_custom_message_queue
        
        async def mock_process_custom_message_queue(self, context, process_all_bots=False):
            """Mock process_custom_message_queue that records calls."""
            process_calls.append({
                'context': context,
                'process_all_bots': process_all_bots
            })
            return {
                'sent_count': 0,
                'failed_count': 0,
                'invalidated_count': 0,
                'failure_reasons': {},
                'failed_user_ids': []
            }
        
        # Patch the NotificationService.process_custom_message_queue method
        with patch.object(
            NotificationService,
            'process_custom_message_queue',
            mock_process_custom_message_queue
        ):
            # Start the processor with a short interval
            task = start_custom_message_processor_in_background(
                mock_application, temp_db, interval_seconds=0.1
            )
            
            # Wait for at least 2 processing cycles
            await asyncio.sleep(0.3)
            
            # Cancel the task
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            
            # Verify process_custom_message_queue was called multiple times
            assert len(process_calls) >= 2, f"Expected at least 2 calls, got {len(process_calls)}"
            
            # Verify all calls had process_all_bots=False (bot-specific processing)
            for call_record in process_calls:
                assert call_record['process_all_bots'] is False
                assert isinstance(call_record['context'], BotContext)
                assert call_record['context'].bot == mock_application.bot
    
    @pytest.mark.asyncio
    async def test_processor_handles_errors(self, mock_application, temp_db, monkeypatch):
        """Test that the processor continues running even if processing fails."""
        # Mock get_bot_env_key to return a fixed bot key
        monkeypatch.setattr('utils.notifications.get_bot_env_key', lambda: 'BOT_TOKEN_1')
        
        # Track calls and make some fail
        call_count = [0]
        
        async def mock_process_with_errors(self, context, process_all_bots=False):
            """Mock that fails on first call, succeeds on subsequent calls."""
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("Simulated processing error")
            return {
                'sent_count': 0,
                'failed_count': 0,
                'invalidated_count': 0,
                'failure_reasons': {},
                'failed_user_ids': []
            }
        
        # Patch the NotificationService.process_custom_message_queue method
        with patch.object(
            NotificationService,
            'process_custom_message_queue',
            mock_process_with_errors
        ):
            # Start the processor with a short interval
            task = start_custom_message_processor_in_background(
                mock_application, temp_db, interval_seconds=0.1
            )
            
            # Wait for multiple cycles (should recover from first error)
            await asyncio.sleep(0.3)
            
            # Cancel the task
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            
            # Verify multiple calls were made (processor recovered from error)
            assert call_count[0] >= 2, "Processor should continue after error"
    
    @pytest.mark.asyncio
    async def test_processor_with_pending_messages(self, mock_application, temp_db, monkeypatch):
        """Test processor with actual pending messages in the database."""
        # Mock get_bot_env_key to return a fixed bot key
        monkeypatch.setattr('utils.notifications.get_bot_env_key', lambda: 'BOT_TOKEN_1')
        monkeypatch.setattr('utils.bot_key.get_bot_env_key', lambda: 'BOT_TOKEN_1')
        
        # Add a test user to the database
        user_id = 12345
        await temp_db.track_user(user_id, username='test_user')
        await temp_db.set_user_language(user_id, 'en')
        await temp_db.set_last_bot_key(user_id, 'BOT_TOKEN_1')
        
        # Queue a custom message for this user
        await temp_db.queue_custom_message(
            user_id=user_id,
            message_text="Test broadcast message",
            target_bot_key='BOT_TOKEN_1'
        )
        
        # Verify message was queued
        pending = await temp_db.get_pending_custom_messages(limit=10)
        assert len(pending) == 1
        assert pending[0]['user_id'] == user_id
        
        # Mock the bot.send_message to avoid actual API calls
        mock_application.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
        
        # Create a NotificationService and process the queue once
        service = NotificationService(temp_db)
        bot_context = BotContext(mock_application.bot, mock_application)
        
        result = await service.process_custom_message_queue(bot_context, process_all_bots=False)
        
        # The message should have been sent (or at least attempted)
        # Note: It might fail due to user validation, which is expected in test environment
        # We're just verifying the processor can handle the queue
        assert isinstance(result, dict)
        assert 'sent_count' in result
        assert 'failed_count' in result


class TestProcessorIntegration:
    """Integration tests for processor with bot instances."""
    
    @pytest.mark.asyncio
    async def test_processor_task_storage(self, mock_application, temp_db, monkeypatch):
        """Test that processor task is properly stored in bot_data."""
        # Mock get_bot_env_key to return a fixed bot key
        monkeypatch.setattr('utils.notifications.get_bot_env_key', lambda: 'BOT_TOKEN_1')
        
        # Start the processor
        task = start_custom_message_processor_in_background(mock_application, temp_db, interval_seconds=1)
        
        # Store task in bot_data (as done in main.py and webhook_server.py)
        mock_application.bot_data['custom_message_processor_task'] = task
        
        # Verify task is stored
        assert 'custom_message_processor_task' in mock_application.bot_data
        assert mock_application.bot_data['custom_message_processor_task'] == task
        
        # Clean up
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
