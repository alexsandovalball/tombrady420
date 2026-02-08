"""
Tests for the DB-backed broadcast forwarder.

Tests the functionality of DB-backed broadcast event publishing and processing
across multiple bot instances without Redis.
"""
import os
import sys
import pytest
import pytest_asyncio
import asyncio
import tempfile
import json
from datetime import datetime
from unittest.mock import Mock, AsyncMock, patch

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from utils.bot_key import get_bot_env_key
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


class TestBroadcastPublishing:
    """Test broadcast event publishing."""
    
    @pytest.mark.asyncio
    async def test_publish_broadcast_basic(self, temp_db):
        """Test that publish_broadcast inserts an event and returns its ID."""
        message = "Test broadcast message"
        event_id = await broadcast_forwarder.publish_broadcast(temp_db, message)
        
        assert event_id > 0
        
        # Verify the event was inserted
        events = await temp_db.get_unprocessed_broadcasts_for_bot(None)
        assert len(events) == 1
        assert events[0]['id'] == event_id
        assert events[0]['message_text'] == message
        assert events[0]['payload_json'] is None
    
    @pytest.mark.asyncio
    async def test_publish_broadcast_with_payload(self, temp_db):
        """Test publishing broadcast with JSON payload."""
        message = "Test broadcast with payload"
        payload = {"key": "value", "number": 42}
        
        event_id = await broadcast_forwarder.publish_broadcast(temp_db, message, payload)
        
        events = await temp_db.get_unprocessed_broadcasts_for_bot(None)
        assert len(events) == 1
        assert events[0]['message_text'] == message
        
        # Verify payload was stored and can be parsed
        stored_payload = json.loads(events[0]['payload_json'])
        assert stored_payload == payload
    
    @pytest.mark.asyncio
    async def test_publish_multiple_broadcasts(self, temp_db):
        """Test publishing multiple broadcast events."""
        messages = ["Message 1", "Message 2", "Message 3"]
        event_ids = []
        
        for msg in messages:
            event_id = await broadcast_forwarder.publish_broadcast(temp_db, msg)
            event_ids.append(event_id)
        
        # Verify all events were inserted
        events = await temp_db.get_unprocessed_broadcasts_for_bot(None)
        assert len(events) == 3
        
        # Verify IDs are sequential
        assert event_ids == sorted(event_ids)
        
        # Verify messages match
        stored_messages = [e['message_text'] for e in events]
        assert stored_messages == messages


class TestBroadcastProcessing:
    """Test broadcast event processing and marking."""
    
    @pytest.mark.asyncio
    async def test_get_unprocessed_broadcasts_for_bot(self, temp_db):
        """Test getting unprocessed broadcasts for a specific bot."""
        # Insert a broadcast
        event_id = await broadcast_forwarder.publish_broadcast(temp_db, "Test message")
        
        # Get unprocessed for BOT_TOKEN
        events = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN')
        assert len(events) == 1
        assert events[0]['id'] == event_id
        
        # Mark as processed for BOT_TOKEN
        await temp_db.mark_broadcast_processed(event_id, 'BOT_TOKEN')
        
        # Should now be empty for BOT_TOKEN
        events = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN')
        assert len(events) == 0
        
        # But should still be unprocessed for BOT_TOKEN_1
        events = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN_1')
        assert len(events) == 1
    
    @pytest.mark.asyncio
    async def test_mark_broadcast_processed_prevents_duplicates(self, temp_db):
        """Test that marking as processed prevents duplicate processing."""
        event_id = await broadcast_forwarder.publish_broadcast(temp_db, "Test message")
        
        # Mark as processed twice (should not error)
        await temp_db.mark_broadcast_processed(event_id, 'BOT_TOKEN')
        await temp_db.mark_broadcast_processed(event_id, 'BOT_TOKEN')
        
        # Should still be marked as processed
        events = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN')
        assert len(events) == 0
    
    @pytest.mark.asyncio
    async def test_multiple_bots_process_same_event(self, temp_db):
        """Test that multiple bots can independently process the same event."""
        event_id = await broadcast_forwarder.publish_broadcast(temp_db, "Shared message")
        
        # Both bots should see the unprocessed event
        events_bot1 = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN')
        events_bot2 = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN_1')
        
        assert len(events_bot1) == 1
        assert len(events_bot2) == 1
        assert events_bot1[0]['id'] == events_bot2[0]['id']
        
        # Mark as processed by first bot
        await temp_db.mark_broadcast_processed(event_id, 'BOT_TOKEN')
        
        # First bot should not see it anymore
        events_bot1 = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN')
        assert len(events_bot1) == 0
        
        # Second bot should still see it
        events_bot2 = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN_1')
        assert len(events_bot2) == 1
        
        # Mark as processed by second bot
        await temp_db.mark_broadcast_processed(event_id, 'BOT_TOKEN_1')
        
        # Now both should have processed it
        events_bot1 = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN')
        events_bot2 = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN_1')
        
        assert len(events_bot1) == 0
        assert len(events_bot2) == 0


