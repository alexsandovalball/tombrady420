"""
Tests for multi-bot synchronized broadcasts and notifications.

Tests the functionality of routing messages to the correct bot process
based on target_bot_key, with backward compatibility for NULL values.
"""
import os
import sys
import pytest
import pytest_asyncio
import asyncio
import tempfile
import aiosqlite
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from utils.bot_key import get_bot_env_key, is_primary_bot_process


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
    except:
        pass


class TestBotKeyDetection:
    """Test bot environment key detection."""
    
    def test_get_bot_env_key_primary(self):
        """Test that BOT_TOKEN is detected as primary."""
        with patch.dict(os.environ, {'BOT_TOKEN': 'test_token'}, clear=True):
            assert get_bot_env_key() == 'BOT_TOKEN'
    
    def test_get_bot_env_key_secondary(self):
        """Test that BOT_TOKEN_1 is detected when BOT_TOKEN is not present."""
        with patch.dict(os.environ, {'BOT_TOKEN_1': 'test_token_1'}, clear=True):
            assert get_bot_env_key() == 'BOT_TOKEN_1'
    
    def test_get_bot_env_key_multiple(self):
        """Test that BOT_TOKEN is preferred over BOT_TOKEN_N."""
        with patch.dict(os.environ, {
            'BOT_TOKEN': 'primary',
            'BOT_TOKEN_1': 'secondary1',
            'BOT_TOKEN_2': 'secondary2'
        }, clear=True):
            assert get_bot_env_key() == 'BOT_TOKEN'
    
    def test_get_bot_env_key_fallback(self):
        """Test fallback when no BOT_TOKEN env vars present."""
        with patch.dict(os.environ, {}, clear=True):
            assert get_bot_env_key() == 'BOT_TOKEN'
    
    def test_is_primary_bot_process_true(self):
        """Test primary bot detection."""
        with patch.dict(os.environ, {'BOT_TOKEN': 'test'}, clear=True):
            assert is_primary_bot_process() == True
    
    def test_is_primary_bot_process_false(self):
        """Test secondary bot detection."""
        with patch.dict(os.environ, {'BOT_TOKEN_1': 'test'}, clear=True):
            assert is_primary_bot_process() == False


class TestUserBotKeyTracking:
    """Test user last_bot_key tracking."""
    
    @pytest.mark.asyncio
    async def test_set_and_get_last_bot_key(self, temp_db):
        """Test setting and retrieving user's last bot key."""
        # Create a user first
        await temp_db.track_user(12345, "testuser", "Test", "User", "testbot")
        
        # Set last bot key
        await temp_db.set_last_bot_key(12345, "BOT_TOKEN_1")
        
        # Retrieve and verify
        bot_key = await temp_db.get_user_last_bot_key(12345)
        assert bot_key == "BOT_TOKEN_1"
    
    @pytest.mark.asyncio
    async def test_get_last_bot_key_not_set(self, temp_db):
        """Test getting last_bot_key for user who hasn't set it."""
        # Create a user
        await temp_db.track_user(12346, "testuser2", "Test", "User2", "testbot")
        
        # Should return None if not set
        bot_key = await temp_db.get_user_last_bot_key(12346)
        assert bot_key is None
    
    @pytest.mark.asyncio
    async def test_update_last_bot_key(self, temp_db):
        """Test updating user's last bot key."""
        await temp_db.track_user(12347, "testuser3", "Test", "User3", "testbot")
        
        # Set initial key
        await temp_db.set_last_bot_key(12347, "BOT_TOKEN")
        assert await temp_db.get_user_last_bot_key(12347) == "BOT_TOKEN"
        
        # Update to different key
        await temp_db.set_last_bot_key(12347, "BOT_TOKEN_2")
        assert await temp_db.get_user_last_bot_key(12347) == "BOT_TOKEN_2"


