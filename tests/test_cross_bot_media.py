"""
Tests for cross-bot media delivery fallback mechanism.

Tests the functionality of downloading and re-uploading media when
a secondary bot encounters "Wrong file identifier" errors with file_ids
from a different bot.
"""
import os
import sys
import pytest
import pytest_asyncio
import asyncio
import tempfile
import io
from unittest.mock import Mock, AsyncMock, patch, MagicMock

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from utils.helpers import send_media_message, download_media_from_bot
from telegram.error import BadRequest
from telegram import Bot, File, InputFile


@pytest_asyncio.fixture
async def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(delete=False, suffix='.db') as f:
        db_path = f.name
    
    db = Database(db_path)
    await db.init_db()
    
    yield db
    
    # Cleanup
    try:
        os.unlink(db_path)
    except:
        pass


class TestProductUploaderBotId:
    """Test that products are created with uploader_bot_id."""
    
    @pytest.mark.asyncio
    async def test_add_product_with_uploader_bot_id(self, temp_db):
        """Test that add_product accepts and stores uploader_bot_id."""
        product_id = await temp_db.add_product(
            file_id="test_file_id_123",
            file_type="photo",
            caption="Test product",
            message_id=12345,
            chat_id=-100123456789,
            uploader_bot_id="BOT_TOKEN"
        )
        
        assert product_id > 0
        
        # Retrieve and verify
        product = await temp_db.get_product(product_id)
        assert product is not None
        assert product["uploader_bot_id"] == "BOT_TOKEN"
        assert product["file_id"] == "test_file_id_123"
    
    @pytest.mark.asyncio
    async def test_add_product_without_uploader_bot_id(self, temp_db):
        """Test backward compatibility - product without uploader_bot_id."""
        product_id = await temp_db.add_product(
            file_id="test_file_id_456",
            file_type="video",
            caption="Legacy product",
            message_id=12346,
            chat_id=-100123456789
        )
        
        assert product_id > 0
        
        # Retrieve and verify
        product = await temp_db.get_product(product_id)
        assert product is not None
        assert product["uploader_bot_id"] is None  # Should be NULL
        assert product["file_id"] == "test_file_id_456"


class TestDownloadMediaFromBot:
    """Test the download_media_from_bot helper function."""
    
    @pytest.mark.asyncio
    async def test_download_media_success(self):
        """Test successful media download from uploader bot."""
        # Mock bot and file
        mock_file = Mock(spec=File)
        mock_file.file_path = "https://api.telegram.org/file/bot123/photos/file.jpg"
        
        mock_bot = AsyncMock(spec=Bot)
        mock_bot.get_file = AsyncMock(return_value=mock_file)
        
        # Mock aiohttp response - must be a proper async context manager
        mock_response = MagicMock()
        mock_response.status = 200
        mock_response.read = AsyncMock(return_value=b"fake_image_bytes_12345")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        
        # Patch at the module level where it's imported, not where it's defined
        with patch('utils.helpers.Bot', return_value=mock_bot):
            with patch('utils.helpers.aiohttp.ClientSession', return_value=mock_session):
                media_bytes = await download_media_from_bot(
                    bot_token="fake_token_123",
                    file_id="test_file_id_789",
                    file_type="photo"
                )
        
        assert media_bytes == b"fake_image_bytes_12345"
        mock_bot.get_file.assert_called_once_with("test_file_id_789")
    
    @pytest.mark.asyncio
    async def test_download_media_http_error(self):
        """Test download failure due to HTTP error."""
        mock_file = Mock(spec=File)
        mock_file.file_path = "https://api.telegram.org/file/bot123/photos/file.jpg"
        
        mock_bot = AsyncMock(spec=Bot)
        mock_bot.get_file = AsyncMock(return_value=mock_file)
        
        # Mock failed HTTP response
        mock_response = MagicMock()
        mock_response.status = 404
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_response)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=None)
        
        with patch('utils.helpers.Bot', return_value=mock_bot):
            with patch('utils.helpers.aiohttp.ClientSession', return_value=mock_session):
                media_bytes = await download_media_from_bot(
                    bot_token="fake_token_123",
                    file_id="test_file_id_789",
                    file_type="photo"
                )
        
        assert media_bytes is None
    
    @pytest.mark.asyncio
    async def test_download_media_telegram_error(self):
        """Test download failure when Telegram get_file raises exception."""
        mock_bot = AsyncMock(spec=Bot)
        mock_bot.get_file = AsyncMock(side_effect=BadRequest("File not found"))
        
        with patch('utils.helpers.Bot', return_value=mock_bot):
            media_bytes = await download_media_from_bot(
                bot_token="fake_token_123",
                file_id="invalid_file_id",
                file_type="photo"
            )
        
        assert media_bytes is None