class TestBroadcastListener:
    """Test the broadcast listener loop."""
    
    @pytest.mark.asyncio
    async def test_listener_processes_event(self, temp_db):
        """Test that the listener processes a broadcast event."""
        # Create a mock callback
        callback_called = asyncio.Event()
        received_message = []
        
        async def mock_callback(context: dict, message_text: str):
            received_message.append(message_text)
            callback_called.set()
        
        # Publish a broadcast event and run listener with patch
        test_message = "Test listener message"
        with patch('utils.broadcast_forwarder.get_bot_env_key', return_value='BOT_TOKEN'):
            event_id = await broadcast_forwarder.publish_broadcast(temp_db, test_message)
            
            # Start listener (run it for a short time)
            listener_task = asyncio.create_task(
                broadcast_forwarder._listener_loop(temp_db, mock_callback)
            )
            
            # Wait for callback to be called (with timeout)
            try:
                await asyncio.wait_for(callback_called.wait(), timeout=5.0)
                # Add a small delay to ensure mark_broadcast_processed completes
                await asyncio.sleep(0.1)
            finally:
                listener_task.cancel()
                try:
                    await listener_task
                except asyncio.CancelledError:
                    pass
            
            # Verify callback was called with correct message
            assert len(received_message) == 1
            assert received_message[0] == test_message
            
            # Verify event was marked as processed (inside the patch context)
            events = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN')
            assert len(events) == 0
    
    @pytest.mark.asyncio
    async def test_listener_handles_callback_error(self, temp_db):
        """Test that listener marks event as processed even if callback fails."""
        # Create a callback that raises an error
        async def failing_callback(context: dict, message_text: str):
            raise ValueError("Simulated callback error")
        
        # Publish a broadcast event
        with patch('utils.broadcast_forwarder.get_bot_env_key', return_value='BOT_TOKEN'):
            event_id = await broadcast_forwarder.publish_broadcast(temp_db, "Test message")
            
            # Start listener
            listener_task = asyncio.create_task(
                broadcast_forwarder._listener_loop(temp_db, failing_callback)
            )
            
            # Give it time to process (callback will fail)
            await asyncio.sleep(0.5)
            
            # Cancel listener
            listener_task.cancel()
            try:
                await listener_task
            except asyncio.CancelledError:
                pass
            
            # Event should still be marked as processed (to avoid infinite retries)
            events = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN')
            assert len(events) == 0
    
    @pytest.mark.asyncio
    async def test_listener_processes_multiple_events(self, temp_db):
        """Test that listener processes multiple events in sequence."""
        processed_messages = []
        all_processed = asyncio.Event()
        
        async def mock_callback(context: dict, message_text: str):
            processed_messages.append(message_text)
            if len(processed_messages) >= 3:
                all_processed.set()
        
        # Publish multiple events and run listener with patch
        messages = ["Message 1", "Message 2", "Message 3"]
        with patch('utils.broadcast_forwarder.get_bot_env_key', return_value='BOT_TOKEN'):
            for msg in messages:
                await broadcast_forwarder.publish_broadcast(temp_db, msg)
            
            # Start listener
            listener_task = asyncio.create_task(
                broadcast_forwarder._listener_loop(temp_db, mock_callback)
            )
            
            # Wait for all to be processed
            try:
                await asyncio.wait_for(all_processed.wait(), timeout=10.0)
                # Add a small delay to ensure all marks complete
                await asyncio.sleep(0.1)
            finally:
                listener_task.cancel()
                try:
                    await listener_task
                except asyncio.CancelledError:
                    pass
            
            # Verify all messages were processed
            assert len(processed_messages) == 3
            assert sorted(processed_messages) == sorted(messages)
            
            # Verify all events are marked as processed (inside patch context)
            events = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN')
            assert len(events) == 0
    
    @pytest.mark.asyncio
    async def test_listener_with_payload(self, temp_db):
        """Test that listener correctly parses and passes payload."""
        received_payload = []
        callback_called = asyncio.Event()
        
        async def mock_callback(context: dict, message_text: str):
            received_payload.append(context)
            callback_called.set()
        
        # Publish event with payload
        payload = {"user_id": 123, "action": "test"}
        with patch('utils.broadcast_forwarder.get_bot_env_key', return_value='BOT_TOKEN'):
            await broadcast_forwarder.publish_broadcast(
                temp_db, 
                "Message with payload",
                payload
            )
            
            # Start listener
            listener_task = asyncio.create_task(
                broadcast_forwarder._listener_loop(temp_db, mock_callback)
            )
            
            # Wait for processing
            try:
                await asyncio.wait_for(callback_called.wait(), timeout=5.0)
            finally:
                listener_task.cancel()
                try:
                    await listener_task
                except asyncio.CancelledError:
                    pass
            
            # Verify payload was correctly parsed
            assert len(received_payload) == 1
            assert received_payload[0] == payload


