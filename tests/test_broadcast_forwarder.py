"""
Tests for DB-backed broadcast forwarding.

This module tests the broadcast forwarding system that replaces Redis pub/sub
with a SQLite-backed polling system.
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


class TestPublishBroadcast:
    """Test publishing broadcast events to the database."""
    
    @pytest.mark.asyncio
    async def test_publish_broadcast_basic(self, temp_db):
        """Test publishing a basic broadcast event."""
        message_text = "Test broadcast message"
        
        broadcast_id = await broadcast_forwarder.publish_broadcast(temp_db, message_text)
        
        assert broadcast_id is not None
        assert broadcast_id > 0
        
        # Verify event was inserted
        events = await temp_db.get_unprocessed_broadcasts_for_bot(None)
        assert len(events) == 1
        assert events[0]['id'] == broadcast_id
        assert events[0]['message_text'] == message_text
        assert events[0]['payload_json'] is None
    
    @pytest.mark.asyncio
    async def test_publish_broadcast_with_payload(self, temp_db):
        """Test publishing a broadcast event with a payload."""
        message_text = "Test broadcast with payload"
        payload = {"priority": "high", "category": "system"}
        
        broadcast_id = await broadcast_forwarder.publish_broadcast(temp_db, message_text, payload)
        
        assert broadcast_id > 0
        
        # Verify event and payload
        events = await temp_db.get_unprocessed_broadcasts_for_bot(None)
        assert len(events) == 1
        assert events[0]['id'] == broadcast_id
        assert events[0]['message_text'] == message_text
        assert events[0]['payload_json'] is not None
        
        # Payload should be JSON string
        import json
        parsed_payload = json.loads(events[0]['payload_json'])
        assert parsed_payload == payload
    
    @pytest.mark.asyncio
    async def test_publish_multiple_broadcasts(self, temp_db):
        """Test publishing multiple broadcast events."""
        messages = ["Broadcast 1", "Broadcast 2", "Broadcast 3"]
        
        broadcast_ids = []
        for msg in messages:
            broadcast_id = await broadcast_forwarder.publish_broadcast(temp_db, msg)
            broadcast_ids.append(broadcast_id)
        
        # Verify all events were inserted
        events = await temp_db.get_unprocessed_broadcasts_for_bot(None)
        assert len(events) == 3
        
        # Verify IDs are unique and sequential
        assert len(set(broadcast_ids)) == 3
        for i in range(len(broadcast_ids) - 1):
            assert broadcast_ids[i+1] > broadcast_ids[i]


class TestGetUnprocessedBroadcasts:
    """Test retrieving unprocessed broadcast events."""
    
    @pytest.mark.asyncio
    async def test_get_unprocessed_no_events(self, temp_db):
        """Test getting unprocessed broadcasts when none exist."""
        events = await temp_db.get_unprocessed_broadcasts_for_bot("BOT_TOKEN")
        assert events == []
    
    @pytest.mark.asyncio
    async def test_get_unprocessed_single_bot(self, temp_db):
        """Test getting unprocessed broadcasts for a single bot."""
        # Create a broadcast event
        broadcast_id = await broadcast_forwarder.publish_broadcast(temp_db, "Test message")
        
        # BOT_TOKEN should see it as unprocessed
        events = await temp_db.get_unprocessed_broadcasts_for_bot("BOT_TOKEN")
        assert len(events) == 1
        assert events[0]['id'] == broadcast_id
    
    @pytest.mark.asyncio
    async def test_get_unprocessed_after_marking_processed(self, temp_db):
        """Test that processed broadcasts are filtered out."""
        # Create a broadcast event
        broadcast_id = await broadcast_forwarder.publish_broadcast(temp_db, "Test message")
        
        # Mark as processed for BOT_TOKEN
        await temp_db.mark_broadcast_processed(broadcast_id, "BOT_TOKEN")
        
        # Should not appear in unprocessed list
        events = await temp_db.get_unprocessed_broadcasts_for_bot("BOT_TOKEN")
        assert len(events) == 0
    
    @pytest.mark.asyncio
    async def test_get_unprocessed_multi_bot(self, temp_db):
        """Test that different bots see the same unprocessed broadcasts."""
        # Create a broadcast event
        broadcast_id = await broadcast_forwarder.publish_broadcast(temp_db, "Multi-bot message")
        
        # Both bots should see it as unprocessed
        events_bot1 = await temp_db.get_unprocessed_broadcasts_for_bot("BOT_TOKEN")
        events_bot2 = await temp_db.get_unprocessed_broadcasts_for_bot("BOT_TOKEN_1")
        
        assert len(events_bot1) == 1
        assert len(events_bot2) == 1
        assert events_bot1[0]['id'] == broadcast_id
        assert events_bot2[0]['id'] == broadcast_id
    
    @pytest.mark.asyncio
    async def test_get_unprocessed_partial_processing(self, temp_db):
        """Test that marking processed for one bot doesn't affect others."""
        # Create a broadcast event
        broadcast_id = await broadcast_forwarder.publish_broadcast(temp_db, "Partial test")
        
        # Mark as processed only for BOT_TOKEN
        await temp_db.mark_broadcast_processed(broadcast_id, "BOT_TOKEN")
        
        # BOT_TOKEN should not see it
        events_bot1 = await temp_db.get_unprocessed_broadcasts_for_bot("BOT_TOKEN")
        assert len(events_bot1) == 0
        
        # BOT_TOKEN_1 should still see it
        events_bot2 = await temp_db.get_unprocessed_broadcasts_for_bot("BOT_TOKEN_1")
        assert len(events_bot2) == 1
        assert events_bot2[0]['id'] == broadcast_id


