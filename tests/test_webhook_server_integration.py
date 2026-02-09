"""
Integration test to verify that the webhook_server.py usage pattern works correctly.

This test specifically validates that passing enqueue_local_broadcast directly
(as done in webhook_server.py line 93) now works correctly with the signature
normalization fix.
"""
import os
import sys
import pytest
import pytest_asyncio
import asyncio
import tempfile
import aiosqlite

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from utils import broadcast_forwarder
from utils.notifications import enqueue_local_broadcast
from unittest.mock import patch


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


class TestWebhookServerPattern:
    """Test the usage pattern from webhook_server.py"""
    
    @pytest.mark.asyncio
    async def test_enqueue_local_broadcast_passed_directly(self, temp_db):
        """
        Test that passing enqueue_local_broadcast directly works (webhook_server.py pattern).
        
        This replicates the code from webhook_server.py line 93:
            listener_task = broadcast_forwarder.start_listener_in_background(db, enqueue_local_broadcast)
        """
        # Mock the bot username/key to avoid environment dependencies
        with patch('utils.bot_key.get_bot_env_key', return_value='BOT_TOKEN'):
            with patch('utils.bot_key.get_bot_username', return_value='test_bot'):
                # Add a test user for this bot
                await temp_db.track_user(12345, username='testuser', bot_username='test_bot')
                await temp_db.set_user_notifications(12345, True)
                
                # Create a broadcast event
                message_text = "Test broadcast message"
                payload = {"target_user_id": 12345}
                broadcast_id = await broadcast_forwarder.publish_broadcast(temp_db, message_text, payload)
                
                # This is the pattern from webhook_server.py - should work without error
                listener_task = broadcast_forwarder.start_listener_in_background(temp_db, enqueue_local_broadcast)
                
                # Wait for processing
                await asyncio.sleep(0.5)
                
                # Cancel the task
                listener_task.cancel()
                try:
                    await listener_task
                except asyncio.CancelledError:
                    pass
                
                # Verify the broadcast was processed (marked as processed)
                events = await temp_db.get_unprocessed_broadcasts_for_bot('BOT_TOKEN')
                assert len(events) == 0, "Broadcast should have been processed"
                
                # Verify a message was queued for the user
                async with aiosqlite.connect(temp_db.db_path) as db:
                    async with db.execute("SELECT COUNT(*) as count FROM custom_message_queue WHERE user_id = ?", (12345,)) as cursor:
                        row = await cursor.fetchone()
                        assert row[0] > 0, "Message should have been queued for user"


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