class TestBroadcastIntegration:
    """Integration tests for the complete broadcast flow."""
    
    @pytest.mark.asyncio
    async def test_end_to_end_broadcast(self, temp_db):
        """Test complete broadcast flow from publish to processing by multiple bots."""
        # Track processing by each bot
        bot1_messages = []
        bot2_messages = []
        bot1_done = asyncio.Event()
        bot2_done = asyncio.Event()
        
        async def bot1_callback(context: dict, message_text: str):
            bot1_messages.append(message_text)
            bot1_done.set()
        
        async def bot2_callback(context: dict, message_text: str):
            bot2_messages.append(message_text)
            bot2_done.set()
        
        # Publish a broadcast
        test_message = "End-to-end test message"
        event_id = await broadcast_forwarder.publish_broadcast(temp_db, test_message)
        
        # Create a counter to alternate bot keys
        call_count = [0]
        
        def mock_get_bot_env_key():
            # Alternate between BOT_TOKEN and BOT_TOKEN_1 based on which task calls
            # This is a bit of a hack but works for the test
            call_count[0] += 1
            return 'BOT_TOKEN' if call_count[0] % 2 == 1 else 'BOT_TOKEN_1'
        
        with patch('utils.broadcast_forwarder.get_bot_env_key', side_effect=mock_get_bot_env_key):
            # Start both listeners
            bot1_task = asyncio.create_task(
                broadcast_forwarder._listener_loop(temp_db, bot1_callback)
            )
            
            bot2_task = asyncio.create_task(
                broadcast_forwarder._listener_loop(temp_db, bot2_callback)
            )
            
            # Wait for both to process
            try:
                await asyncio.wait_for(
                    asyncio.gather(bot1_done.wait(), bot2_done.wait()),
                    timeout=10.0
                )
                # Add a small delay to ensure marks complete
                await asyncio.sleep(0.1)
            finally:
                bot1_task.cancel()
                bot2_task.cancel()
                try:
                    await asyncio.gather(bot1_task, bot2_task, return_exceptions=True)
                except:
                    pass
            
            # Verify both bots processed the message
            assert bot1_messages == [test_message]
            assert bot2_messages == [test_message]
            
            # Verify event is marked as processed by both (inside patch context)
            events_bot1 = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN')
            events_bot2 = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN_1')
            
            assert len(events_bot1) == 0
            assert len(events_bot2) == 0
