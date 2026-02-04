#!/usr/bin/env python3
"""
Test script to verify the language column migration fix.
Tests all database operations related to the language column.
"""
import asyncio
import sys
import os

# Mock environment variables for testing
os.environ['BOT_TOKEN'] = 'test_token'
os.environ['CHANNEL_USERNAME'] = '@test_channel'
os.environ['ADMIN_IDS'] = '123'

sys.path.insert(0, '.')
from database import Database


async def test_language_operations():
    """Test all language-related database operations."""
    db = Database('catalog.db')
    test_user_id = 987654321  # Use a unique ID that likely doesn't exist
    
    # Ensure test user doesn't exist
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute("DELETE FROM bot_users WHERE user_id = ?", (test_user_id,))
        await conn.commit()
    
    print("=" * 60)
    print("Testing Language Column Operations")
    print("=" * 60)
    
    # Test 1: Get language for non-existent user (should return default 'en')
    print("\n1. Testing get_user_language() for non-existent user...")
    lang = await db.get_user_language(test_user_id)
    assert lang == 'en', f"Expected 'en', got '{lang}'"
    print(f"   ✓ Default language correctly returned: {lang}")
    
    # Test 2: Set language for new user
    print("\n2. Testing set_user_language() for new user...")
    await db.set_user_language(test_user_id, 'es')
    lang = await db.get_user_language(test_user_id)
    assert lang == 'es', f"Expected 'es', got '{lang}'"
    print(f"   ✓ Language set successfully: {lang}")
    
    # Test 3: Update language for existing user
    print("\n3. Testing set_user_language() for existing user...")
    await db.set_user_language(test_user_id, 'fr')
    lang = await db.get_user_language(test_user_id)
    assert lang == 'fr', f"Expected 'fr', got '{lang}'"
    print(f"   ✓ Language updated successfully: {lang}")
    
    # Test 4: Test all supported languages
    print("\n4. Testing all supported languages...")
    test_languages = ['en', 'de', 'nl', 'it', 'es', 'fr']
    for test_lang in test_languages:
        await db.set_user_language(test_user_id, test_lang)
        lang = await db.get_user_language(test_user_id)
        assert lang == test_lang, f"Expected '{test_lang}', got '{lang}'"
        print(f"   ✓ {test_lang.upper()}: {lang}")
    
    # Test 5: Test with existing users from database
    print("\n5. Testing with existing users...")
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        async with conn.execute("SELECT user_id, language FROM bot_users LIMIT 5") as cursor:
            rows = await cursor.fetchall()
            if rows:
                for row in rows:
                    user_id, language = row
                    lang = await db.get_user_language(user_id)
                    print(f"   ✓ User {user_id}: {lang}")
            else:
                print("   ✓ No existing users to test (OK)")
    
    # Cleanup test user
    print("\n6. Cleaning up test data...")
    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute("DELETE FROM bot_users WHERE user_id = ?", (test_user_id,))
        await conn.commit()
    print("   ✓ Test user removed")
    
    print("\n" + "=" * 60)
    print("All tests passed! ✓")
    print("=" * 60)


async def verify_schema():
    """Verify the database schema has the correct language column."""
    import aiosqlite
    
    print("\n" + "=" * 60)
    print("Verifying Database Schema")
    print("=" * 60)
    
    async with aiosqlite.connect('catalog.db') as db:
        async with db.execute("PRAGMA table_info(bot_users)") as cursor:
            columns = await cursor.fetchall()
            
            print("\nbot_users table columns:")
            has_language = False
            has_preferred_language = False
            
            for col in columns:
                col_id, name, col_type, not_null, default_val, pk = col
                print(f"  {col_id}. {name} ({col_type})")
                if name == 'language':
                    has_language = True
                if name == 'preferred_language':
                    has_preferred_language = True
            
            print()
            if has_language and not has_preferred_language:
                print("✓ Schema verification passed!")
                print("  - 'language' column exists")
                print("  - 'preferred_language' column removed")
            elif has_preferred_language:
                print("✗ Schema verification failed!")
                print("  - 'preferred_language' column still exists")
                print("  - Migration not applied")
                return False
            elif not has_language:
                print("✗ Schema verification failed!")
                print("  - 'language' column does not exist")
                return False
    
    print("=" * 60)
    return True


async def main():
    """Run all tests."""
    try:
        # First verify schema
        if not await verify_schema():
            print("\n⚠ Schema verification failed. Run migration first:")
            print("  python migrate_language_column.py catalog.db")
            sys.exit(1)
        
        # Then test operations
        await test_language_operations()
        
        print("\n🎉 All tests completed successfully!")
        print("The language column migration is working correctly.\n")
        
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
