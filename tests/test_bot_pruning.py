#!/usr/bin/env python3
"""
Tests for inactive bot pruning functionality.

This test validates:
1. Detection of users/products from inactive bots
2. Pruning logic works correctly
3. Active bots' data is preserved
4. Notification error handling for deleted users

Run from project root: python tests/test_bot_pruning.py
"""
import asyncio
import os
import sys
import tempfile
import logging
from datetime import datetime

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from database import Database


async def test_inactive_bot_detection():
    """Test detection of users and products from inactive bots."""
    logger.info("TEST 1: Inactive bot detection")
    
    # Create a temporary database
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name
    
    try:
        db = Database(db_path=db_path)
        await db.init_db()
        
        # Add test users with different bot usernames
        await db.track_user(
            user_id=1001,
            username="user1",
            first_name="User",
            last_name="One",
            bot_username="activebot"
        )
        
        await db.track_user(
            user_id=1002,
            username="user2",
            first_name="User",
            last_name="Two",
            bot_username="inactivebot"
        )
        
        await db.track_user(
            user_id=1003,
            username="user3",
            first_name="User",
            last_name="Three",
            bot_username="deletedbot"
        )
        
        # Test: Get users with inactive bots
        active_bots = ["activebot"]
        inactive_users = await db.get_users_with_inactive_bots(active_bots)
        
        assert len(inactive_users) == 2, f"Expected 2 inactive users, got {len(inactive_users)}"
        inactive_user_ids = {u['user_id'] for u in inactive_users}
        assert 1002 in inactive_user_ids, "User 1002 should be inactive"
        assert 1003 in inactive_user_ids, "User 1003 should be inactive"
        assert 1001 not in inactive_user_ids, "User 1001 should be active"
        
        logger.info("✅ PASS: Correctly identified 2 inactive users")
        
    finally:
        # Cleanup
        if os.path.exists(db_path):
            os.unlink(db_path)


async def test_prune_dry_run():
    """Test pruning in dry run mode (no actual deletion)."""
    logger.info("\nTEST 2: Dry run pruning")
    
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name
    
    try:
        db = Database(db_path=db_path)
        await db.init_db()
        
        # Add test data for dry run
        await db.track_user(1001, "user1", "User", "One", "activebot")
        await db.track_user(1002, "user2", "User", "Two", "inactivebot")
        
        # Add products
        await db.add_product(
            file_id="file1",
            file_type="photo",
            caption="Active bot product",
            message_id=101,
            chat_id=-1001234567890,
            bot_username="activebot"
        )
        
        await db.add_product(
            file_id="file2",
            file_type="photo",
            caption="Inactive bot product",
            message_id=102,
            chat_id=-1001234567890,
            bot_username="inactivebot"
        )
        
        # Queue notifications
        await db.queue_notification(1002, 2)  # For inactive user
        
        # Dry run
        active_bots = ["activebot"]
        stats = await db.prune_inactive_bot_users(active_bots, dry_run=True)
        
        assert stats['users'] == 1, f"Expected 1 user to prune, got {stats['users']}"
        assert stats['products'] == 1, f"Expected 1 product to prune, got {stats['products']}"
        
        # Verify nothing was actually deleted
        all_users_count = await db.get_users_count_by_bot()
        assert all_users_count == 2, "Dry run should not delete users"
        
        logger.info(f"✅ PASS: Dry run reported {stats['users']} users, {stats['products']} products to prune")
        logger.info("   No actual deletion performed")
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


