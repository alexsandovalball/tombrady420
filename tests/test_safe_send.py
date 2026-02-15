"""
Test safe send/edit wrappers with entity parsing fallback and user cleanup.

These tests validate that:
1. Entity parsing errors trigger fallback behavior
2. Deleted/deactivated users are removed from database
3. Safe wrappers handle all error types gracefully
"""
import asyncio
import aiosqlite
import os
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from telegram.error import BadRequest, Forbidden, TelegramError

# Import the Database class and safe send functions
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from database import Database
from utils.safe_send import (
    safe_send_message,
    safe_edit_message_text,
    safe_send_photo,
    safe_send_video,
    handle_send_exception,
    escape_text_for_parse_mode
)


class TestEscapeTextForParseMode(unittest.TestCase):
    """Test text escaping for different parse modes."""
    
    def test_escape_html(self):
        """Test HTML escaping."""
        text = "<b>Test</b> & \"quotes\""
        escaped = escape_text_for_parse_mode(text, "HTML")
        self.assertIn("&lt;", escaped)
        self.assertIn("&gt;", escaped)
        self.assertIn("&amp;", escaped)
    
    def test_escape_markdown(self):
        """Test Markdown escaping."""
        text = "Test *bold* _italic_ `code` [link]"
        escaped = escape_text_for_parse_mode(text, "Markdown")
        self.assertIn(r"\*", escaped)
        self.assertIn(r"\_", escaped)
        self.assertIn(r"\`", escaped)
        self.assertIn(r"\[", escaped)
    
    def test_no_escape_for_none(self):
        """Test that no escaping happens when parse_mode is None."""
        text = "Test *bold* _italic_"
        escaped = escape_text_for_parse_mode(text, None)
        self.assertEqual(text, escaped)


class TestHandleSendException(unittest.TestCase):
    """Test exception handling and user cleanup."""
    
    def setUp(self):
        """Create a temporary database for testing."""
        self.temp_db = tempfile.NamedTemporaryFile(delete=False, suffix='.db')
        self.temp_db.close()
        self.db_path = self.temp_db.name
        self.db = Database(db_path=self.db_path)
        
        # Initialize database
        asyncio.run(self.db.init_db())
    
    def tearDown(self):
        """Clean up temporary database."""
        if os.path.exists(self.db_path):
            os.unlink(self.db_path)
    
    def test_forbidden_triggers_user_deletion(self):
        """Test that Forbidden error triggers user deletion."""
        async def test():
            # Create a test user
            user_id = 12345
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "INSERT INTO bot_users (user_id, username) VALUES (?, ?)",
                    (user_id, "testuser")
                )
                await db.commit()
            
            # Handle Forbidden exception
            exception = Forbidden("Bot was blocked by the user")
            with patch('utils.safe_send.Database') as mock_db_class:
                mock_db_instance = AsyncMock()
                mock_db_class.return_value = mock_db_instance
                
                result = await handle_send_exception(exception, user_id)
                
                # Verify user deletion was called
                self.assertTrue(result)
                mock_db_instance.delete_user.assert_called_once_with(user_id)
        
        asyncio.run(test())
    
    def test_badrequest_user_deactivated_triggers_deletion(self):
        """Test that BadRequest with 'user is deactivated' triggers deletion."""
        async def test():
            user_id = 67890
            
            exception = BadRequest("User is deactivated")
            with patch('utils.safe_send.Database') as mock_db_class:
                mock_db_instance = AsyncMock()
                mock_db_class.return_value = mock_db_instance
                
                result = await handle_send_exception(exception, user_id)
                
                # Verify user deletion was called
                self.assertTrue(result)
                mock_db_instance.delete_user.assert_called_once_with(user_id)
        
        asyncio.run(test())
    
    def test_badrequest_chat_not_found_triggers_deletion(self):
        """Test that BadRequest with 'chat not found' triggers deletion."""
        async def test():
            user_id = 11111
            
            exception = BadRequest("Chat not found")
            with patch('utils.safe_send.Database') as mock_db_class:
                mock_db_instance = AsyncMock()
                mock_db_class.return_value = mock_db_instance
                
                result = await handle_send_exception(exception, user_id)
                
                # Verify user deletion was called
                self.assertTrue(result)
                mock_db_instance.delete_user.assert_called_once_with(user_id)
        
        asyncio.run(test())
    
    def test_badrequest_entity_error_does_not_trigger_deletion(self):
        """Test that entity parsing errors do NOT trigger user deletion."""
        async def test():
            user_id = 22222
            
            exception = BadRequest("Can't find end of the entity starting at byte offset 10")
            result = await handle_send_exception(exception, user_id)
            
            # User should NOT be deleted for parsing errors
            self.assertFalse(result)
        
        asyncio.run(test())