class TestQueueWithTargetBotKey:
    """Test queueing messages with target_bot_key."""
    
    @pytest.mark.asyncio
    async def test_queue_custom_message_with_target(self, temp_db):
        """Test queuing custom message with explicit target_bot_key."""
        with patch('database.get_bot_env_key', return_value='BOT_TOKEN'):
            await temp_db.queue_custom_message(
                user_id=123,
                message_text="Test message",
                target_bot_key="BOT_TOKEN_1"
            )
        
        # Verify message was queued with correct target_bot_key
        messages = await temp_db.get_pending_custom_messages(limit=10)
        assert len(messages) == 1
        assert messages[0]['target_bot_key'] == "BOT_TOKEN_1"
    
    @pytest.mark.asyncio
    async def test_queue_custom_message_default_target(self, temp_db):
        """Test queuing custom message defaults to current process bot key."""
        with patch('database.get_bot_env_key', return_value='BOT_TOKEN'):
            await temp_db.queue_custom_message(
                user_id=124,
                message_text="Test message 2"
            )
        
        messages = await temp_db.get_pending_custom_messages(limit=10)
        assert len(messages) == 1
        assert messages[0]['target_bot_key'] == "BOT_TOKEN"
    
    @pytest.mark.asyncio
    async def test_queue_custom_messages_batch_with_targets(self, temp_db):
        """Test batch queuing with individual target_bot_key per user."""
        users_with_targets = [
            (100, "BOT_TOKEN"),
            (101, "BOT_TOKEN_1"),
            (102, "BOT_TOKEN_2"),
        ]
        
        count = await temp_db.queue_custom_messages_batch_with_targets(
            users_with_targets=users_with_targets,
            message_text="Broadcast message"
        )
        
        assert count == 3
        
        # Verify each message has correct target
        messages = await temp_db.get_pending_custom_messages(limit=10)
        assert len(messages) == 3
        
        # Check targets match
        message_targets = {m['user_id']: m['target_bot_key'] for m in messages}
        assert message_targets[100] == "BOT_TOKEN"
        assert message_targets[101] == "BOT_TOKEN_1"
        assert message_targets[102] == "BOT_TOKEN_2"
    
    @pytest.mark.asyncio
    async def test_queue_notification_with_target(self, temp_db):
        """Test queuing notification with target_bot_key."""
        # Create a product first
        product_id = await temp_db.add_product(
            file_id="test_file",
            file_type="photo",
            message_id=999,
            chat_id=-100,
            caption="Test product"
        )
        
        with patch('database.get_bot_env_key', return_value='BOT_TOKEN'):
            await temp_db.queue_notification(
                user_id=200,
                product_id=product_id,
                target_bot_key="BOT_TOKEN_1"
            )
        
        # Verify notification was queued
        notifications = await temp_db.get_pending_notifications(limit=10)
        assert len(notifications) == 1
        assert notifications[0]['target_bot_key'] == "BOT_TOKEN_1"
    
    @pytest.mark.asyncio
    async def test_queue_notifications_batch_with_targets(self, temp_db):
        """Test batch notification queuing with individual targets."""
        # Create a product
        product_id = await temp_db.add_product(
            file_id="test_file2",
            file_type="photo",
            message_id=1000,
            chat_id=-100,
            caption="Test product 2"
        )
        
        users_with_targets = [
            (300, "BOT_TOKEN"),
            (301, "BOT_TOKEN_1"),
        ]
        
        count = await temp_db.queue_notifications_batch_with_targets(
            users_with_targets=users_with_targets,
            product_id=product_id
        )
        
        assert count == 2
        
        # Verify targets
        notifications = await temp_db.get_pending_notifications(limit=10)
        assert len(notifications) == 2
        
        notif_targets = {n['user_id']: n['target_bot_key'] for n in notifications}
        assert notif_targets[300] == "BOT_TOKEN"
        assert notif_targets[301] == "BOT_TOKEN_1"


class TestBackwardCompatibility:
    """Test backward compatibility with NULL target_bot_key."""
    
    @pytest.mark.asyncio
    async def test_null_target_bot_key_in_queue(self, temp_db):
        """Test that NULL target_bot_key is handled correctly."""
        # Manually insert message with NULL target_bot_key (simulating old data)
        async with aiosqlite.connect(temp_db.db_path) as db:
            await db.execute("""
                INSERT INTO custom_message_queue 
                (user_id, message_text, target_bot_key, created_at)
                VALUES (?, ?, NULL, ?)
            """, (500, "Old message", datetime.now()))
            await db.commit()
        
        # Verify message was queued with NULL target
        messages = await temp_db.get_pending_custom_messages(limit=10)
        assert len(messages) == 1
        assert messages[0]['target_bot_key'] is None
    
    @pytest.mark.asyncio
    async def test_null_last_bot_key_fallback(self, temp_db):
        """Test that NULL last_bot_key falls back to current process."""
        # Create user without last_bot_key set
        await temp_db.track_user(600, "olduser", "Old", "User", "oldbot")
        
        # Verify last_bot_key is NULL
        bot_key = await temp_db.get_user_last_bot_key(600)
        assert bot_key is None
        
        # When queuing, it should use current process key
        with patch('database.get_bot_env_key', return_value='BOT_TOKEN'):
            await temp_db.queue_custom_message(
                user_id=600,
                message_text="Message for old user"
            )
        
        messages = await temp_db.get_pending_custom_messages(limit=10)
        # Should default to current process key (BOT_TOKEN) since user has NULL
        assert messages[0]['target_bot_key'] == "BOT_TOKEN"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
