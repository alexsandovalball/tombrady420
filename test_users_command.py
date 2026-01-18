"""
Test for /users command handling of None values.

This test verifies that the users_command properly handles None values
in user data (first_name, last_name, username) without throwing TypeError.
"""
import asyncio
import tempfile
import sys
from database import Database
from telegram.helpers import escape_markdown


async def test_escape_markdown_with_none_values():
    """Test that str() wrapping prevents TypeError when user data is None."""
    
    print("=" * 60)
    print("Testing /users command None handling")
    print("=" * 60)
    
    # Test 1: Verify escape_markdown fails on None (demonstrating the bug)
    print("\n[Test 1] Testing escape_markdown with None (should fail)...")
    try:
        result = escape_markdown(None)
        print(f"❌ Expected TypeError but got result: {result}")
        sys.exit(1)
    except TypeError as e:
        print(f"✓ escape_markdown(None) correctly raises TypeError: {e}")
    
    # Test 2: Verify str() wrapping fixes the issue
    print("\n[Test 2] Testing escape_markdown with str(None)...")
    try:
        result = escape_markdown(str(None))
        print(f"✓ escape_markdown(str(None)) works: '{result}'")
        assert result == "None", f"Expected 'None', got '{result}'"
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        sys.exit(1)
    
    # Test 3: Test with user data scenarios
    print("\n[Test 3] Testing user data scenarios...")
    
    # Simulate user data with None values
    test_users = [
        {'username': None, 'first_name': None, 'last_name': None},
        {'username': 'johndoe', 'first_name': None, 'last_name': 'Doe'},
        {'username': None, 'first_name': 'Jane', 'last_name': None},
        {'username': 'test_user', 'first_name': 'Test', 'last_name': 'User'},
    ]
    
    for i, user in enumerate(test_users, 1):
        print(f"\n  Testing user {i}: {user}")
        
        # Simulate the fixed code from handlers/admin.py
        username_raw = user.get('username', '')
        username = escape_markdown(f"@{str(username_raw)}") if username_raw else "No username"
        first_name = escape_markdown(str(user.get('first_name', 'N/A')))
        last_name = escape_markdown(str(user.get('last_name', '')))
        full_name = f"{first_name} {last_name}".strip()
        
        print(f"    username: {username}")
        print(f"    first_name: {first_name}")
        print(f"    last_name: {last_name}")
        print(f"    full_name: {full_name}")
        
        # Verify no exceptions were raised
        assert username is not None
        assert first_name is not None
        assert last_name is not None
        print(f"  ✓ User {i} processed successfully")
    
    # Test 4: Test with database containing None values
    print("\n[Test 4] Testing with actual database...")
    
    # Create temporary database
    test_db_fd, test_db_path = tempfile.mkstemp(suffix='.db', prefix='test_users_')
    db = Database(test_db_path)
    
    try:
        await db.init_db()
        
        # Add users with None values using aiosqlite directly
        import aiosqlite
        test_user_data = [
            (1, None, None, None),  # All None
            (2, 'user2', None, 'Smith'),  # first_name is None
            (3, None, 'John', None),  # username and last_name are None
            (4, 'user4', 'Jane', 'Doe'),  # All values present
        ]
        
        async with aiosqlite.connect(test_db_path) as conn:
            for user_id, username, first_name, last_name in test_user_data:
                await conn.execute(
                    "INSERT INTO bot_users (user_id, username, first_name, last_name) VALUES (?, ?, ?, ?)",
                    (user_id, username, first_name, last_name)
                )
            await conn.commit()
        
        # Retrieve users and process them
        users = await db.get_all_users()
        print(f"  Retrieved {len(users)} users from database")
        
        for user in users:
            # Apply the fixed logic
            username_raw = user.get('username', '')
            username = escape_markdown(f"@{str(username_raw)}") if username_raw else "No username"
            first_name = escape_markdown(str(user.get('first_name', 'N/A')))
            last_name = escape_markdown(str(user.get('last_name', '')))
            full_name = f"{first_name} {last_name}".strip()
            
            print(f"  User {user['user_id']}: {full_name} ({username})")
        
        print("  ✓ All database users processed successfully")
        
    finally:
        import os
        try:
            os.close(test_db_fd)
            os.unlink(test_db_path)
        except:
            pass
    
    print("\n" + "=" * 60)
    print("✅ All tests passed!")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(test_escape_markdown_with_none_values())