class TestSafeSendMessage(unittest.TestCase):
    """Test safe_send_message function."""
    
    def test_successful_send(self):
        """Test successful message send."""
        async def test():
            mock_bot = AsyncMock()
            mock_message = MagicMock()
            mock_bot.send_message.return_value = mock_message
            
            result = await safe_send_message(
                bot=mock_bot,
                chat_id=12345,
                text="Test message"
            )
            
            self.assertEqual(result, mock_message)
            mock_bot.send_message.assert_called_once()
        
        asyncio.run(test())
    
    def test_entity_parsing_fallback(self):
        """Test that entity parsing errors trigger fallback."""
        async def test():
            mock_bot = AsyncMock()
            mock_message = MagicMock()
            
            # First call fails with entity error, second succeeds
            mock_bot.send_message.side_effect = [
                BadRequest("Can't find end of the entity starting at byte offset 10"),
                mock_message  # Success on retry
            ]
            
            result = await safe_send_message(
                bot=mock_bot,
                chat_id=12345,
                text="Test *message*",
                parse_mode="Markdown"
            )
            
            # Should have been called twice (initial + retry)
            self.assertEqual(mock_bot.send_message.call_count, 2)
            self.assertEqual(result, mock_message)
        
        asyncio.run(test())
    
    def test_final_fallback_to_plain_text(self):
        """Test final fallback to plain text with no parse_mode."""
        async def test():
            mock_bot = AsyncMock()
            mock_message = MagicMock()
            
            # First two calls fail, third succeeds with plain text
            mock_bot.send_message.side_effect = [
                BadRequest("Can't find end of the entity starting at byte offset 10"),
                BadRequest("Can't parse entities: error"),
                mock_message  # Success on plain text retry
            ]
            
            result = await safe_send_message(
                bot=mock_bot,
                chat_id=12345,
                text="Test *message*",
                parse_mode="Markdown"
            )
            
            # Should have been called 3 times
            self.assertEqual(mock_bot.send_message.call_count, 3)
            
            # Last call should have parse_mode=None
            last_call = mock_bot.send_message.call_args_list[-1]
            self.assertIsNone(last_call.kwargs.get('parse_mode'))
            
            self.assertEqual(result, mock_message)
        
        asyncio.run(test())
    
    def test_forbidden_user_cleanup(self):
        """Test that Forbidden error triggers user cleanup."""
        async def test():
            mock_bot = AsyncMock()
            mock_bot.send_message.side_effect = Forbidden("Bot was blocked by the user")
            
            with patch('utils.safe_send.handle_send_exception') as mock_handler:
                mock_handler.return_value = True
                
                result = await safe_send_message(
                    bot=mock_bot,
                    chat_id=12345,
                    text="Test message"
                )
                
                # Should return None on error
                self.assertIsNone(result)
                
                # Should have called exception handler
                mock_handler.assert_called_once()
        
        asyncio.run(test())


