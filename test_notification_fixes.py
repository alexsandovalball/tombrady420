"""
Test notification system fixes for button callbacks and staggered delivery.
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from utils.notifications import (
    NotificationService, 
    ENABLE_STAGGERED_DELIVERY,
    STAGGER_GROUP_SIZE,
    STAGGER_INTERVAL_SECONDS
)


async def test_product_button_callback_format():
    """Test that the 'View Product' button callback format is correct."""
    print("Testing 'View Product' button callback format...")
    
    # Use test database
    db = Database("test_button_callback.db")
    
    try:
        await db.init_db()
        
        # Create notification service
        service = NotificationService(db)
        
        # Add test user
        await db.track_user(
            user_id=5000,
            username="testbutton",
            first_name="Test",
            last_name="Button"
        )
        
        # Add test product
        product_id = await db.add_product(
            file_id="test_file_btn",
            file_type="photo",
            caption="Test Button Product",
            message_id=5001,
            chat_id=5002,
            category="FLOWER",
            subcategory="EXOTICS"
        )
        
        # Queue notification
        await db.queue_notification(5000, product_id)
        
        # Mock context and bot
        mock_context = MagicMock()
        mock_bot = AsyncMock()
        mock_context.bot = mock_bot
        
        # Track what callback_data was sent
        sent_callback_data = None
        
        async def mock_send_message(chat_id, text, parse_mode=None, reply_markup=None):
            nonlocal sent_callback_data
            if reply_markup and reply_markup.inline_keyboard:
                # Get the "View Product" button (first button in first row)
                view_button = reply_markup.inline_keyboard[0][0]
                sent_callback_data = view_button.callback_data
            return MagicMock(message_id=1)
        
        mock_bot.send_message = mock_send_message
        
        # Process notification
        await service.process_notification_queue(mock_context)
        
        # Verify callback format is correct (should be "product|{id}" not "product|{id}|1")
        assert sent_callback_data is not None, "No callback data was sent"
        parts = sent_callback_data.split("|")
        assert len(parts) == 2, f"Callback should have 2 parts, got {len(parts)}: {sent_callback_data}"
        assert parts[0] == "product", f"First part should be 'product', got '{parts[0]}'"
        assert parts[1] == str(product_id), f"Second part should be product_id {product_id}, got '{parts[1]}'"
        
        print(f"✓ Callback format is correct: {sent_callback_data}")
        
    except AssertionError as e:
        print(f"❌ Test failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if os.path.exists("test_button_callback.db"):
            os.remove("test_button_callback.db")
    
    return True


async def test_unsubscribe_button():
    """Test that the 'Unsubscribe' button callback is present."""
    print("\nTesting 'Unsubscribe' button...")
    
    db = Database("test_unsub_button.db")
    
    try:
        await db.init_db()
        
        service = NotificationService(db)
        
        # Add test user
        await db.track_user(
            user_id=6000,
            username="testunsub",
            first_name="Test",
            last_name="Unsub"
        )
        
        # Add test product
        product_id = await db.add_product(
            file_id="test_file_unsub",
            file_type="photo",
            caption="Test Unsub Product",
            message_id=6001,
            chat_id=6002,
            category="CARTRIDGES",
            subcategory="AUTHENTICS"
        )
        
        # Queue notification
        await db.queue_notification(6000, product_id)
        
        # Mock context
        mock_context = MagicMock()
        mock_bot = AsyncMock()
        mock_context.bot = mock_bot
        
        unsub_callback_found = False
        
        async def mock_send_message(chat_id, text, parse_mode=None, reply_markup=None):
            nonlocal unsub_callback_found
            if reply_markup and reply_markup.inline_keyboard:
                # Get the "Unsubscribe" button (second row)
                if len(reply_markup.inline_keyboard) > 1:
                    unsub_button = reply_markup.inline_keyboard[1][0]
                    if unsub_button.callback_data == "unsubscribe_notifications":
                        unsub_callback_found = True
            return MagicMock(message_id=1)
        
        mock_bot.send_message = mock_send_message
        
        # Process notification
        await service.process_notification_queue(mock_context)
        
        assert unsub_callback_found, "Unsubscribe button not found or has wrong callback_data"
        print("✓ Unsubscribe button is correctly configured")
        
    except AssertionError as e:
        print(f"❌ Test failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if os.path.exists("test_unsub_button.db"):
            os.remove("test_unsub_button.db")
    
    return True


async def test_staggered_delivery():
    """Test that staggered delivery groups users correctly."""
    print("\nTesting staggered delivery system...")
    
    db = Database("test_staggered.db")
    
    try:
        await db.init_db()
        
        service = NotificationService(db)
        
        # Create more users than STAGGER_GROUP_SIZE to trigger staggering
        num_users = STAGGER_GROUP_SIZE * 2 + 5  # e.g., 25 users if group size is 10
        print(f"Creating {num_users} test users (group size: {STAGGER_GROUP_SIZE})...")
        
        for i in range(1, num_users + 1):
            await db.track_user(
                user_id=7000 + i,
                username=f"user{i}",
                first_name=f"User{i}",
                last_name="Test"
            )
        
        # Add test product
        product_id = await db.add_product(
            file_id="test_file_stagger",
            file_type="photo",
            caption="Test Staggered Product",
            message_id=7001,
            chat_id=7002,
            category="EDIBLES",
            subcategory="GUMMIES"
        )
        
        # Queue notifications for all users
        for i in range(1, num_users + 1):
            await db.queue_notification(7000 + i, product_id)
        
        print(f"✓ Queued {num_users} notifications")
        
        # Mock context
        mock_context = MagicMock()
        mock_bot = AsyncMock()
        mock_context.bot = mock_bot
        
        # Track timing of message sends to verify staggering
        send_times = []
        start_time = asyncio.get_event_loop().time()
        
        async def mock_send_message(chat_id, text, parse_mode=None, reply_markup=None):
            send_times.append(asyncio.get_event_loop().time() - start_time)
            return MagicMock(message_id=1)
        
        mock_bot.send_message = mock_send_message
        
        # Process notifications
        await service.process_notification_queue(mock_context)
        
        # Verify all notifications were sent
        assert len(send_times) == num_users, f"Expected {num_users} messages sent, got {len(send_times)}"
        print(f"✓ All {num_users} notifications sent")
        
        # If staggered delivery is enabled, verify grouping
        if ENABLE_STAGGERED_DELIVERY and num_users > STAGGER_GROUP_SIZE:
            # Calculate expected groups
            expected_groups = (num_users + STAGGER_GROUP_SIZE - 1) // STAGGER_GROUP_SIZE
            print(f"✓ Expected {expected_groups} groups with stagger interval of {STAGGER_INTERVAL_SECONDS}s")
            
            # Verify that there were delays between groups
            # The last message should have been sent after at least (groups-1) * STAGGER_INTERVAL_SECONDS
            min_expected_time = (expected_groups - 1) * STAGGER_INTERVAL_SECONDS
            actual_total_time = send_times[-1] - send_times[0] if len(send_times) > 1 else 0
            
            print(f"✓ Total delivery time: {actual_total_time:.1f}s (minimum expected: {min_expected_time}s)")
            
            # Verify staggering occurred (with some tolerance for execution time)
            assert actual_total_time >= min_expected_time * 0.9, \
                f"Staggering not detected: total time {actual_total_time:.1f}s < expected {min_expected_time}s"
            
            print("✓ Staggered delivery working correctly")
        else:
            print("✓ Standard batch processing used (staggering disabled or small user count)")
        
    except AssertionError as e:
        print(f"❌ Test failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if os.path.exists("test_staggered.db"):
            os.remove("test_staggered.db")
    
    return True


async def test_staggered_configuration():
    """Test that staggered delivery configuration is correct."""
    print("\nTesting staggered delivery configuration...")
    
    try:
        # Verify configuration constants exist
        assert isinstance(ENABLE_STAGGERED_DELIVERY, bool), "ENABLE_STAGGERED_DELIVERY should be boolean"
        assert isinstance(STAGGER_GROUP_SIZE, int) and STAGGER_GROUP_SIZE > 0, \
            "STAGGER_GROUP_SIZE should be positive integer"
        assert isinstance(STAGGER_INTERVAL_SECONDS, int) and STAGGER_INTERVAL_SECONDS > 0, \
            "STAGGER_INTERVAL_SECONDS should be positive integer"
        
        print(f"✓ ENABLE_STAGGERED_DELIVERY = {ENABLE_STAGGERED_DELIVERY}")
        print(f"✓ STAGGER_GROUP_SIZE = {STAGGER_GROUP_SIZE}")
        print(f"✓ STAGGER_INTERVAL_SECONDS = {STAGGER_INTERVAL_SECONDS}")
        
        # Verify reasonable values
        assert STAGGER_INTERVAL_SECONDS in [30, 60], \
            f"STAGGER_INTERVAL_SECONDS should be 30 or 60, got {STAGGER_INTERVAL_SECONDS}"
        
        print("✓ Configuration values are valid")
        
    except AssertionError as e:
        print(f"❌ Test failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()
        return False
    
    return True


async def test_rate_limiting_with_staggering():
    """Test that rate limiting still works with staggered delivery."""
    print("\nTesting rate limiting with staggered delivery...")
    
    db = Database("test_rate_limit_stagger.db")
    
    try:
        await db.init_db()
        
        service = NotificationService(db)
        
        # Create a user
        await db.track_user(
            user_id=8000,
            username="ratelimituser",
            first_name="Rate",
            last_name="Limit"
        )
        
        # Add multiple products
        product_ids = []
        for i in range(7):  # More than MAX_NOTIFICATIONS_PER_HOUR (5)
            product_id = await db.add_product(
                file_id=f"test_file_rate_{i}",
                file_type="photo",
                caption=f"Test Rate Limit Product {i}",
                message_id=8000 + i,
                chat_id=8100 + i,
                category="FLOWER",
                subcategory="EXOTICS"
            )
            product_ids.append(product_id)
        
        # Queue all notifications
        for product_id in product_ids:
            await db.queue_notification(8000, product_id)
        
        # Mock context
        mock_context = MagicMock()
        mock_bot = AsyncMock()
        mock_context.bot = mock_bot
        
        messages_sent = 0
        
        async def mock_send_message(chat_id, text, parse_mode=None, reply_markup=None):
            nonlocal messages_sent
            messages_sent += 1
            return MagicMock(message_id=1)
        
        mock_bot.send_message = mock_send_message
        
        # Process notifications
        await service.process_notification_queue(mock_context)
        
        # Verify rate limiting: should send max 5 notifications
        from utils.notifications import MAX_NOTIFICATIONS_PER_HOUR
        assert messages_sent <= MAX_NOTIFICATIONS_PER_HOUR, \
            f"Rate limiting failed: sent {messages_sent} messages, max allowed {MAX_NOTIFICATIONS_PER_HOUR}"
        
        print(f"✓ Rate limiting working: sent {messages_sent}/{MAX_NOTIFICATIONS_PER_HOUR} max notifications")
        
        # Verify remaining notifications are still queued
        pending = await db.get_pending_notifications()
        expected_pending = len(product_ids) - messages_sent
        assert len(pending) == expected_pending, \
            f"Expected {expected_pending} pending notifications, got {len(pending)}"
        
        print(f"✓ {len(pending)} notifications still queued for later delivery")
        
    except AssertionError as e:
        print(f"❌ Test failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if os.path.exists("test_rate_limit_stagger.db"):
            os.remove("test_rate_limit_stagger.db")
    
    return True


if __name__ == "__main__":
    print("=" * 70)
    print("NOTIFICATION SYSTEM FIXES TEST SUITE")
    print("=" * 70)
    
    success = True
    
    # Test button callback fixes
    print("\n" + "=" * 70)
    print("TESTING BUTTON CALLBACK FIXES")
    print("=" * 70)
    success = asyncio.run(test_product_button_callback_format()) and success
    success = asyncio.run(test_unsubscribe_button()) and success
    
    # Test staggered delivery
    print("\n" + "=" * 70)
    print("TESTING STAGGERED DELIVERY")
    print("=" * 70)
    success = asyncio.run(test_staggered_configuration()) and success
    success = asyncio.run(test_staggered_delivery()) and success
    success = asyncio.run(test_rate_limiting_with_staggering()) and success
    
    print("\n" + "=" * 70)
    if success:
        print("✅ ALL TESTS PASSED")
    else:
        print("❌ SOME TESTS FAILED")
    print("=" * 70)
    
    sys.exit(0 if success else 1)
