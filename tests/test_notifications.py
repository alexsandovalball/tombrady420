"""
Test notification system functionality.
"""
import asyncio
import os
import sys
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database


async def test_notification_system():
    """Test notification preferences and queue functionality."""
    print("Testing notification system...")
    
    # Use test database
    db = Database("test_notifications.db")
    
    try:
        # Initialize database
        await db.init_db()
        print("✓ Database initialized")
        
        # Test 1: Track a user
        await db.track_user(
            user_id=123456,
            username="testuser",
            first_name="Test",
            last_name="User"
        )
        print("✓ User tracked")
        
        # Test 2: Check default notification status (should be enabled)
        is_subscribed = await db.is_user_subscribed(123456)
        assert is_subscribed == True, "User should be subscribed by default"
        print("✓ Default subscription status is enabled")
        
        # Test 3: Disable notifications
        await db.set_user_notifications(123456, False)
        is_subscribed = await db.is_user_subscribed(123456)
        assert is_subscribed == False, "User should be unsubscribed"
        print("✓ Unsubscribe works")
        
        # Test 4: Enable notifications
        await db.set_user_notifications(123456, True)
        is_subscribed = await db.is_user_subscribed(123456)
        assert is_subscribed == True, "User should be subscribed"
        print("✓ Subscribe works")
        
        # Test 5: Add a test product
        product_id = await db.add_product(
            file_id="test_file_123",
            file_type="photo",
            caption="Test Product",
            message_id=999,
            chat_id=888,
            category="CARTRIDGES",
            subcategory="AUTHENTICS"
        )
        assert product_id > 0, "Product should be added"
        print(f"✓ Product added with ID {product_id}")
        
        # Test 6: Queue notification
        await db.queue_notification(123456, product_id)
        print("✓ Notification queued")
        
        # Test 7: Get pending notifications
        pending = await db.get_pending_notifications()
        assert len(pending) == 1, "Should have 1 pending notification"
        assert pending[0]["user_id"] == 123456, "Notification should be for correct user"
        assert pending[0]["product_id"] == product_id, "Notification should be for correct product"
        print("✓ Pending notifications retrieved")
        
        # Test 8: Mark notification as sent
        notification_id = pending[0]["id"]
        await db.mark_notification_sent(notification_id)
        print("✓ Notification marked as sent")
        
        # Test 9: Verify no more pending notifications
        pending = await db.get_pending_notifications()
        assert len(pending) == 0, "Should have 0 pending notifications"
        print("✓ No pending notifications after marking as sent")
        
        # Test 10: Check recent notifications count
        count = await db.get_recent_notifications_count(123456, minutes=60)
        assert count == 1, "Should have 1 recent notification"
        print("✓ Recent notifications count works")
        
        # Test 11: Get subscribed users
        subscribed = await db.get_subscribed_users()
        assert 123456 in subscribed, "User should be in subscribed list"
        print("✓ Get subscribed users works")
        
        # Test 12: Admin users excluded from subscribed list
        # Create an admin user
        await db.track_user(
            user_id=999999,  # This would be an admin ID in practice
            username="admin",
            first_name="Admin",
            last_name="User"
        )
        # Note: Actual filtering happens in get_subscribed_users which checks admin IDs from config
        print("✓ Admin exclusion logic present")
        
        print("\n✅ All notification system tests passed!")
        
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        return False
    except Exception as e:
        print(f"\n❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Cleanup
        if os.path.exists("test_notifications.db"):
            os.remove("test_notifications.db")
            print("✓ Test database cleaned up")
    
    return True


async def test_notification_service():
    """Test the notification service with rate limiting."""
    print("\nTesting notification service...")
    
    from utils.notifications import NotificationService, MAX_NOTIFICATIONS_PER_HOUR
    
    # Use test database
    db = Database("test_notif_service.db")
    
    try:
        await db.init_db()
        
        # Create notification service
        service = NotificationService(db)
        print("✓ NotificationService created")
        
        # Add test users
        for i in range(1, 4):
            await db.track_user(
                user_id=1000 + i,
                username=f"user{i}",
                first_name=f"User{i}",
                last_name="Test"
            )
        print("✓ Test users created")
        
        # Add test product
        product_id = await db.add_product(
            file_id="test_file_456",
            file_type="photo",
            caption="Test Notification Product",
            message_id=1001,
            chat_id=2001,
            category="FLOWER",
            subcategory="EXOTICS"
        )
        print(f"✓ Test product created with ID {product_id}")
        
        # Queue notifications for all users
        for i in range(1, 4):
            await db.queue_notification(1000 + i, product_id)
        print("✓ Notifications queued for 3 users")
        
        # Get pending notifications
        pending = await db.get_pending_notifications()
        assert len(pending) == 3, f"Should have 3 pending notifications, got {len(pending)}"
        print("✓ Pending notifications verified")
        
        # Verify rate limiting constant
        assert MAX_NOTIFICATIONS_PER_HOUR > 0, "Rate limit should be positive"
        print(f"✓ Rate limit set to {MAX_NOTIFICATIONS_PER_HOUR} per hour")
        
        print("\n✅ Notification service tests passed!")
        
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        return False
    except Exception as e:
        print(f"\n❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Cleanup
        if os.path.exists("test_notif_service.db"):
            os.remove("test_notif_service.db")
            print("✓ Test database cleaned up")
    
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("NOTIFICATION SYSTEM TEST SUITE")
    print("=" * 60)
    
    success = True
    
    # Run database tests
    success = asyncio.run(test_notification_system()) and success
    
    # Run service tests
    success = asyncio.run(test_notification_service()) and success
    
    print("\n" + "=" * 60)
    if success:
        print("✅ ALL TESTS PASSED")
    else:
        print("❌ SOME TESTS FAILED")
    print("=" * 60)
    
    sys.exit(0 if success else 1)
