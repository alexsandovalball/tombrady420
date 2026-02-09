"""
Tests for bot-scoped file_id fallback and caching mechanism.

Tests the per-bot file_id caching functionality that solves "Wrong file identifier"
errors when secondary bots try to send media using file_ids from other bots.
"""
import os
import sys
import pytest
import pytest_asyncio
import asyncio
import tempfile
import io
from unittest.mock import Mock, AsyncMock, patch, MagicMock, call

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database
from utils.helpers import send_media_message
from telegram.error import BadRequest
from telegram import Bot, Message, PhotoSize, Video, Document, InputFile


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


class TestBotFileIdCaching:
    """Test bot-specific file_id caching in the database."""
    
    @pytest.mark.asyncio
    async def test_cache_miss_returns_none(self, temp_db):
        """Test that get_bot_file_id returns None when no cache entry exists."""
        file_id = await temp_db.get_bot_file_id('BOT_TOKEN', 123)
        assert file_id is None
    
    @pytest.mark.asyncio
    async def test_set_and_get_bot_file_id(self, temp_db):
        """Test caching and retrieving a bot-specific file_id."""
        success = await temp_db.set_bot_file_id('BOT_TOKEN', 123, 'file_id_bot0_123')
        assert success is True
        
        file_id = await temp_db.get_bot_file_id('BOT_TOKEN', 123)
        assert file_id == 'file_id_bot0_123'
    
    @pytest.mark.asyncio
    async def test_upsert_updates_existing_file_id(self, temp_db):
        """Test that set_bot_file_id updates an existing cache entry."""
        await temp_db.set_bot_file_id('BOT_TOKEN', 123, 'old_file_id')
        await temp_db.set_bot_file_id('BOT_TOKEN', 123, 'new_file_id')
        
        file_id = await temp_db.get_bot_file_id('BOT_TOKEN', 123)
        assert file_id == 'new_file_id'
    
    @pytest.mark.asyncio
    async def test_different_bots_different_cache(self, temp_db):
        """Test that different bots have separate cache entries for the same resource."""
        await temp_db.set_bot_file_id('BOT_TOKEN', 123, 'file_id_bot0')
        await temp_db.set_bot_file_id('BOT_TOKEN_1', 123, 'file_id_bot1')
        
        file_id_bot0 = await temp_db.get_bot_file_id('BOT_TOKEN', 123)
        file_id_bot1 = await temp_db.get_bot_file_id('BOT_TOKEN_1', 123)
        
        assert file_id_bot0 == 'file_id_bot0'
        assert file_id_bot1 == 'file_id_bot1'
    
    @pytest.mark.asyncio
    async def test_invalidate_bot_file_id(self, temp_db):
        """Test invalidating (deleting) a cached file_id."""
        await temp_db.set_bot_file_id('BOT_TOKEN', 123, 'file_id_123')
        
        deleted = await temp_db.invalidate_bot_file_id('BOT_TOKEN', 123)
        assert deleted is True
        
        file_id = await temp_db.get_bot_file_id('BOT_TOKEN', 123)
        assert file_id is None
    
    @pytest.mark.asyncio
    async def test_invalidate_nonexistent_returns_false(self, temp_db):
        """Test that invalidating a nonexistent cache entry returns False."""
        deleted = await temp_db.invalidate_bot_file_id('BOT_TOKEN', 999)
        assert deleted is False


