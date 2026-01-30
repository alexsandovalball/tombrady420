"""
Test fixes for:
1. Block feature preventing callback access
2. Unsubscribe confirmation prompt
3. Notification toggle functionality
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from telegram import Update, CallbackQuery, User, Message, Chat
from telegram.ext import ContextTypes


async def test_blocked_user_callback_prevention():
    """Test that blocked users cannot use callback buttons."""
    print("Testing blocked user callback prevention...")
    
    # Use test database
    db = Database("test_block_callback.db")
    
    try:
        await db.init_db()
        
        # Add test user
        test_user_id = 12345
        await db.track_user(
            user_id=test_user_id,
            username="testuser",
            first_name="Test",
            last_name="User"
        )
        
        # Block the user
        await db.block_user(test_user_id)
        
        # Verify user is blocked
        is_blocked = await db.is_user_blocked(test_user_id)
        assert is_blocked == True, "User should be blocked"
        print("✓ User successfully blocked in database")
        
        # Mock callback query from blocked user
        mock_user = MagicMock(spec=User)
        mock_user.id = test_user_id
        mock_user.username = "testuser"
        mock_user.first_name = "Test"
        
        mock_chat = MagicMock(spec=Chat)
        mock_chat.id = test_user_id
        
        mock_message = MagicMock(spec=Message)
        mock_message.chat = mock_chat
        
        mock_query = AsyncMock(spec=CallbackQuery)
        mock_query.data = "categories"
        mock_query.message = mock_message
        
        mock_update = MagicMock(spec=Update)
        mock_update.callback_query = mock_query
        mock_update.effective_user = mock_user
        
        mock_context = MagicMock()
        
        # Track if query.answer was called with block message
        answer_called = False
        answer_text = None
        answer_alert = False
        
        async def mock_answer(text=None, show_alert=False):
            nonlocal answer_called, answer_text, answer_alert
            answer_called = True
            answer_text = text
            answer_alert = show_alert
        
        mock_query.answer = mock_answer
        
        # Patch the global db instance in main module
        with patch('main.db', db):
            # Import and test the callback handler
            from main import callback_query_handler
            
            # Call the callback handler
            await callback_query_handler(mock_update, mock_context)
        
        # Verify the blocked user received the block message
        assert answer_called, "query.answer should have been called"
        assert answer_text is not None, "Block message should have been sent"
        assert "blocked" in answer_text.lower(), f"Message should mention blocking: {answer_text}"
        assert answer_alert == True, "Block message should be shown as alert"
        print(f"✓ Blocked user received block message: {answer_text}")
        
        # Unblock the user and test again
        await db.unblock_user(test_user_id)
        is_blocked = await db.is_user_blocked(test_user_id)
        assert is_blocked == False, "User should be unblocked"
        print("✓ User successfully unblocked")
        
    except AssertionError as e:
        print(f"❌ Test failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if os.path.exists("test_block_callback.db"):
            os.remove("test_block_callback.db")
    
    return True


async def test_unsubscribe_confirmation_prompt():
    """Test that unsubscribing shows a confirmation prompt."""
    print("Testing unsubscribe confirmation prompt...")
    
    # Use test database
    db = Database("test_unsub_confirm.db")
    
    try:
        await db.init_db()
        
        # Add test user (subscribed by default)
        test_user_id = 54321
        await db.track_user(
            user_id=test_user_id,
            username="testuser2",
            first_name="Test2",
            last_name="User2"
        )
        
        # Verify user is subscribed
        is_subscribed = await db.is_user_subscribed(test_user_id)
        assert is_subscribed == True, "User should be subscribed by default"
        print("✓ User is subscribed by default")
        
        # Mock callback query for unsubscribe button
        mock_user = MagicMock(spec=User)
        mock_user.id = test_user_id
        mock_user.username = "testuser2"
        
        mock_chat = MagicMock(spec=Chat)
        mock_chat.id = test_user_id
        
        mock_message = MagicMock(spec=Message)
        mock_message.chat = mock_chat
        
        mock_query = AsyncMock(spec=CallbackQuery)
        mock_query.data = "unsubscribe_notifications"
        mock_query.message = mock_message
        
        mock_update = MagicMock(spec=Update)
        mock_update.callback_query = mock_query
        mock_update.effective_user = mock_user
        
        mock_context = MagicMock()
        
        # Track if edit_message_reply_markup was called with confirmation buttons
        edit_called = False
        confirmation_keyboard = None
        answer_text = None
        
        async def mock_edit_markup(reply_markup=None):
            nonlocal edit_called, confirmation_keyboard
            edit_called = True
            confirmation_keyboard = reply_markup
        
        async def mock_answer(text=None, show_alert=False):
            nonlocal answer_text
            answer_text = text
        
        mock_query.edit_message_reply_markup = mock_edit_markup
        mock_query.answer = mock_answer
        
        # Patch the global db instance
        with patch('main.db', db):
            from main import callback_query_handler
            
            # Call the callback handler
            await callback_query_handler(mock_update, mock_context)
        
        # Verify confirmation prompt was shown
        assert edit_called, "edit_message_reply_markup should have been called"
        assert confirmation_keyboard is not None, "Confirmation keyboard should be present"
        assert answer_text is not None, "Answer message should be present"
        assert "sure" in answer_text.lower(), f"Message should ask for confirmation: {answer_text}"
        
        # Check that confirmation buttons are present
        buttons = confirmation_keyboard.inline_keyboard
        assert len(buttons) >= 2, "Should have at least 2 button rows (confirm and cancel)"
        
        # Find confirm and cancel buttons
        confirm_button = None
        cancel_button = None
        for row in buttons:
            for button in row:
                if "confirm_unsubscribe" in button.callback_data:
                    confirm_button = button
                elif "cancel_unsubscribe" in button.callback_data:
                    cancel_button = button
        
        assert confirm_button is not None, "Should have confirm unsubscribe button"
        assert cancel_button is not None, "Should have cancel button"
        print(f"✓ Confirmation prompt shown with buttons: {[btn.text for row in buttons for btn in row]}")
        
        # Verify user is still subscribed (not unsubscribed yet)
        is_subscribed = await db.is_user_subscribed(test_user_id)
        assert is_subscribed == True, "User should still be subscribed until confirmed"
        print("✓ User remains subscribed until confirmation")
        
        # Now test the confirm action
        mock_query.data = "confirm_unsubscribe"
        answer_text = None
        
        with patch('main.db', db):
            await callback_query_handler(mock_update, mock_context)
        
        # Verify user is now unsubscribed
        is_subscribed = await db.is_user_subscribed(test_user_id)
        assert is_subscribed == False, "User should be unsubscribed after confirmation"
        assert answer_text is not None, "Confirmation message should be shown"
        assert "unsubscribed" in answer_text.lower(), f"Message should confirm unsubscribe: {answer_text}"
        print(f"✓ User successfully unsubscribed after confirmation: {answer_text}")
        
        # Test cancel action
        await db.set_user_notifications(test_user_id, True)  # Re-subscribe
        mock_query.data = "unsubscribe_notifications"
        
        with patch('main.db', db):
            await callback_query_handler(mock_update, mock_context)
        
        mock_query.data = "cancel_unsubscribe"
        answer_text = None
        
        with patch('main.db', db):
            await callback_query_handler(mock_update, mock_context)
        
        # Verify user is still subscribed
        is_subscribed = await db.is_user_subscribed(test_user_id)
        assert is_subscribed == True, "User should remain subscribed after cancel"
        assert answer_text is not None and "cancel" in answer_text.lower(), "Answer text should indicate cancellation was successful"
        print(f"✓ User remains subscribed after cancelling: {answer_text}")
        
    except AssertionError as e:
        print(f"❌ Test failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if os.path.exists("test_unsub_confirm.db"):
            os.remove("test_unsub_confirm.db")
    
    return True


async def test_notification_toggle():
    """Test the notification toggle functionality."""
    print("Testing notification toggle functionality...")
    
    # Use test database
    db = Database("test_notif_toggle.db")
    
    try:
        await db.init_db()
        
        # Add test user
        test_user_id = 99999
        await db.track_user(
            user_id=test_user_id,
            username="toggleuser",
            first_name="Toggle",
            last_name="User"
        )
        
        # Verify default subscribed state
        is_subscribed = await db.is_user_subscribed(test_user_id)
        assert is_subscribed == True, "User should be subscribed by default"
        print("✓ User is subscribed by default")
        
        # Test toggling from subscribed to showing unsubscribe confirmation
        mock_user = MagicMock(spec=User)
        mock_user.id = test_user_id
        
        mock_chat = MagicMock(spec=Chat)
        mock_chat.id = test_user_id
        
        mock_message = MagicMock(spec=Message)
        mock_message.chat = mock_chat
        
        mock_query = AsyncMock(spec=CallbackQuery)
        mock_query.data = "toggle_notifications"
        mock_query.message = mock_message
        
        mock_update = MagicMock(spec=Update)
        mock_update.callback_query = mock_query
        mock_update.effective_user = mock_user
        
        mock_context = MagicMock()
        
        edit_called = False
        answer_text = None
        
        async def mock_edit_markup(reply_markup=None):
            nonlocal edit_called
            edit_called = True
        
        async def mock_answer(text=None, show_alert=False):
            nonlocal answer_text
            answer_text = text
        
        mock_query.edit_message_reply_markup = mock_edit_markup
        mock_query.answer = mock_answer
        
        with patch('main.db', db):
            from main import callback_query_handler
            
            # When subscribed, should show confirmation
            await callback_query_handler(mock_update, mock_context)
        
        assert edit_called, "Should show confirmation dialog"
        assert "sure" in answer_text.lower(), "Should ask for confirmation"
        print("✓ Toggle from subscribed shows confirmation prompt")
        
        # Unsubscribe the user
        await db.set_user_notifications(test_user_id, False)
        is_subscribed = await db.is_user_subscribed(test_user_id)
        assert is_subscribed == False, "User should be unsubscribed"
        
        # When unsubscribed, toggling should re-subscribe immediately
        edit_called = False
        answer_text = None
        edit_markup_value = None
        
        async def mock_edit_markup2(reply_markup=None):
            nonlocal edit_called, edit_markup_value
            edit_called = True
            edit_markup_value = reply_markup
        
        mock_query.edit_message_reply_markup = mock_edit_markup2
        
        with patch('main.db', db):
            await callback_query_handler(mock_update, mock_context)
        
        # Should edit markup to remove buttons (reply_markup=None) but not show confirmation dialog
        assert edit_called, "Should remove buttons after re-subscribing"
        assert edit_markup_value is None, "Should remove buttons (set to None)"
        assert answer_text is not None and "subscribed" in answer_text.lower(), "Should confirm subscription"
        
        is_subscribed = await db.is_user_subscribed(test_user_id)
        assert is_subscribed == True, "User should be re-subscribed"
        print("✓ Toggle from unsubscribed immediately re-subscribes")
        
    except AssertionError as e:
        print(f"❌ Test failed: {e}")
        return False
    except Exception as e:
        print(f"❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if os.path.exists("test_notif_toggle.db"):
            os.remove("test_notif_toggle.db")
    
    return True


async def main():
    """Run all tests."""
    print("=" * 60)
    print("Running Block and Unsubscribe Fixes Tests")
    print("=" * 60)
    print()
    
    tests = [
        ("Blocked User Callback Prevention", test_blocked_user_callback_prevention),
        ("Unsubscribe Confirmation Prompt", test_unsubscribe_confirmation_prompt),
        ("Notification Toggle", test_notification_toggle),
    ]
    
    results = []
    for test_name, test_func in tests:
        print(f"\n{'=' * 60}")
        print(f"Test: {test_name}")
        print('=' * 60)
        result = await test_func()
        results.append((test_name, result))
        print()
    
    # Print summary
    print("=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    for test_name, result in results:
        status = "✅ PASSED" if result else "❌ FAILED"
        print(f"{status}: {test_name}")
    print()
    
    all_passed = all(result for _, result in results)
    if all_passed:
        print("🎉 All tests passed!")
    else:
        print("⚠️  Some tests failed!")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

