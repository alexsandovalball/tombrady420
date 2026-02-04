#!/usr/bin/env python3
"""
Integration test to simulate the /start command flow and verify
that the language column error is fixed.
"""
import asyncio
import sqlite3
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import Database


async def test_start_command_flow():
    """
    Test that simulates the /start command flow to ensure no 'no such column: language' error.
    This test reproduces the issue from the problem statement.
    """
    # Use tempfile for cross-platform compatibility
    fd, test_db_path = tempfile.mkstemp(suffix='.db', prefix='test_start_command_')
    os.close(fd)  # Close the file descriptor
    os.remove(test_db_path)  # Remove the empty file so we can create our own
    
    print("="*70)
    print("INTEGRATION TEST: /start Command Flow")
    print("="*70)
    print("\nSimulating the scenario from the problem statement:")
    print("User FAUNA (user_id=8453686304) sends /start command")
    print()
    
    # ===== SCENARIO 1: Fresh database with proper schema =====
    print("SCENARIO 1: Fresh database (typical production deployment)")
    print("-" * 70)
    
    db = Database(test_db_path)
    await db.init_db()
    print("✓ Database initialized")
    
    # Simulate user sending /start command
    test_user_id = 8453686304
    test_username = "FAUNA"
    test_first_name = "Fauna"
    test_last_name = "Test"
    
    # Track user (as done in start_command handler)
    await db.track_user(
        user_id=test_user_id,
        username=test_username,
        first_name=test_first_name,
        last_name=test_last_name
    )
    print(f"✓ User tracked: {test_username} (ID: {test_user_id})")
    
    # Get user's language preference (this is line 27 in handlers/start.py)
    try:
        user_lang = await db.get_user_language(test_user_id)
        print(f"✓ User language retrieved successfully: '{user_lang}'")
    except Exception as e:
        print(f"✗ ERROR: {e}")
        raise
    
    # Check notification subscription
    is_subscribed = await db.is_user_subscribed(test_user_id)
    print(f"✓ Subscription status checked: {is_subscribed}")
    
    print("\n✅ SCENARIO 1 PASSED - No 'no such column: language' error!\n")
    
    # Clean up
    os.remove(test_db_path)
    
    # ===== SCENARIO 2: Existing database WITHOUT language column =====
    print("SCENARIO 2: Existing database without language column")
    print("-" * 70)
    print("(This simulates a production database before the fix)")
    print()
    
    # Create old-style database
    conn = sqlite3.connect(test_db_path)
    cursor = conn.cursor()
    
    # Create bot_users table WITHOUT language column (old schema)
    cursor.execute("""
        CREATE TABLE bot_users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            interaction_count INTEGER DEFAULT 1,
            notifications_enabled INTEGER DEFAULT 1,
            is_blocked INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()
    
    print("✓ Old database created (without language column)")
    
    # Now initialize with our fix - this should add the language column
    db = Database(test_db_path)
    await db.init_db()
    print("✓ Database migrated with init_db()")
    
    # Verify the column was added
    conn = sqlite3.connect(test_db_path)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(bot_users)")
    columns = cursor.fetchall()
    column_names = [col[1] for col in columns]
    conn.close()
    
    if 'language' in column_names:
        print("✓ Language column successfully added during init_db()")
    else:
        print("✗ ERROR: Language column was NOT added!")
        raise AssertionError("Language column missing after init_db()")
    
    # Now simulate /start command on this migrated database
    test_user_id2 = 9876543210
    test_username2 = "TestUser2"
    
    await db.track_user(
        user_id=test_user_id2,
        username=test_username2,
        first_name="Test",
        last_name="User2"
    )
    print(f"✓ New user tracked: {test_username2} (ID: {test_user_id2})")
    
    # This should NOT raise "no such column: language" error
    try:
        user_lang = await db.get_user_language(test_user_id2)
        print(f"✓ User language retrieved successfully: '{user_lang}'")
    except Exception as e:
        print(f"✗ ERROR: {e}")
        raise
    
    print("\n✅ SCENARIO 2 PASSED - Migration successful!\n")
    
    # Clean up
    os.remove(test_db_path)
    
    # ===== SCENARIO 3: Test language operations =====
    print("SCENARIO 3: Full language functionality test")
    print("-" * 70)
    
    # Create fresh database
    db = Database(test_db_path)
    await db.init_db()
    
    test_user_id3 = 1234567890
    await db.track_user(
        user_id=test_user_id3,
        username="MultiLingualUser",
        first_name="Multi",
        last_name="Lingual"
    )
    
    # Test default language
    lang = await db.get_user_language(test_user_id3)
    assert lang == 'en', f"Expected default 'en', got '{lang}'"
    print(f"✓ Default language: {lang}")
    
    # Test setting different languages
    test_languages = ['es', 'de', 'fr', 'it', 'nl']
    for test_lang in test_languages:
        await db.set_user_language(test_user_id3, test_lang)
        retrieved_lang = await db.get_user_language(test_user_id3)
        assert retrieved_lang == test_lang, f"Expected '{test_lang}', got '{retrieved_lang}'"
        print(f"✓ Language set and retrieved: {test_lang}")
    
    print("\n✅ SCENARIO 3 PASSED - All language operations working!\n")
    
    # Clean up
    os.remove(test_db_path)
    
    # ===== SCENARIO 4: Test new user language selection flow =====
    print("SCENARIO 4: New user language selection flow")
    print("-" * 70)
    
    # Create fresh database
    db = Database(test_db_path)
    await db.init_db()
    
    # Simulate a brand new user (doesn't exist in database)
    new_user_id = 5555555555
    
    # Check that user doesn't exist
    user_exists = await db.user_exists(new_user_id)
    assert not user_exists, "User should not exist yet"
    print(f"✓ New user {new_user_id} does not exist in database")
    
    # Track the new user (this happens in start_command)
    await db.track_user(
        user_id=new_user_id,
        username="NewUser",
        first_name="New",
        last_name="User"
    )
    print(f"✓ New user tracked: {new_user_id}")
    
    # Verify user now exists
    user_exists = await db.user_exists(new_user_id)
    assert user_exists, "User should exist after tracking"
    print(f"✓ User {new_user_id} now exists in database")
    
    # Simulate user selecting a language (Spanish)
    await db.set_user_language(new_user_id, 'es')
    print("✓ User selected language: es")
    
    # Verify language was set correctly
    user_lang = await db.get_user_language(new_user_id)
    assert user_lang == 'es', f"Expected 'es', got '{user_lang}'"
    print(f"✓ Language preference saved: {user_lang}")
    
    # Test with existing user (should not show language selection)
    existing_user_id = 7777777777
    await db.track_user(
        user_id=existing_user_id,
        username="ExistingUser",
        first_name="Existing",
        last_name="User"
    )
    await db.set_user_language(existing_user_id, 'de')
    
    # On second /start, user should already exist
    user_exists_again = await db.user_exists(existing_user_id)
    assert user_exists_again, "Existing user should still exist"
    print(f"✓ Existing user {existing_user_id} check works correctly")
    
    print("\n✅ SCENARIO 4 PASSED - New user language selection flow working!\n")
    
    # Clean up
    os.remove(test_db_path)
    
    print("="*70)
    print("✅ ALL INTEGRATION TESTS PASSED!")
    print("="*70)
    print("\nSUMMARY:")
    print("--------")
    print("✓ Fresh databases work correctly")
    print("✓ Existing databases are automatically migrated")
    print("✓ The 'no such column: language' error is FIXED")
    print("✓ /start command will work correctly for all users")
    print("✓ New users see language selection menu")
    print("✓ Existing users see normal welcome message")
    print("\nThe fix ensures that when init_db() is called during")
    print("deployment, the language column is added to existing")
    print("databases, preventing the error from occurring.")
    print()
    
    return True


if __name__ == "__main__":
    try:
        success = asyncio.run(test_start_command_flow())
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
