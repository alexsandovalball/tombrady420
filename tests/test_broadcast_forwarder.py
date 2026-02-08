"""
Tests for broadcast forwarder Redis pub/sub functionality.

Tests the publish/listen flow with mocked Redis client and notification service.
"""
import os
import sys
import pytest
import asyncio
import json
from unittest.mock import Mock, AsyncMock, patch, MagicMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestBroadcastForwarder:
    """Test broadcast forwarder publish and listen functionality."""
    
    @pytest.mark.asyncio
    async def test_publish_broadcast_success(self):
        """Test successful broadcast publishing to Redis."""
        # Mock Redis client
        mock_redis = MagicMock()
        mock_redis.publish.return_value = 2  # 2 subscribers received the message
        
        with patch('utils.redis_client.get_redis_client', return_value=mock_redis):
            from utils import broadcast_forwarder
            
            # Publish a broadcast
            message_text = "Test broadcast message"
            success = await broadcast_forwarder.publish_broadcast(message_text)
            
            # Verify success
            assert success is True
            
            # Verify Redis publish was called
            mock_redis.publish.assert_called_once()
            call_args = mock_redis.publish.call_args
            
            # Check channel
            assert call_args[0][0] == "bot:broadcast"
            
            # Check payload
            payload = json.loads(call_args[0][1])
            assert payload["message_text"] == message_text
            assert "timestamp" in payload
    
    @pytest.mark.asyncio
    async def test_publish_broadcast_redis_error(self):
        """Test broadcast publishing failure when Redis is unavailable."""
        # Mock Redis client to raise error
        with patch('utils.redis_client.get_redis_client', side_effect=Exception("Redis unavailable")):
            from utils import broadcast_forwarder
            
            # Try to publish
            success = await broadcast_forwarder.publish_broadcast("Test message")
            
            # Should return False on error
            assert success is False
    
    @pytest.mark.asyncio
    async def test_listener_receives_broadcast(self):
        """Test that listener receives and processes broadcast messages."""
        # Create mock Redis pubsub
        mock_pubsub = MagicMock()
        
        # Simulate receiving a broadcast message
        broadcast_payload = {
            "message_text": "Test broadcast from Redis",
            "timestamp": 123456.789
        }
        
        mock_message = {
            "type": "message",
            "data": json.dumps(broadcast_payload)
        }
        
        # Mock get_message to return our message once, then raise CancelledError to stop loop
        call_count = [0]
        def mock_get_message(timeout):
            call_count[0] += 1
            if call_count[0] == 1:
                return mock_message
            else:
                raise asyncio.CancelledError()
        
        mock_pubsub.get_message = mock_get_message
        
        # Mock Redis client
        mock_redis = MagicMock()
        mock_redis.pubsub.return_value = mock_pubsub
        
        # Create mock context
        mock_context = MagicMock()
        mock_context.bot = MagicMock()
        mock_context.bot.username = "test_bot"
        
        # Create mock enqueue callback
        enqueue_called = asyncio.Event()
        received_message = []
        
        async def mock_enqueue(context, message_text):
            received_message.append(message_text)
            enqueue_called.set()
        
        with patch('utils.redis_client.get_redis_client', return_value=mock_redis):
            with patch('utils.bot_key.get_bot_env_key', return_value='BOT_TOKEN'):
                with patch('utils.bot_key.get_bot_key_display', return_value='@test_bot'):
                    from utils import broadcast_forwarder
                    
                    # Start listener loop (it will be cancelled after first message)
                    try:
                        await broadcast_forwarder._redis_listener_loop(mock_context, mock_enqueue)
                    except asyncio.CancelledError:
                        pass
        
        # Verify subscription happened
        mock_pubsub.subscribe.assert_called_once_with("bot:broadcast")
        
        # Verify enqueue callback was called with correct message
        assert len(received_message) == 1
        assert received_message[0] == "Test broadcast from Redis"
    
    @pytest.mark.asyncio
    async def test_start_listener_no_redis(self):
        """Test that listener is not started when Redis is not configured."""
        mock_context = MagicMock()
        mock_enqueue = AsyncMock()
        
        # Mock Config with no REDIS_URL
        with patch('configs.config.Config') as mock_config:
            mock_config.REDIS_URL = None
            
            from utils import broadcast_forwarder
            
            # Try to start listener
            task = broadcast_forwarder.start_listener_in_background(mock_context, mock_enqueue)
            
            # Should return None when Redis not configured
            assert task is None
    
    @pytest.mark.asyncio
    async def test_start_listener_success(self):
        """Test successful listener startup."""
        # Mock Config with REDIS_URL
        with patch('configs.config.Config') as mock_config:
            mock_config.REDIS_URL = "redis://localhost:6379/0"
            
            from utils import broadcast_forwarder
            
            # Reset global listener task
            broadcast_forwarder._listener_task = None
            
            mock_context = MagicMock()
            mock_enqueue = AsyncMock()
            
            # Mock the listener loop to avoid actually running
            async def mock_listener_loop(*args):
                # Simulate running task
                await asyncio.sleep(0.1)
            
            with patch.object(broadcast_forwarder, '_redis_listener_loop', side_effect=mock_listener_loop):
                # Start listener
                task = broadcast_forwarder.start_listener_in_background(mock_context, mock_enqueue)
                
                # Should return a task
                assert task is not None
                assert isinstance(task, asyncio.Task)
                
                # Clean up
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
    
    def test_stop_listener(self):
        """Test listener stop functionality."""
        from utils import broadcast_forwarder
        
        # Create a mock task
        mock_task = MagicMock()
        mock_task.done.return_value = False
        
        # Set as global listener
        broadcast_forwarder._listener_task = mock_task
        
        # Stop listener
        broadcast_forwarder.stop_listener()
        
        # Verify task was cancelled
        mock_task.cancel.assert_called_once()
        
        # Verify global task was cleared
        assert broadcast_forwarder._listener_task is None