class TestMarkBroadcastProcessed:
    """Test marking broadcasts as processed."""
    
    @pytest.mark.asyncio
    async def test_mark_broadcast_processed_basic(self, temp_db):
        """Test basic marking of broadcast as processed."""
        broadcast_id = await broadcast_forwarder.publish_broadcast(temp_db, "Test")
        
        # Mark as processed
        await temp_db.mark_broadcast_processed(broadcast_id, "BOT_TOKEN")
        
        # Verify it's marked
        events = await temp_db.get_unprocessed_broadcasts_for_bot("BOT_TOKEN")
        assert len(events) == 0
    
    @pytest.mark.asyncio
    async def test_mark_broadcast_processed_idempotent(self, temp_db):
        """Test that marking processed multiple times doesn't error (INSERT OR IGNORE)."""
        broadcast_id = await broadcast_forwarder.publish_broadcast(temp_db, "Test")
        
        # Mark as processed multiple times - should not raise error
        await temp_db.mark_broadcast_processed(broadcast_id, "BOT_TOKEN")
        await temp_db.mark_broadcast_processed(broadcast_id, "BOT_TOKEN")
        await temp_db.mark_broadcast_processed(broadcast_id, "BOT_TOKEN")
        
        # Should still be marked as processed (not duplicated)
        events = await temp_db.get_unprocessed_broadcasts_for_bot("BOT_TOKEN")
        assert len(events) == 0
    
    @pytest.mark.asyncio
    async def test_mark_broadcast_processed_none_bot_key(self, temp_db):
        """Test that marking with None bot_key does nothing."""
        broadcast_id = await broadcast_forwarder.publish_broadcast(temp_db, "Test")
        
        # Mark with None bot_key - should do nothing
        await temp_db.mark_broadcast_processed(broadcast_id, None)
        
        # Event should still be unprocessed for all bots
        events = await temp_db.get_unprocessed_broadcasts_for_bot("BOT_TOKEN")
        assert len(events) == 1