class TestSendMediaWithCaching:
    """Test send_media_message with bot-specific file_id caching."""
    
    @pytest.mark.asyncio
    async def test_successful_send_caches_file_id(self, temp_db):
        """Test that successful send caches the bot-specific file_id."""
        # Mock context and sent message
        mock_context = AsyncMock()
        mock_context.bot.username = 'test_bot'
        mock_context.bot.send_photo = AsyncMock()
        
        # Mock sent message with photo
        mock_sent_msg = Mock(spec=Message)
        mock_photo = Mock(spec=PhotoSize)
        mock_photo.file_id = 'bot_specific_file_id_123'
        mock_sent_msg.photo = [mock_photo]
        
        mock_context.bot.send_photo.return_value = mock_sent_msg
        
        # Patch get_bot_env_key to return consistent bot key
        with patch('utils.bot_key.get_bot_env_key', return_value='BOT_TOKEN'):
            await send_media_message(
                context=mock_context,
                chat_id=12345,
                file_id='original_file_id',
                file_type='photo',
                caption='Test photo',
                product_id=456,
                db=temp_db
            )
        
        # Verify file_id was cached
        cached_file_id = await temp_db.get_bot_file_id('BOT_TOKEN', 456)
        assert cached_file_id == 'bot_specific_file_id_123'
    
    @pytest.mark.asyncio
    async def test_uses_cached_file_id_on_subsequent_send(self, temp_db):
        """Test that cached file_id is used on subsequent sends."""
        # Pre-cache a file_id
        await temp_db.set_bot_file_id('BOT_TOKEN', 456, 'cached_file_id_456')
        
        # Mock context
        mock_context = AsyncMock()
        mock_context.bot.username = 'test_bot'
        mock_context.bot.send_photo = AsyncMock()
        
        mock_sent_msg = Mock(spec=Message)
        mock_photo = Mock(spec=PhotoSize)
        mock_photo.file_id = 'new_file_id'
        mock_sent_msg.photo = [mock_photo]
        mock_context.bot.send_photo.return_value = mock_sent_msg
        
        with patch('utils.bot_key.get_bot_env_key', return_value='BOT_TOKEN'):
            await send_media_message(
                context=mock_context,
                chat_id=12345,
                file_id='original_file_id',
                file_type='photo',
                product_id=456,
                db=temp_db
            )
        
        # Verify send_photo was called with the cached file_id
        mock_context.bot.send_photo.assert_called_once()
        call_args = mock_context.bot.send_photo.call_args
        assert call_args.kwargs['photo'] == 'cached_file_id_456'
    
    @pytest.mark.asyncio
    async def test_invalidates_cache_on_wrong_file_identifier(self, temp_db):
        """Test that cached file_id is invalidated when it causes BadRequest."""
        # Pre-cache a file_id that will fail
        await temp_db.set_bot_file_id('BOT_TOKEN', 456, 'bad_cached_file_id')
        
        # Mock context
        mock_context = AsyncMock()
        mock_context.bot.username = 'test_bot'
        mock_context.bot.send_photo = AsyncMock()
        mock_context.bot.send_message = AsyncMock()
        
        # First call with cached file_id fails, no fallback (no uploader_bot_id)
        mock_context.bot.send_photo.side_effect = BadRequest("Wrong file identifier/http url specified")
        
        with patch('utils.bot_key.get_bot_env_key', return_value='BOT_TOKEN'):
            await send_media_message(
                context=mock_context,
                chat_id=12345,
                file_id='original_file_id',
                file_type='photo',
                product_id=456,
                db=temp_db
            )
        
        # Verify cached file_id was invalidated
        cached_file_id = await temp_db.get_bot_file_id('BOT_TOKEN', 456)
        assert cached_file_id is None
        
        # Verify fallback to text message was sent
        mock_context.bot.send_message.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_fallback_reupload_caches_new_file_id(self, temp_db):
        """Test that successful fallback reupload caches the new bot-specific file_id."""
        # Mock context
        mock_context = AsyncMock()
        mock_context.bot.username = 'test_bot'
        
        # First send attempt fails, second succeeds (after reupload)
        mock_sent_msg = Mock(spec=Message)
        mock_photo = Mock(spec=PhotoSize)
        mock_photo.file_id = 'new_bot_file_id_after_reupload'
        mock_sent_msg.photo = [mock_photo]
        
        mock_context.bot.send_photo = AsyncMock()
        mock_context.bot.send_photo.side_effect = [
            BadRequest("Wrong file identifier/http url specified"),  # First attempt fails
            mock_sent_msg  # Reupload succeeds
        ]
        
        # Mock download_media_from_bot
        with patch('utils.helpers.download_media_from_bot', new_callable=AsyncMock) as mock_download:
            mock_download.return_value = b"fake_image_bytes"
            
            # Mock Config.BOT_TOKENS
            with patch('utils.helpers.Config.BOT_TOKENS', ['token0', 'token1']):
                with patch('utils.bot_key.get_bot_env_key', return_value='BOT_TOKEN_1'):
                    await send_media_message(
                        context=mock_context,
                        chat_id=12345,
                        file_id='original_file_id',
                        file_type='photo',
                        caption='Test',
                        product_id=456,
                        uploader_bot_id='BOT_TOKEN',
                        db=temp_db
                    )
        
        # Verify new file_id was cached for the current bot
        cached_file_id = await temp_db.get_bot_file_id('BOT_TOKEN_1', 456)
        assert cached_file_id == 'new_bot_file_id_after_reupload'
        
        # Verify send_photo was called twice (failed attempt + successful reupload)
        assert mock_context.bot.send_photo.call_count == 2


class TestSendMediaVideoAndDocument:
    """Test send_media_message caching for video and document types."""
    
    @pytest.mark.asyncio
    async def test_caches_video_file_id(self, temp_db):
        """Test that video file_id is cached correctly."""
        mock_context = AsyncMock()
        mock_context.bot.username = 'test_bot'
        mock_context.bot.send_video = AsyncMock()
        
        mock_sent_msg = Mock(spec=Message)
        mock_video = Mock(spec=Video)
        mock_video.file_id = 'video_file_id_123'
        mock_sent_msg.video = mock_video
        
        mock_context.bot.send_video.return_value = mock_sent_msg
        
        with patch('utils.bot_key.get_bot_env_key', return_value='BOT_TOKEN'):
            await send_media_message(
                context=mock_context,
                chat_id=12345,
                file_id='original_video_id',
                file_type='video',
                product_id=789,
                db=temp_db
            )
        
        cached_file_id = await temp_db.get_bot_file_id('BOT_TOKEN', 789)
        assert cached_file_id == 'video_file_id_123'
    
    @pytest.mark.asyncio
    async def test_caches_document_file_id(self, temp_db):
        """Test that document file_id is cached correctly."""
        mock_context = AsyncMock()
        mock_context.bot.username = 'test_bot'
        mock_context.bot.send_document = AsyncMock()
        
        mock_sent_msg = Mock(spec=Message)
        mock_doc = Mock(spec=Document)
        mock_doc.file_id = 'doc_file_id_123'
        mock_sent_msg.document = mock_doc
        
        mock_context.bot.send_document.return_value = mock_sent_msg
        
        with patch('utils.bot_key.get_bot_env_key', return_value='BOT_TOKEN'):
            await send_media_message(
                context=mock_context,
                chat_id=12345,
                file_id='original_doc_id',
                file_type='document',
                product_id=999,
                db=temp_db
            )
        
        cached_file_id = await temp_db.get_bot_file_id('BOT_TOKEN', 999)
        assert cached_file_id == 'doc_file_id_123'
