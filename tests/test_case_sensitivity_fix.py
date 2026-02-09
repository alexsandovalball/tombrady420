"""
Test case-sensitivity fix for bot_username that was causing 'invalid_for_bot' errors.

This test simulates the exact scenario from the production logs where:
1. User started a bot with username 'tombrady420mediabot'  
2. Message was queued to bot '@tombrady420mediabot'
3. But validation failed because case didn't match in database
"""
import os
import sys
import pytest
import pytest_asyncio
import tempfile

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from utils.bot_key import set_bot_username


@pytest_asyncio.fixture
async def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name
    
    db = Database(db_path)
    await db.init_db()
    yield db
    
    # Cleanup
    import os
    try:
        os.unlink(db_path)
    except:
        pass


class TestCaseSensitivityFix:
    """Test that case-sensitivity fixes resolve 'invalid_for_bot' errors."""
    
    @pytest.mark.asyncio
    async def test_scenario_from_production_logs(self, temp_db):
        """
        Simulate the exact scenario from production logs:
        - User 8446142277 started 'Tbradymediabot'
        - Message queued to '@Tbradymediabot'
        - Should find user even with different casing
        """
        # Set up bot
        set_bot_username('Tbradymediabot')
        
        # User started bot with mixed case (simulating Telegram API returning this)
        await temp_db.track_user(8446142277, 'user8446', 'Test', 'User', bot_username='Tbradymediabot')
        
        # Try to get users for this bot (simulating get_users_by_bot call)
        users = await temp_db.get_users_by_bot('Tbradymediabot')
        
        # User should be found even though stored as lowercase
        assert 8446142277 in users, "User should be found with case-insensitive matching"
        
        # Try with different casing
        users_lower = await temp_db.get_users_by_bot('tbradymediabot')
        assert 8446142277 in users_lower, "User should be found with lowercase query"
        
        users_upper = await temp_db.get_users_by_bot('TBRADYMEDIABOT')
        assert 8446142277 in users_upper, "User should be found with uppercase query"
    
    @pytest.mark.asyncio
    async def test_scenario_user_7808719305(self, temp_db):
        """
        Simulate another scenario from logs:
        - User 7808719305 started 'tombrady420mediabot'
        - Should match regardless of case
        """
        set_bot_username('tombrady420mediabot')
        
        # Track user
        await temp_db.track_user(7808719305, 'user7808', 'Test', 'User', bot_username='tombrady420mediabot')
        
        # Verify bot_username was normalized to lowercase
        user = await temp_db.get_user(7808719305)
        assert user['bot_username'] == 'tombrady420mediabot', "bot_username should be stored as lowercase"
        
        # Verify user is found with any case variation
        users = await temp_db.get_users_by_bot('tombrady420mediabot')
        assert 7808719305 in users
        
        users = await temp_db.get_users_by_bot('Tombrady420mediabot')
        assert 7808719305 in users
        
        users = await temp_db.get_users_by_bot('TOMBRADY420MEDIABOT')
        assert 7808719305 in users
    
    @pytest.mark.asyncio
    async def test_bot_key_mapping_case_insensitive(self, temp_db):
        """Test that bot_key mapping works with case variations."""
        # User started a bot and has a last_bot_key
        await temp_db.track_user(999, 'user999', 'Test', 'User', bot_username='MixedCaseBot')
        await temp_db.set_last_bot_key(999, 'BOT_TOKEN_2')
        
        # Should find bot_key regardless of case in query
        bot_key = await temp_db.get_bot_key_for_username('MixedCaseBot')
        assert bot_key == 'BOT_TOKEN_2'
        
        bot_key = await temp_db.get_bot_key_for_username('mixedcasebot')
        assert bot_key == 'BOT_TOKEN_2'
        
        bot_key = await temp_db.get_bot_key_for_username('MIXEDCASEBOT')
        assert bot_key == 'BOT_TOKEN_2'
    
    @pytest.mark.asyncio
    async def test_multiple_users_different_cases(self, temp_db):
        """Test that multiple users with same bot (different cases) are all found."""
        # Simulate different users starting the same bot with different case variations
        # (this could happen if bot username changed case or from different API responses)
        await temp_db.track_user(1001, 'user1001', 'User', '1', bot_username='MyBot')
        await temp_db.track_user(1002, 'user1002', 'User', '2', bot_username='mybot')  
        await temp_db.track_user(1003, 'user1003', 'User', '3', bot_username='MYBOT')
        await temp_db.track_user(1004, 'user1004', 'User', '4', bot_username='mYbOt')
        
        # All should be normalized to lowercase and found together
        users = await temp_db.get_users_by_bot('MyBot')
        assert len(users) == 4
        assert set(users) == {1001, 1002, 1003, 1004}
        
        # Should work with any case variation
        users = await temp_db.get_users_by_bot('mybot')
        assert len(users) == 4
        
        users = await temp_db.get_users_by_bot('MYBOT')
        assert len(users) == 4