class TestListenerLoop:
    """Test the broadcast listener loop."""
    
    @pytest.mark.asyncio
    async def test_listener_processes_events(self, temp_db):
        """Test that listener processes broadcast events."""
        # Create a mock callback
        callback = AsyncMock()
        
        # Create a broadcast event
        message_text = "Test listener message"
        broadcast_id = await broadcast_forwarder.publish_broadcast(temp_db, message_text)
        
        # Run listener loop once (manually, not via background task)
        with patch('utils.bot_key.get_bot_env_key', return_value='BOT_TOKEN'):
            # Run one iteration of the listener
            # We'll simulate this by directly calling the internal function with a short timeout
            listener_task = asyncio.create_task(
                broadcast_forwarder._listener_loop(temp_db, callback, 'BOT_TOKEN')
            )
            
            # Wait a bit for processing
            await asyncio.sleep(0.5)
            
            # Cancel the task
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass
        
        # Verify callback was called
        assert callback.call_count >= 1
        callback.assert_called_with({}, message_text)
        
        # Verify event was marked as processed
        events = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN')
        assert len(events) == 0
    
    @pytest.mark.asyncio
    async def test_listener_processes_payload(self, temp_db):
        """Test that listener deserializes payload correctly."""
        callback = AsyncMock()
        
        # Create a broadcast with payload
        message_text = "Test with payload"
        payload = {"key": "value", "number": 42}
        broadcast_id = await broadcast_forwarder.publish_broadcast(temp_db, message_text, payload)
        
        # Run listener loop
        with patch('utils.bot_key.get_bot_env_key', return_value='BOT_TOKEN'):
            listener_task = asyncio.create_task(
                broadcast_forwarder._listener_loop(temp_db, callback, 'BOT_TOKEN')
            )
            
            await asyncio.sleep(0.5)
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass
        
        # Verify callback was called with deserialized payload
        assert callback.call_count >= 1
        callback.assert_called_with(payload, message_text)
    
    @pytest.mark.asyncio
    async def test_listener_handles_callback_error(self, temp_db):
        """Test that listener continues even if callback fails."""
        # Create a callback that raises an error
        callback = AsyncMock(side_effect=Exception("Callback error"))
        
        broadcast_id = await broadcast_forwarder.publish_broadcast(temp_db, "Test error handling")
        
        # Run listener loop
        with patch('utils.bot_key.get_bot_env_key', return_value='BOT_TOKEN'):
            listener_task = asyncio.create_task(
                broadcast_forwarder._listener_loop(temp_db, callback, 'BOT_TOKEN')
            )
            
            await asyncio.sleep(0.5)
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass
        
        # Callback should have been called
        assert callback.call_count >= 1
        
        # Event should still be marked as processed (to avoid infinite retries)
        events = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN')
        assert len(events) == 0
    
    @pytest.mark.asyncio
    async def test_listener_multiple_events(self, temp_db):
        """Test that listener processes multiple events."""
        callback = AsyncMock()
        
        # Create multiple broadcasts
        await broadcast_forwarder.publish_broadcast(temp_db, "Message 1")
        await broadcast_forwarder.publish_broadcast(temp_db, "Message 2")
        await broadcast_forwarder.publish_broadcast(temp_db, "Message 3")
        
        # Run listener
        with patch('utils.bot_key.get_bot_env_key', return_value='BOT_TOKEN'):
            listener_task = asyncio.create_task(
                broadcast_forwarder._listener_loop(temp_db, callback, 'BOT_TOKEN')
            )
            
            # Wait for all events to be processed
            await asyncio.sleep(1.0)
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass
        
        # All three events should have been processed
        assert callback.call_count >= 3
        
        # No unprocessed events should remain
        events = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN')
        assert len(events) == 0


