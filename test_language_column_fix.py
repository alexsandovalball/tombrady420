#!/usr/bin/env python3
"""
Test to verify that the language column is properly added to existing databases
that don't have it.
"""
import asyncio
import sqlite3
import os
import sys
import tempfile

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import Database


async def test_language_column_migration():
    """Test that init_db adds the language column to existing databases."""
    # Use tempfile for cross-platform compatibility
    fd, test_db_path = tempfile.mkstemp(suffix='.db', prefix='test_language_fix_')
    os.close(fd)  # Close the file descriptor
    os.remove(test_db_path)  # Remove the empty file so we can create our own
    
    print("Step 1: Creating a database without the language column...")
    # Create an old-style database without the language column
    conn = sqlite3.connect(test_db_path)
    cursor = conn.cursor()
    
    # Create bot_users table without language column (simulating old schema)
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
    
    # Insert a test user
    cursor.execute("""
        INSERT INTO bot_users (user_id, username, first_name, last_name)
        VALUES (123456, 'testuser', 'Test', 'User')
    """)
    
    conn.commit()
    
    # Verify language column doesn't exist
    cursor.execute("PRAGMA table_info(bot_users)")
    columns = cursor.fetchall()
    column_names = [col[1] for col in columns]
    
    print(f"Columns before migration: {column_names}")
    assert 'language' not in column_names, "Language column should not exist yet"
    print("✓ Confirmed: language column does not exist in old database")
    
    conn.close()
    
    print("\nStep 2: Running init_db to add the language column...")
    # Now run init_db which should add the language column
    db = Database(test_db_path)
    await db.init_db()
    
    print("\nStep 3: Verifying the language column was added...")
    # Verify the column was added
    conn = sqlite3.connect(test_db_path)
    cursor = conn.cursor()
    
    cursor.execute("PRAGMA table_info(bot_users)")
    columns = cursor.fetchall()
    column_names = [col[1] for col in columns]
    
    print(f"Columns after migration: {column_names}")
    assert 'language' in column_names, "Language column should exist after init_db"
    print("✓ Confirmed: language column was successfully added")
    
    print("\nStep 4: Testing get_user_language functionality...")
    # Test that we can query the language for existing user
    cursor.execute("SELECT language FROM bot_users WHERE user_id = 123456")
    result = cursor.fetchone()
    assert result is not None, "User should still exist"
    assert result[0] == 'en', f"Default language should be 'en', got {result[0]}"
    print(f"✓ User language: {result[0]} (default)")
    
    conn.close()
    
    print("\nStep 5: Testing Database.get_user_language method...")
    # Test using the actual Database methods
    user_lang = await db.get_user_language(123456)
    assert user_lang == 'en', f"Expected 'en', got {user_lang}"
    print(f"✓ Database.get_user_language returned: {user_lang}")
    
    print("\nStep 6: Testing Database.set_user_language method...")
    # Test setting a different language
    await db.set_user_language(123456, 'es')
    user_lang = await db.get_user_language(123456)
    assert user_lang == 'es', f"Expected 'es', got {user_lang}"
    print(f"✓ Database.set_user_language works correctly: {user_lang}")
    
    # Clean up
    os.remove(test_db_path)
    
    print("\n" + "="*60)
    print("✅ ALL TESTS PASSED!")
    print("="*60)
    print("\nThe language column migration fix is working correctly.")
    print("Existing databases will now have the language column added")
    print("automatically when init_db is called.")
    
    return True


if __name__ == "__main__":
    try:
        success = asyncio.run(test_language_column_migration())
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
