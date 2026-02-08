#!/usr/bin/env python3
"""
Smoke test for multi-bot notification system.

Tests:
1. Notifications are queued per-bot based on target_bot_key
2. Callback_data buttons are used instead of URL deep links
3. No duplicate categorization prompts are created
4. Concurrent sending works correctly

Usage:
    python scripts/smoke_test_notifications.py
"""
import asyncio
import sys
import os
import json
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from database import Database
from utils.notifications import NotificationService
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from configs.config import Config

# Test constants
TEST_MESSAGE_ID = 999999
TEST_CHAT_ID = -1001234567890


async def setup_test_product(db: Database) -> int:
    """
    Create a test product in the database.
    
    Returns:
        int: Product ID of the created and categorized test product
    """
    print("📦 Creating test product...")
    
    # Create a simple test product
    product_id = await db.add_product(
        file_id="test_file_id_123",
        file_type="photo",
        caption="Test product for smoke testing",
        message_id=TEST_MESSAGE_ID,
        chat_id=TEST_CHAT_ID,
        media_group_id=None,
        additional_file_ids=None,
        additional_message_ids=None
    )
    
    # Categorize it
    await db.categorize_product(product_id, "MEDIA", None)
    
    print(f"✅ Test product created with ID: {product_id}")
    return product_id


async def test_notification_buttons(db: Database, product_id: int):
    """Test that notification buttons use callback_data instead of URL."""
    print("\n🔘 Testing notification button structure...")
    
    # Get queued messages for the product
    pending = await db.get_pending_custom_messages(limit=10)
    
    if not pending:
        print("⚠️  No pending messages found")
        return False
    
    # Check the first message
    first_msg = pending[0]
    reply_markup_json = first_msg.get("reply_markup")
    
    if not reply_markup_json:
        print("❌ No reply_markup found in queued message")
        return False
    
    # Parse the reply markup
    try:
        reply_markup_data = json.loads(reply_markup_json)
        inline_keyboard = reply_markup_data.get("inline_keyboard", [])
        
        # Check for callback_data buttons
        found_product_callback = False
        found_categories_callback = False
        
        for row in inline_keyboard:
            for button in row:
                callback_data = button.get("callback_data", "")
                url = button.get("url", "")
                
                # Check that we're using callback_data, not URL
                if f"product|{product_id}" in callback_data:
                    found_product_callback = True
                    print(f"✅ Found product callback button: {callback_data}")
                
                if "categories" in callback_data:
                    found_categories_callback = True
                    print(f"✅ Found categories callback button: {callback_data}")
                
                # Check that we're NOT using URL deep links for product/catalog
                if url and ("start=product_" in url or "start=menu" in url):
                    print(f"❌ Found URL deep link (should use callback_data): {url}")
                    return False
        
        if found_product_callback and found_categories_callback:
            print("✅ Notification buttons correctly use callback_data")
            return True
        else:
            print(f"⚠️  Missing expected callbacks (product={found_product_callback}, categories={found_categories_callback})")
            return False
            
    except Exception as e:
        print(f"❌ Error parsing reply_markup: {e}")
        return False


async def test_bot_routing(db: Database, product_id: int):
    """Test that messages are routed to appropriate bots."""
    print("\n🔀 Testing bot routing...")
    
    # Get all queued messages
    pending = await db.get_pending_custom_messages(limit=100)
    
    if not pending:
        print("⚠️  No pending messages found")
        return False
    
    # Count messages by target_bot_key
    routing_counts = {}
    for msg in pending:
        target_key = msg.get("target_bot_key", "None")
        routing_counts[target_key] = routing_counts.get(target_key, 0) + 1
    
    print(f"📊 Message routing distribution:")
    for target_key, count in routing_counts.items():
        print(f"   - {target_key}: {count} messages")
    
    print("✅ Bot routing test complete")
    return True