class TestMultiBotScenario:
    """Test multi-bot broadcast scenarios."""
    
    @pytest.mark.asyncio
    async def test_two_bots_process_same_event(self, temp_db):
        """Test that two bots each process the same broadcast once."""
        callback1 = AsyncMock()
        callback2 = AsyncMock()
        
        # Create a broadcast
        message_text = "Multi-bot broadcast"
        broadcast_id = await broadcast_forwarder.publish_broadcast(temp_db, message_text)
        
        # Start listeners for both bots
        listener1 = asyncio.create_task(
            broadcast_forwarder._listener_loop(temp_db, callback1, 'BOT_TOKEN')
        )
        listener2 = asyncio.create_task(
            broadcast_forwarder._listener_loop(temp_db, callback2, 'BOT_TOKEN_1')
        )
        
        # Wait for both to process
        await asyncio.sleep(1.0)
        
        # Cancel both listeners
        listener1.cancel()
        listener2.cancel()
        try:
            await listener1
        except asyncio.CancelledError:
            pass
        try:
            await listener2
        except asyncio.CancelledError:
            pass
        
        # Both callbacks should have been called once
        assert callback1.call_count >= 1
        assert callback2.call_count >= 1
        callback1.assert_called_with({}, message_text)
        callback2.assert_called_with({}, message_text)
        
        # Event should be marked as processed for both bots
        events1 = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN')
        events2 = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN_1')
        assert len(events1) == 0
        assert len(events2) == 0
    
    @pytest.mark.asyncio
    async def test_new_bot_processes_old_events(self, temp_db):
        """Test that a new bot instance processes existing unprocessed events."""
        callback1 = AsyncMock()
        callback2 = AsyncMock()
        
        # Bot 1 processes a broadcast
        broadcast_id = await broadcast_forwarder.publish_broadcast(temp_db, "Old event")
        
        listener1 = asyncio.create_task(
            broadcast_forwarder._listener_loop(temp_db, callback1, 'BOT_TOKEN')
        )
        await asyncio.sleep(0.5)
        listener1.cancel()
        try:
            await listener1
        except asyncio.CancelledError:
            pass
        
        # Bot 1 should have processed it
        assert callback1.call_count >= 1
        
        # Now start Bot 2 - it should also process the same event
        listener2 = asyncio.create_task(
            broadcast_forwarder._listener_loop(temp_db, callback2, 'BOT_TOKEN_1')
        )
        await asyncio.sleep(0.5)
        listener2.cancel()
        try:
            await listener2
        except asyncio.CancelledError:
            pass
        
        # Bot 2 should also have processed it
        assert callback2.call_count >= 1


class TestStartListenerInBackground:
    """Test the start_listener_in_background helper."""
    
    @pytest.mark.asyncio
    async def test_start_listener_returns_task(self, temp_db):
        """Test that start_listener_in_background returns an asyncio task."""
        callback = AsyncMock()
        
        with patch('utils.bot_key.get_bot_env_key', return_value='BOT_TOKEN'):
            task = broadcast_forwarder.start_listener_in_background(temp_db, callback)
        
        assert isinstance(task, asyncio.Task)
        assert not task.done()
        
        # Cleanup
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    
    @pytest.mark.asyncio
    async def test_start_listener_wraps_3arg_callback(self, temp_db):
        """Test that start_listener_in_background automatically wraps 3-arg callbacks."""
        # Create a 3-arg callback that expects (db, context, message_text)
        # We need to create an actual async function with 3 parameters
        call_tracker = []
        
        async def three_arg_callback(db, context, message_text):
            """Mock 3-arg callback with proper signature."""
            call_tracker.append((db, context, message_text))
        
        # Create a broadcast event
        message_text = "Test 3-arg callback"
        payload = {"key": "value"}
        broadcast_id = await broadcast_forwarder.publish_broadcast(temp_db, message_text, payload)
        
        # Start listener with 3-arg callback
        with patch('utils.bot_key.get_bot_env_key', return_value='BOT_TOKEN'):
            task = broadcast_forwarder.start_listener_in_background(temp_db, three_arg_callback)
        
        # Wait for processing
        await asyncio.sleep(0.5)
        
        # Cancel the task
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        
        # Verify the 3-arg callback was called with db, context, and message_text
        assert len(call_tracker) >= 1
        db_arg, context_arg, message_arg = call_tracker[0]
        assert db_arg == temp_db
        assert context_arg == payload
        assert message_arg == message_text


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