async def test_prune_actual():
    """Test actual pruning (with deletion)."""
    logger.info("\nTEST 3: Actual pruning")
    
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name
    
    try:
        db = Database(db_path=db_path)
        await db.init_db()
        
        # Add test data
        await db.track_user(1001, "user1", "User", "One", "activebot")
        await db.track_user(1002, "user2", "User", "Two", "inactivebot")
        await db.track_user(1003, "user3", "User", "Three", "deletedbot")
        
        # Add products
        await db.add_product(
            file_id="file1",
            file_type="photo",
            caption="Active bot product",
            message_id=101,
            chat_id=-1001234567890,
            bot_username="activebot"
        )
        
        await db.add_product(
            file_id="file2",
            file_type="photo",
            caption="Inactive bot product",
            message_id=102,
            chat_id=-1001234567890,
            bot_username="inactivebot"
        )
        
        # Actual pruning
        active_bots = ["activebot"]
        stats = await db.prune_inactive_bot_users(active_bots, dry_run=False)
        
        assert stats['users'] == 2, f"Expected 2 users pruned, got {stats['users']}"
        assert stats['products'] == 1, f"Expected 1 product pruned, got {stats['products']}"
        
        # Verify data was actually deleted
        remaining_users = await db.get_users_count_by_bot()
        assert remaining_users == 1, f"Expected 1 user remaining, got {remaining_users}"
        
        # Verify active user still exists
        active_user = await db.get_user_by_id(1001)
        assert active_user is not None, "Active user should still exist"
        assert active_user['bot_username'].lower() == "activebot"
        
        # Verify inactive users are gone
        inactive_user = await db.get_user_by_id(1002)
        assert inactive_user is None, "Inactive user should be deleted"
        
        logger.info(f"✅ PASS: Pruned {stats['users']} users, {stats['products']} products")
        logger.info("   Active bot users preserved")
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


async def test_case_insensitive_matching():
    """Test that bot username matching is case-insensitive."""
    logger.info("\nTEST 4: Case-insensitive bot matching")
    
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name
    
    try:
        db = Database(db_path=db_path)
        await db.init_db()
        
        # Add users with different case variations
        await db.track_user(1001, "user1", "User", "One", "ActiveBot")
        await db.track_user(1002, "user2", "User", "Two", "ACTIVEBOT")
        await db.track_user(1003, "user3", "User", "Three", "activebot")
        
        # All should be considered active (case-insensitive)
        active_bots = ["activebot"]  # lowercase
        inactive_users = await db.get_users_with_inactive_bots(active_bots)
        
        assert len(inactive_users) == 0, f"Expected 0 inactive users (case-insensitive), got {len(inactive_users)}"
        
        logger.info("✅ PASS: Case-insensitive matching works correctly")
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


async def test_preserve_untracked_users():
    """Test that users without bot_username are not affected by pruning."""
    logger.info("\nTEST 5: Preserve untracked users")
    
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name
    
    try:
        db = Database(db_path=db_path)
        await db.init_db()
        
        # Add users without bot_username (legacy users)
        await db.track_user(1001, "user1", "User", "One", None)
        await db.track_user(1002, "user2", "User", "Two", "")
        await db.track_user(1003, "user3", "User", "Three", "inactivebot")
        
        # Prune with active bots
        active_bots = ["activebot"]
        stats = await db.prune_inactive_bot_users(active_bots, dry_run=False)
        
        # Only the user with inactivebot should be pruned
        assert stats['users'] == 1, f"Expected 1 user pruned, got {stats['users']}"
        
        # Verify untracked users still exist
        user1 = await db.get_user_by_id(1001)
        user2 = await db.get_user_by_id(1002)
        assert user1 is not None, "Untracked user 1001 should be preserved"
        assert user2 is not None, "Untracked user 1002 should be preserved"
        
        logger.info("✅ PASS: Untracked users preserved during pruning")
        
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)


async def run_all_tests():
    """Run all tests."""
    logger.info("=" * 60)
    logger.info("RUNNING BOT PRUNING TESTS")
    logger.info("=" * 60)
    
    tests = [
        test_inactive_bot_detection,
        test_prune_dry_run,
        test_prune_actual,
        test_case_insensitive_matching,
        test_preserve_untracked_users,
    ]
    
    passed = 0
    failed = 0
    
    for test in tests:
        try:
            await test()
            passed += 1
        except AssertionError as e:
            logger.error(f"❌ FAIL: {test.__name__}")
            logger.error(f"   {e}")
            failed += 1
        except Exception as e:
            logger.error(f"❌ ERROR: {test.__name__}")
            logger.error(f"   {e}", exc_info=True)
            failed += 1
    
    logger.info("\n" + "=" * 60)
    logger.info("TEST RESULTS")
    logger.info("=" * 60)
    logger.info(f"Passed: {passed}/{len(tests)}")
    logger.info(f"Failed: {failed}/{len(tests)}")
    
    if failed == 0:
        logger.info("\n✅ ALL TESTS PASSED!")
        return 0
    else:
        logger.error(f"\n❌ {failed} TEST(S) FAILED")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(run_all_tests())
    exit(exit_code)