class TestSendMediaMessageFallback:
    """Test send_media_message fallback for cross-bot file_ids."""
    
    @pytest.mark.asyncio
    async def test_send_media_same_bot_success(self):
        """Test normal send (same bot) - no fallback needed."""
        mock_context = AsyncMock()
        mock_context.bot = AsyncMock()
        mock_context.bot.username = "test_bot"
        mock_context.bot.send_photo = AsyncMock()
        
        await send_media_message(
            context=mock_context,
            chat_id=12345,
            file_id="valid_file_id",
            file_type="photo",
            caption="Test caption",
            product_id=1,
            uploader_bot_id="BOT_TOKEN"
        )
        
        # Verify send_photo was called once successfully
        mock_context.bot.send_photo.assert_called_once()
        assert mock_context.bot.send_photo.call_args[1]['chat_id'] == 12345
        assert mock_context.bot.send_photo.call_args[1]['photo'] == "valid_file_id"
    
    @pytest.mark.asyncio
    async def test_send_media_cross_bot_fallback_success(self):
        """Test cross-bot fallback - download and re-upload."""
        # Setup mock context
        mock_context = AsyncMock()
        mock_context.bot = AsyncMock()
        mock_context.bot.username = "secondary_bot"
        
        # First send_photo fails with BadRequest, second succeeds
        mock_context.bot.send_photo = AsyncMock(
            side_effect=[
                BadRequest("Wrong file identifier/HTTP URL specified"),
                None  # Success on retry with InputFile
            ]
        )
        
        # Mock Config to have bot tokens
        mock_config = Mock()
        mock_config.BOT_TOKEN = "primary_bot_token"
        mock_config.BOT_TOKENS = ["primary_bot_token", "secondary_bot_token"]
        
        # Mock download_media_from_bot to return fake bytes
        fake_media_bytes = b"fake_image_data_xyz"
        
        with patch('utils.helpers.Config', mock_config):
            with patch('utils.helpers.download_media_from_bot', 
                      AsyncMock(return_value=fake_media_bytes)):
                await send_media_message(
                    context=mock_context,
                    chat_id=12345,
                    file_id="cross_bot_file_id",
                    file_type="photo",
                    caption="Test caption",
                    product_id=1,
                    uploader_bot_id="BOT_TOKEN"
                )
        
        # Verify send_photo was called twice (initial fail + retry)
        assert mock_context.bot.send_photo.call_count == 2
        
        # Second call should use InputFile
        second_call_kwargs = mock_context.bot.send_photo.call_args_list[1][1]
        assert second_call_kwargs['chat_id'] == 12345
        # The photo argument should be an InputFile wrapping our fake bytes
        assert isinstance(second_call_kwargs['photo'], InputFile)
    
    @pytest.mark.asyncio
    async def test_send_media_cross_bot_no_uploader_id(self):
        """Test fallback fails when uploader_bot_id is not provided."""
        mock_context = AsyncMock()
        mock_context.bot = AsyncMock()
        mock_context.bot.username = "test_bot"
        mock_context.bot.send_photo = AsyncMock(
            side_effect=BadRequest("Wrong file identifier/HTTP URL specified")
        )
        mock_context.bot.send_message = AsyncMock()  # Fallback to text
        
        # Should fall back to text message since no uploader_bot_id
        await send_media_message(
            context=mock_context,
            chat_id=12345,
            file_id="cross_bot_file_id",
            file_type="photo",
            caption="Test caption",
            product_id=1,
            uploader_bot_id=None  # No uploader_bot_id
        )
        
        # Should have tried send_photo once, then sent text fallback
        assert mock_context.bot.send_photo.call_count == 1
        assert mock_context.bot.send_message.call_count == 1
        
        # Verify fallback text includes error message
        fallback_call = mock_context.bot.send_message.call_args[1]
        assert "Media temporarily unavailable" in fallback_call['text']
    
    @pytest.mark.asyncio
    async def test_send_media_cross_bot_download_fails(self):
        """Test fallback when download from uploader bot fails."""
        mock_context = AsyncMock()
        mock_context.bot = AsyncMock()
        mock_context.bot.username = "secondary_bot"
        mock_context.bot.send_photo = AsyncMock(
            side_effect=BadRequest("Wrong file identifier/HTTP URL specified")
        )
        mock_context.bot.send_message = AsyncMock()  # Fallback to text
        
        # Mock Config
        mock_config = Mock()
        mock_config.BOT_TOKEN = "primary_bot_token"
        mock_config.BOT_TOKENS = ["primary_bot_token", "secondary_bot_token"]
        
        # Mock download to fail
        with patch('utils.helpers.Config', mock_config):
            with patch('utils.helpers.download_media_from_bot', 
                      AsyncMock(return_value=None)):  # Download failed
                await send_media_message(
                    context=mock_context,
                    chat_id=12345,
                    file_id="cross_bot_file_id",
                    file_type="photo",
                    caption="Test caption",
                    product_id=1,
                    uploader_bot_id="BOT_TOKEN"
                )
        
        # Should have tried send_photo once, then sent text fallback
        assert mock_context.bot.send_photo.call_count == 1
        assert mock_context.bot.send_message.call_count == 1
    
    @pytest.mark.asyncio
    async def test_send_media_video_type(self):
        """Test fallback works for video file type."""
        mock_context = AsyncMock()
        mock_context.bot = AsyncMock()
        mock_context.bot.username = "secondary_bot"
        
        # First send_video fails, second succeeds
        mock_context.bot.send_video = AsyncMock(
            side_effect=[
                BadRequest("Wrong file identifier/HTTP URL specified"),
                None
            ]
        )
        
        mock_config = Mock()
        mock_config.BOT_TOKEN = "primary_bot_token"
        mock_config.BOT_TOKENS = ["primary_bot_token"]
        
        with patch('utils.helpers.Config', mock_config):
            with patch('utils.helpers.download_media_from_bot', 
                      AsyncMock(return_value=b"fake_video_bytes")):
                await send_media_message(
                    context=mock_context,
                    chat_id=12345,
                    file_id="cross_bot_video_id",
                    file_type="video",
                    caption="Video caption",
                    product_id=2,
                    uploader_bot_id="BOT_TOKEN"
                )
        
        # Verify send_video was called twice
        assert mock_context.bot.send_video.call_count == 2