class TestEnqueueLocalBroadcast:
    """Test enqueue_local_broadcast functionality."""
    
    @pytest.mark.asyncio
    async def test_enqueue_filters_admins(self):
        """Test that enqueue_local_broadcast filters out admin users."""
        # Mock all the dependencies before importing
        with patch.dict('sys.modules', {
            'deep_translator': MagicMock(),
            'translations.translator': MagicMock()
        }):
            from utils.notifications import NotificationService
            
            # Create mock database
            mock_db = AsyncMock()
            
            # Mock users: 2 regular users, 1 admin, 1 blocked
            mock_users = [
                {"user_id": 1, "last_bot_key": "BOT_TOKEN", "is_blocked": 0},
                {"user_id": 2, "last_bot_key": "BOT_TOKEN", "is_blocked": 0},
                {"user_id": 999, "last_bot_key": "BOT_TOKEN", "is_blocked": 0},  # admin
                {"user_id": 3, "last_bot_key": "BOT_TOKEN", "is_blocked": 1},  # blocked
            ]
            mock_db.get_all_users.return_value = mock_users
            mock_db.queue_custom_messages_batch_with_targets.return_value = 2
            
            # Create notification service
            service = NotificationService(mock_db)
            
            # Mock context
            mock_context = MagicMock()
            mock_context.bot = MagicMock()
            mock_context.bot.username = "test_bot"
            
            with patch('utils.helpers.get_admin_ids', return_value=[999]):
                with patch('utils.bot_key.get_bot_env_key', return_value='BOT_TOKEN'):
                    with patch('utils.bot_key.get_bot_key_display', return_value='@test_bot'):
                        # Mock the store task method
                        service._store_task = MagicMock()
                        
                        # Enqueue broadcast
                        count = await service.enqueue_local_broadcast(
                            mock_context,
                            "Test message",
                            exclude_blocked=True
                        )
                        
                        # Should queue for 2 users only (not admin, not blocked)
                        assert count == 2
                        
                        # Verify queue was called with correct users
                        call_args = mock_db.queue_custom_messages_batch_with_targets.call_args
                        users_with_targets = call_args[1]['users_with_targets']
                        
                        # Should only have users 1 and 2
                        user_ids = [uid for uid, _ in users_with_targets]
                        assert set(user_ids) == {1, 2}
    
    @pytest.mark.asyncio
    async def test_enqueue_filters_other_bots(self):
        """Test that enqueue_local_broadcast only queues for current bot's users."""
        # Mock all the dependencies before importing
        with patch.dict('sys.modules', {
            'deep_translator': MagicMock(),
            'translations.translator': MagicMock()
        }):
            from utils.notifications import NotificationService
            
            # Create mock database
            mock_db = AsyncMock()
            
            # Mock users: some for BOT_TOKEN, some for BOT_TOKEN_1
            mock_users = [
                {"user_id": 1, "last_bot_key": "BOT_TOKEN", "is_blocked": 0},
                {"user_id": 2, "last_bot_key": "BOT_TOKEN_1", "is_blocked": 0},  # other bot
                {"user_id": 3, "last_bot_key": "BOT_TOKEN", "is_blocked": 0},
                {"user_id": 4, "last_bot_key": None, "is_blocked": 0},  # NULL - will be included
            ]
            mock_db.get_all_users.return_value = mock_users
            mock_db.queue_custom_messages_batch_with_targets.return_value = 3
            
            # Create notification service
            service = NotificationService(mock_db)
            
            # Mock context
            mock_context = MagicMock()
            mock_context.bot = MagicMock()
            mock_context.bot.username = "test_bot"
            
            with patch('utils.helpers.get_admin_ids', return_value=[]):
                with patch('utils.bot_key.get_bot_env_key', return_value='BOT_TOKEN'):
                    with patch('utils.bot_key.get_bot_key_display', return_value='@test_bot'):
                        # Mock the store task method
                        service._store_task = MagicMock()
                        
                        # Enqueue broadcast
                        count = await service.enqueue_local_broadcast(
                            mock_context,
                            "Test message",
                            exclude_blocked=True
                        )
                        
                        # Should queue for 3 users: 1, 3, and 4 (NULL is included)
                        assert count == 3
                        
                        # Verify queue was called with correct users
                        call_args = mock_db.queue_custom_messages_batch_with_targets.call_args
                        users_with_targets = call_args[1]['users_with_targets']
                        
                        # Should have users 1, 3, and 4 (not 2 - different bot)
                        user_ids = [uid for uid, _ in users_with_targets]
                        assert set(user_ids) == {1, 3, 4}


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v"])
