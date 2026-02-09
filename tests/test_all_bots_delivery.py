"""
Tests for ensuring all bot instances deliver notifications to their own users.

This module tests the updated notification delivery system where:
1. Broadcasts are delivered by all bots to their own users (based on bot_username)
2. Single-user sends are delivered by all bots the user has started
3. Product notifications are delivered by all bots to their subscribed users
"""
import os
import sys
import pytest
import pytest_asyncio
import asyncio
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from utils import broadcast_forwarder
from utils.notifications import enqueue_local_broadcast
from utils.bot_key import set_bot_username, get_bot_username


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


class TestBotUsernameFiltering:
    """Test that broadcasts are filtered by bot_username instead of last_bot_key."""
    
    @pytest.mark.asyncio
    async def test_enqueue_local_broadcast_uses_bot_username(self, temp_db):
        """Test that enqueue_local_broadcast filters users by bot_username."""
        # Set up bot username
        set_bot_username('testbot1')
        
        # Add users with different bot_usernames
        # User 1: started testbot1
        await temp_db.track_user(101, 'user1', 'User', 'One', bot_username='testbot1')
        await temp_db.set_last_bot_key(101, 'BOT_TOKEN_1')  # Different from bot_username
        
        # User 2: started testbot2
        await temp_db.track_user(102, 'user2', 'User', 'Two', bot_username='testbot2')
        await temp_db.set_last_bot_key(102, 'BOT_TOKEN_2')
        
        # User 3: started testbot1
        await temp_db.track_user(103, 'user3', 'User', 'Three', bot_username='testbot1')
        await temp_db.set_last_bot_key(103, 'BOT_TOKEN')  # Different from bot_username
        
        # Enqueue a broadcast
        message_text = "Test broadcast message"
        context = {}
        await enqueue_local_broadcast(temp_db, context, message_text)
        
        # Get queued messages
        pending = await temp_db.get_pending_custom_messages(limit=100)
        
        # Should have 2 messages (users 101 and 103 who started testbot1)
        assert len(pending) == 2
        user_ids = {msg['user_id'] for msg in pending}
        assert user_ids == {101, 103}
        
        # Verify message content
        for msg in pending:
            assert msg['message_text'] == message_text
    
    @pytest.mark.asyncio
    async def test_single_user_send_broadcast_pattern(self, temp_db):
        """Test that single-user sends use broadcast forwarder pattern."""
        # Set up bot username
        set_bot_username('bot1')
        
        # Add a user who has started bot1
        await temp_db.track_user(201, 'user201', 'User', '201', bot_username='bot1')
        
        # Publish a single-user send as a broadcast event
        message_text = "Test single-user message"
        payload = {"target_user_id": 201}
        
        broadcast_id = await broadcast_forwarder.publish_broadcast(
            temp_db, 
            message_text, 
            payload=payload
        )
        
        assert broadcast_id > 0
        
        # Verify broadcast event was created
        events = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN')
        assert len(events) == 1
        assert events[0]['id'] == broadcast_id
        
        # Process the broadcast
        context = {"target_user_id": 201}
        await enqueue_local_broadcast(temp_db, context, message_text)
        
        # Verify message was queued for user 201
        pending = await temp_db.get_pending_custom_messages(limit=100)
        assert len(pending) == 1
        assert pending[0]['user_id'] == 201
        assert pending[0]['message_text'] == message_text
    
    @pytest.mark.asyncio
    async def test_product_notification_to_subscribed_users_only(self, temp_db):
        """Test that product notifications only go to subscribed users of this bot."""
        # Set up bot username
        set_bot_username('bot1')
        
        # Add users
        # User 301: started bot1, subscribed
        await temp_db.track_user(301, 'user301', 'User', '301', bot_username='bot1')
        await temp_db.set_user_notifications(301, True)
        
        # User 302: started bot1, NOT subscribed
        await temp_db.track_user(302, 'user302', 'User', '302', bot_username='bot1')
        await temp_db.set_user_notifications(302, False)
        
        # User 303: started bot2, subscribed (should not receive from bot1)
        await temp_db.track_user(303, 'user303', 'User', '303', bot_username='bot2')
        await temp_db.set_user_notifications(303, True)
        
        # Process a product notification broadcast
        message_text = "New product available!"
        payload = {"product_id": 123, "reply_markup_json": None}
        context = payload
        
        await enqueue_local_broadcast(temp_db, context, message_text)
        
        # Verify only user 301 received the message
        pending = await temp_db.get_pending_custom_messages(limit=100)
        assert len(pending) == 1
        assert pending[0]['user_id'] == 301
        assert pending[0]['message_text'] == message_text
    
    @pytest.mark.asyncio
    async def test_multiple_bots_deliver_to_own_users(self, temp_db):
        """Test that different bot instances deliver to their own users."""
        # Add a user who has started both bots
        await temp_db.track_user(401, 'user401', 'User', '401', bot_username='bot1')
        
        # Simulate bot1 processing the broadcast
        set_bot_username('bot1')
        message_text = "Test broadcast"
        context = {}
        await enqueue_local_broadcast(temp_db, context, message_text)
        
        # Check that user 401 has a message from bot1
        pending1 = await temp_db.get_pending_custom_messages(limit=100)
        assert len(pending1) == 1
        assert pending1[0]['user_id'] == 401
        
        # Now simulate the same user also started bot2
        await temp_db.track_user(401, 'user401', 'User', '401', bot_username='bot2')
        
        # Clear the queue
        for msg in pending1:
            await temp_db.mark_custom_message_sent(msg['id'])
        
        # Simulate bot2 processing the same broadcast
        set_bot_username('bot2')
        await enqueue_local_broadcast(temp_db, context, message_text)
        
        # Check that user 401 now has a message from bot2
        pending2 = await temp_db.get_pending_custom_messages(limit=100)
        assert len(pending2) == 1
        assert pending2[0]['user_id'] == 401
        
        # This demonstrates that users who started multiple bots will receive
        # the same message multiple times (once from each bot)


class TestBackwardCompatibility:
    """Test that the system handles NULL bot_username gracefully."""
    
    @pytest.mark.asyncio
    async def test_null_bot_username_fallback(self, temp_db):
        """Test that NULL bot_username users are handled gracefully."""
        # Set up bot username
        set_bot_username('bot1')
        
        # Add a user with NULL bot_username (legacy user)
        await temp_db.track_user(501, 'user501', 'User', '501', bot_username=None)
        
        # In the current implementation, users with NULL bot_username
        # should only be included if they match the bot via get_users_by_bot logic
        # which includes NULL users for the primary bot
        
        # For now, we just verify that it doesn't crash
        message_text = "Test broadcast"
        context = {}
        
        try:
            await enqueue_local_broadcast(temp_db, context, message_text)
            # If bot_username is None in enqueue_local_broadcast, it should
            # fall back to getting all users
        except Exception as e:
            pytest.fail(f"enqueue_local_broadcast failed with NULL bot_username: {e}")