class TestBotTokenResolution:
    """Test uploader_bot_id to bot_token resolution."""
    
    @pytest.mark.asyncio
    async def test_resolve_primary_bot_token(self):
        """Test resolving BOT_TOKEN env key."""
        mock_context = AsyncMock()
        mock_context.bot = AsyncMock()
        mock_context.bot.username = "secondary_bot"
        mock_context.bot.send_photo = AsyncMock(
            side_effect=[
                BadRequest("Wrong file identifier/HTTP URL specified"),
                None
            ]
        )
        
        mock_config = Mock()
        mock_config.BOT_TOKEN = "primary_token_xyz"
        mock_config.BOT_TOKENS = ["primary_token_xyz", "secondary_token_abc"]
        
        # Track which token was used for download
        used_token = None
        
        async def mock_download(token, file_id, file_type):
            nonlocal used_token
            used_token = token
            return b"fake_bytes"
        
        with patch('utils.helpers.Config', mock_config):
            with patch('utils.helpers.download_media_from_bot', 
                      AsyncMock(side_effect=mock_download)):
                await send_media_message(
                    context=mock_context,
                    chat_id=12345,
                    file_id="test_id",
                    file_type="photo",
                    uploader_bot_id="BOT_TOKEN"
                )
        
        # Verify primary token was used
        assert used_token == "primary_token_xyz"
    
    @pytest.mark.asyncio
    async def test_resolve_secondary_bot_token(self):
        """Test resolving BOT_TOKEN_1 env key."""
        mock_context = AsyncMock()
        mock_context.bot = AsyncMock()
        mock_context.bot.username = "primary_bot"
        mock_context.bot.send_photo = AsyncMock(
            side_effect=[
                BadRequest("Wrong file identifier/HTTP URL specified"),
                None
            ]
        )
        
        mock_config = Mock()
        mock_config.BOT_TOKEN = "primary_token_xyz"
        mock_config.BOT_TOKENS = ["primary_token_xyz", "secondary_token_abc"]
        
        # Track which token was used
        used_token = None
        
        async def mock_download(token, file_id, file_type):
            nonlocal used_token
            used_token = token
            return b"fake_bytes"
        
        with patch('utils.helpers.Config', mock_config):
            with patch('utils.helpers.download_media_from_bot', 
                      AsyncMock(side_effect=mock_download)):
                await send_media_message(
                    context=mock_context,
                    chat_id=12345,
                    file_id="test_id",
                    file_type="photo",
                    uploader_bot_id="BOT_TOKEN_1"
                )
        
        # Verify secondary token was used
        assert used_token == "secondary_token_abc"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
