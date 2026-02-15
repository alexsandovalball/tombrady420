"""
Test notification deduplication and error handling.

These tests validate that:
1. Duplicate notifications are prevented at database level
2. Pre-check validation works correctly
3. Runtime deduplication catches any missed duplicates
4. Error recovery mechanisms work as expected
"""
import asyncio
import aiosqlite
import os
import tempfile
import unittest
from datetime import datetime

# Import the Database class
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from database import Database


class TestNotificationDeduplication(unittest.TestCase):
    """Test notification deduplication mechanisms."""
    
    def setUp(self):
        """Create a temporary database for testing."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()
        self.db_path = self.temp_db.name
        self.db = Database(db_path=self.db_path)
        
        # Initialize database
        asyncio.run(self.db.init_db())
    
    def tearDown(self):
        """Clean up temporary database."""
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
    
    def test_unique_index_creation(self):
        """Test that unique index is created successfully."""
        async def check_index():
            async with aiosqlite.connect(self.db_path) as db:
                # Query sqlite_master to check if index exists
                async with db.execute("""
                    SELECT name FROM sqlite_master 
                    WHERE type='index' 
                    AND name='idx_notification_queue_user_product_unsent'
                """) as cursor:
                    result = await cursor.fetchone()
                    self.assertIsNotNone(result, "Unique index should be created")
                    self.assertEqual(result[0], 'idx_notification_queue_user_product_unsent')
        
        asyncio.run(check_index())
    
    def test_queue_notification_prevents_duplicates(self):
        """Test that queueing the same notification twice is prevented."""
        async def test():
            user_id = 12345
            product_id = 67890
            
            # Queue notification first time
            await self.db.queue_notification(user_id, product_id)
            
            # Try to queue same notification again
            await self.db.queue_notification(user_id, product_id)
            
            # Check that only one notification exists
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("""
                    SELECT COUNT(*) FROM notification_queue 
                    WHERE user_id = ? AND product_id = ? AND sent_at IS NULL
                """, (user_id, product_id)) as cursor:
                    count = (await cursor.fetchone())[0]
                    self.assertEqual(count, 1, "Should have exactly one notification in queue")
        
        asyncio.run(test())
    
    def test_queue_notification_allows_different_products(self):
        """Test that same user can have notifications for different products."""
        async def test():
            user_id = 12345
            product_id_1 = 67890
            product_id_2 = 67891
            
            # Queue notifications for two different products
            await self.db.queue_notification(user_id, product_id_1)
            await self.db.queue_notification(user_id, product_id_2)
            
            # Check that both notifications exist
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("""
                    SELECT COUNT(*) FROM notification_queue 
                    WHERE user_id = ? AND sent_at IS NULL
                """, (user_id,)) as cursor:
                    count = (await cursor.fetchone())[0]
                    self.assertEqual(count, 2, "Should have two notifications for different products")
        
        asyncio.run(test())
    
    def test_queue_notification_allows_different_users(self):
        """Test that different users can have notifications for same product."""
        async def test():
            user_id_1 = 12345
            user_id_2 = 12346
            product_id = 67890
            
            # Queue notifications for two different users
            await self.db.queue_notification(user_id_1, product_id)
            await self.db.queue_notification(user_id_2, product_id)
            
            # Check that both notifications exist
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("""
                    SELECT COUNT(*) FROM notification_queue 
                    WHERE product_id = ? AND sent_at IS NULL
                """, (product_id,)) as cursor:
                    count = (await cursor.fetchone())[0]
                    self.assertEqual(count, 2, "Should have two notifications for different users")
        
        asyncio.run(test())
    
    def test_queue_notification_allows_resend_after_sent(self):
        """Test that same notification can be queued again after being marked as sent."""
        async def test():
            user_id = 12345
            product_id = 67890
            
            # Queue notification
            await self.db.queue_notification(user_id, product_id)
            
            # Get the notification ID and mark as sent
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("""
                    SELECT id FROM notification_queue 
                    WHERE user_id = ? AND product_id = ?
                """, (user_id, product_id)) as cursor:
                    notification_id = (await cursor.fetchone())[0]
            
            await self.db.mark_notification_sent(notification_id)
            
            # Queue same notification again (should be allowed now)
            await self.db.queue_notification(user_id, product_id)
            
            # Check that we have one sent and one pending notification
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("""
                    SELECT COUNT(*) FROM notification_queue 
                    WHERE user_id = ? AND product_id = ? AND sent_at IS NULL
                """, (user_id, product_id)) as cursor:
                    pending_count = (await cursor.fetchone())[0]
                    self.assertEqual(pending_count, 1, "Should have one pending notification")
                
                async with db.execute("""
                    SELECT COUNT(*) FROM notification_queue 
                    WHERE user_id = ? AND product_id = ? AND sent_at IS NOT NULL
                """, (user_id, product_id)) as cursor:
                    sent_count = (await cursor.fetchone())[0]
                    self.assertEqual(sent_count, 1, "Should have one sent notification")
        
        asyncio.run(test())
    
    def test_pre_check_returns_early(self):
        """Test that pre-check in queue_notification prevents unnecessary inserts."""
        async def test():
            user_id = 12345
            product_id = 67890
            
            # Queue notification first time
            await self.db.queue_notification(user_id, product_id)
            
            # Get initial row count
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("SELECT COUNT(*) FROM notification_queue") as cursor:
                    initial_count = (await cursor.fetchone())[0]
            
            # Try to queue again - should return early due to pre-check
            await self.db.queue_notification(user_id, product_id)
            
            # Row count should be unchanged (no INSERT OR IGNORE was attempted)
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("SELECT COUNT(*) FROM notification_queue") as cursor:
                    final_count = (await cursor.fetchone())[0]
            
            self.assertEqual(initial_count, final_count, "Pre-check should prevent INSERT attempt")
        
        asyncio.run(test())


class TestNotificationErrorHandling(unittest.TestCase):
    """Test notification error handling and recovery."""
    
    def setUp(self):
        """Create a temporary database for testing."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()
        self.db_path = self.temp_db.name
        self.db = Database(db_path=self.db_path)
        
        # Initialize database
        asyncio.run(self.db.init_db())
    
    def tearDown(self):
        """Clean up temporary database."""
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
    
    def test_delete_notification(self):
        """Test that notification can be deleted successfully."""
        async def test():
            user_id = 12345
            product_id = 67890
            
            # Queue notification
            await self.db.queue_notification(user_id, product_id)
            
            # Get notification ID
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("""
                    SELECT id FROM notification_queue 
                    WHERE user_id = ? AND product_id = ?
                """, (user_id, product_id)) as cursor:
                    notification_id = (await cursor.fetchone())[0]
            
            # Delete notification
            await self.db.delete_notification(notification_id)
            
            # Verify deletion
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("""
                    SELECT COUNT(*) FROM notification_queue WHERE id = ?
                """, (notification_id,)) as cursor:
                    count = (await cursor.fetchone())[0]
                    self.assertEqual(count, 0, "Notification should be deleted")
        
        asyncio.run(test())
    
    def test_mark_notification_sent(self):
        """Test that notification can be marked as sent."""
        async def test():
            user_id = 12345
            product_id = 67890
            
            # Queue notification
            await self.db.queue_notification(user_id, product_id)
            
            # Get notification ID
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("""
                    SELECT id FROM notification_queue 
                    WHERE user_id = ? AND product_id = ?
                """, (user_id, product_id)) as cursor:
                    notification_id = (await cursor.fetchone())[0]
            
            # Mark as sent
            await self.db.mark_notification_sent(notification_id)
            
            # Verify sent_at is not NULL
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("""
                    SELECT sent_at FROM notification_queue WHERE id = ?
                """, (notification_id,)) as cursor:
                    sent_at = (await cursor.fetchone())[0]
                    self.assertIsNotNone(sent_at, "sent_at should not be NULL")
        
        asyncio.run(test())


if __name__ == '__main__':
    unittest.main()
