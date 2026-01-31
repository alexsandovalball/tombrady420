"""
Test notification category filtering to ensure excluded categories don't send notifications.
Tests Issue #4: Notifications should not be sent for DATEDPROOFS and CLIENTTOUCHDOWNS.
"""
import asyncio
import os
import sys
import random
from unittest.mock import MagicMock, AsyncMock, patch

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from utils.notifications import NotificationService
from utils.categories import EXCLUDED_FROM_ALL_PRODUCTS


async def test_notification_filtering():
    """Test that notifications are filtered for excluded categories."""
    print("\n" + "="*70)
    print("Testing Notification Category Filtering (Issue #4)")
    print("="*70)
    
    # Use unique test database name
    db_name = f"test_notification_filtering_{random.randint(1000, 9999)}.db"
    
    # Clean up any existing test database
    if os.path.exists(db_name):
        os.remove(db_name)
    
    db = Database(db_name)
    
    try:
        # Initialize database
        await db.init_db()
        print("✓ Database initialized")
        
        # Track a test user
        await db.track_user(
            user_id=999999,
            username="testuser",
            first_name="Test",
            last_name="User"
        )
        
        # Ensure user is subscribed
        await db.set_user_notifications(999999, True)
        print("✓ Test user created and subscribed")
        
        # Use random message IDs to avoid conflicts
        base_msg_id = random.randint(10000, 99999)
        
        # Test 1: Add a product in DATEDPROOFS category
        product_id_dated = await db.add_product(
            file_id=f"test_file_dated_{base_msg_id}",
            file_type="photo",
            caption="Test dated proof product",
            message_id=base_msg_id + 1,
            chat_id=-1001234567890,
            category="DATEDPROOFS",
            subcategory=None
        )
        print(f"✓ Created test product in DATEDPROOFS: ID={product_id_dated}")
        
        # Test 2: Add a product in CLIENTTOUCHDOWNS category
        product_id_touchdown = await db.add_product(
            file_id=f"test_file_touchdown_{base_msg_id}",
            file_type="photo",
            caption="Test client touchdown product",
            message_id=base_msg_id + 2,
            chat_id=-1001234567890,
            category="CLIENTTOUCHDOWNS",
            subcategory=None
        )
        print(f"✓ Created test product in CLIENTTOUCHDOWNS: ID={product_id_touchdown}")
        
        # Test 3: Add a product in a normal category (FLOWER)
        product_id_normal = await db.add_product(
            file_id=f"test_file_normal_{base_msg_id}",
            file_type="photo",
            caption="Test normal product",
            message_id=base_msg_id + 3,
            chat_id=-1001234567890,
            category="FLOWER",
            subcategory="EXOTICS"
        )
        print(f"✓ Created test product in FLOWER: ID={product_id_normal}")
        
        # Create notification service with mocked context
        notification_service = NotificationService(db)
        
        # Mock the context object
        mock_context = MagicMock()
        mock_bot = AsyncMock()
        mock_context.bot = mock_bot
        
        # Test 4: Verify DATEDPROOFS product does NOT queue notifications
        print("\nTest: DATEDPROOFS should NOT send notifications")
        await notification_service.notify_users_about_product(mock_context, product_id_dated)
        
        # Check if any notifications were queued
        pending_dated = await db.get_pending_notifications(limit=100)
        dated_notifications = [n for n in pending_dated if n["product_id"] == product_id_dated]
        
        assert len(dated_notifications) == 0, "DATEDPROOFS should not create notifications"
        print("✓ DATEDPROOFS product correctly excluded from notifications")
        
        # Test 5: Verify CLIENTTOUCHDOWNS product does NOT queue notifications
        print("\nTest: CLIENTTOUCHDOWNS should NOT send notifications")
        await notification_service.notify_users_about_product(mock_context, product_id_touchdown)
        
        # Check if any notifications were queued
        pending_touchdown = await db.get_pending_notifications(limit=100)
        touchdown_notifications = [n for n in pending_touchdown if n["product_id"] == product_id_touchdown]
        
        assert len(touchdown_notifications) == 0, "CLIENTTOUCHDOWNS should not create notifications"
        print("✓ CLIENTTOUCHDOWNS product correctly excluded from notifications")
        
        # Test 6: Verify normal category DOES queue notifications
        print("\nTest: FLOWER should send notifications")
        await notification_service.notify_users_about_product(mock_context, product_id_normal)
        
        # Check if notifications were queued
        pending_normal = await db.get_pending_notifications(limit=100)
        normal_notifications = [n for n in pending_normal if n["product_id"] == product_id_normal]
        
        assert len(normal_notifications) == 1, "FLOWER should create notifications"
        print(f"✓ FLOWER product correctly queued {len(normal_notifications)} notification(s)")
        
        # Test 7: Verify the EXCLUDED_FROM_ALL_PRODUCTS list is correct
        print(f"\nTest: Verify excluded categories list")
        print(f"  Excluded categories: {EXCLUDED_FROM_ALL_PRODUCTS}")
        assert "DATEDPROOFS" in EXCLUDED_FROM_ALL_PRODUCTS, "DATEDPROOFS should be in excluded list"
        assert "CLIENTTOUCHDOWNS" in EXCLUDED_FROM_ALL_PRODUCTS, "CLIENTTOUCHDOWNS should be in excluded list"
        print("✓ Excluded categories list is correct")
        
        print("\n" + "="*70)
        print("✅ ALL TESTS PASSED - Notification filtering works correctly!")
        print("="*70)
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        raise
    
    finally:
        # Cleanup - Database doesn't have a close method, just delete the file
        if os.path.exists(db_name):
            os.remove(db_name)
            print("\n✓ Test database cleaned up")


if __name__ == "__main__":
    asyncio.run(test_notification_filtering())