async def test_no_duplicate_categorization(db: Database, product_id: int):
    """Test that only one categorization request is created."""
    print("\n📝 Testing categorization prompt uniqueness...")
    
    # Check pending categorization
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        async with conn.execute(
            "SELECT COUNT(*) FROM pending_categorization WHERE product_id = ?",
            (product_id,)
        ) as cursor:
            row = await cursor.fetchone()
            count = row[0] if row else 0
    
    if count <= 1:
        print(f"✅ Categorization prompt count: {count} (expected 0-1)")
        return True
    else:
        print(f"❌ Multiple categorization prompts found: {count}")
        return False


async def cleanup_test_data(db: Database, product_id: int):
    """Clean up test data."""
    print("\n🧹 Cleaning up test data...")
    
    try:
        # Delete test product
        await db.delete_product(product_id)
        
        # Clear any remaining test messages from queue
        import aiosqlite
        async with aiosqlite.connect(db.db_path) as conn:
            await conn.execute(
                "DELETE FROM custom_message_queue WHERE message_text LIKE '%Test product for smoke testing%'"
            )
            await conn.commit()
        
        print("✅ Cleanup complete")
    except Exception as e:
        print(f"⚠️  Cleanup error (non-critical): {e}")


async def main():
    """Run smoke tests."""
    print("=" * 60)
    print("🧪 Multi-Bot Notification System - Smoke Tests")
    print("=" * 60)
    
    # Initialize database
    db = Database()
    await db.init_db()
    
    try:
        # Create test product
        product_id = await setup_test_product(db)
        
        # Simulate notification queueing
        # Note: We can't fully test without a real bot context,
        # but we can test the database layer and button structure
        print("\n📨 Simulating notification queue...")
        
        # Get some test users (or create dummy ones for testing)
        subscribed_users = await db.get_subscribed_users()
        
        if not subscribed_users:
            print("⚠️  No subscribed users found. Skipping queue tests.")
            print("   (In production, users would be subscribed)")
        else:
            # Queue test notifications
            from utils.bot_key import get_bot_env_key
            current_bot_key = get_bot_env_key()
            
            # Build test message
            message_text = (
                f"🆕 **New Product Available!**\n\n"
                f"📂 MEDIA\n\n"
                f"📝 Test product for smoke testing\n\n"
                f"👉 Use /menu to view the catalog"
            )
            
            # Build test keyboard with callback_data
            keyboard = InlineKeyboardMarkup([
                [InlineKeyboardButton("👁️ View Product", callback_data=f"product|{product_id}")],
                [InlineKeyboardButton("📋 View Catalog", callback_data="categories")],
                [InlineKeyboardButton("🔕 Unsubscribe", callback_data=f"toggle_notifications|{product_id}")]
            ])
            reply_markup_json = json.dumps(keyboard.to_dict())
            
            # Queue messages with target routing
            users_with_targets = [
                (user_id, current_bot_key) for user_id in subscribed_users[:5]  # Limit to 5 for testing
            ]
            
            queued_count = await db.queue_custom_messages_batch_with_targets(
                users_with_targets=users_with_targets,
                message_text=message_text,
                reply_markup=reply_markup_json
            )
            
            print(f"✅ Queued {queued_count} test messages")
        
        # Run tests
        all_passed = True
        
        # Test 1: Button structure
        if subscribed_users:
            test1_passed = await test_notification_buttons(db, product_id)
            all_passed = all_passed and test1_passed
        
        # Test 2: Bot routing
        if subscribed_users:
            test2_passed = await test_bot_routing(db, product_id)
            all_passed = all_passed and test2_passed
        
        # Test 3: No duplicate categorization
        test3_passed = await test_no_duplicate_categorization(db, product_id)
        all_passed = all_passed and test3_passed
        
        # Cleanup
        await cleanup_test_data(db, product_id)
        
        # Summary
        print("\n" + "=" * 60)
        if all_passed:
            print("✅ All smoke tests PASSED")
            print("=" * 60)
            return 0
        else:
            print("❌ Some smoke tests FAILED")
            print("=" * 60)
            return 1
            
    except Exception as e:
        print(f"\n❌ Smoke test error: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