class TestSafeEditMessageText(unittest.TestCase):
    """Test safe_edit_message_text function."""
    
    def test_successful_edit(self):
        """Test successful message edit."""
        async def test():
            mock_bot = AsyncMock()
            mock_message = MagicMock()
            mock_bot.edit_message_text.return_value = mock_message
            
            result = await safe_edit_message_text(
                bot=mock_bot,
                chat_id=12345,
                message_id=67890,
                text="Updated text"
            )
            
            self.assertEqual(result, mock_message)
            mock_bot.edit_message_text.assert_called_once()
        
        asyncio.run(test())
    
    def test_entity_parsing_fallback(self):
        """Test that entity parsing errors trigger fallback."""
        async def test():
            mock_bot = AsyncMock()
            mock_message = MagicMock()
            
            # First call fails with entity error, second succeeds
            mock_bot.edit_message_text.side_effect = [
                BadRequest("Can't parse entities: error at position 5"),
                mock_message  # Success on retry
            ]
            
            result = await safe_edit_message_text(
                bot=mock_bot,
                chat_id=12345,
                message_id=67890,
                text="Test **message**",
                parse_mode="Markdown"
            )
            
            # Should have been called twice (initial + retry)
            self.assertEqual(mock_bot.edit_message_text.call_count, 2)
            self.assertEqual(result, mock_message)
        
        asyncio.run(test())


class TestSafeSendPhoto(unittest.TestCase):
    """Test safe_send_photo function."""
    
    def test_successful_send(self):
        """Test successful photo send."""
        async def test():
            mock_bot = AsyncMock()
            mock_message = MagicMock()
            mock_bot.send_photo.return_value = mock_message
            
            result = await safe_send_photo(
                bot=mock_bot,
                chat_id=12345,
                photo="file_id_123",
                caption="Test caption"
            )
            
            self.assertEqual(result, mock_message)
            mock_bot.send_photo.assert_called_once()
        
        asyncio.run(test())
    
    def test_caption_parsing_fallback(self):
        """Test that caption parsing errors trigger fallback."""
        async def test():
            mock_bot = AsyncMock()
            mock_message = MagicMock()
            
            # First call fails with entity error, second succeeds
            mock_bot.send_photo.side_effect = [
                BadRequest("Can't find end of the entity in caption"),
                mock_message  # Success on retry
            ]
            
            result = await safe_send_photo(
                bot=mock_bot,
                chat_id=12345,
                photo="file_id_123",
                caption="Photo *caption*",
                parse_mode="Markdown"
            )
            
            # Should have been called twice (initial + retry)
            self.assertEqual(mock_bot.send_photo.call_count, 2)
            self.assertEqual(result, mock_message)
        
        asyncio.run(test())


class TestSafeSendVideo(unittest.TestCase):
    """Test safe_send_video function."""
    
    def test_successful_send(self):
        """Test successful video send."""
        async def test():
            mock_bot = AsyncMock()
            mock_message = MagicMock()
            mock_bot.send_video.return_value = mock_message
            
            result = await safe_send_video(
                bot=mock_bot,
                chat_id=12345,
                video="file_id_456",
                caption="Test video caption"
            )
            
            self.assertEqual(result, mock_message)
            mock_bot.send_video.assert_called_once()
        
        asyncio.run(test())
    
    def test_caption_parsing_fallback(self):
        """Test that caption parsing errors trigger fallback."""
        async def test():
            mock_bot = AsyncMock()
            mock_message = MagicMock()
            
            # First call fails with entity error, second succeeds
            mock_bot.send_video.side_effect = [
                BadRequest("Can't parse entities in video caption"),
                mock_message  # Success on retry
            ]
            
            result = await safe_send_video(
                bot=mock_bot,
                chat_id=12345,
                video="file_id_456",
                caption="Video **caption**",
                parse_mode="Markdown"
            )
            
            # Should have been called twice (initial + retry)
            self.assertEqual(mock_bot.send_video.call_count, 2)
            self.assertEqual(result, mock_message)
        
        asyncio.run(test())


if __name__ == '__main__':
    unittest.main()
