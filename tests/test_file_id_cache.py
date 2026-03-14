"""
Tests for the bot-specific file_id cache (get_bot_specific_file_id).

These tests exercise:
- Cache hit: cached file_id is returned directly without forwarding
- Cache miss: file is forwarded and result cached
- Invalid cached file_id: cache entry is cleared and file is re-fetched
- Cache persistence via Database.get_bot_file_id / cache_bot_file_id
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_bot(username: str = "testbot") -> MagicMock:
    """Build a minimal Bot mock that looks like a python-telegram-bot Bot."""
    # Use spec= to restrict attributes so hasattr(bot, 'bot') returns False
    # (otherwise MagicMock auto-creates a 'bot' attribute which confuses the
    # bot-vs-context detection logic in get_bot_specific_file_id).
    bot = MagicMock(spec=["username", "forward_message", "delete_message", "get_file"])
    bot.username = username
    bot.get_file = AsyncMock()
    bot.forward_message = AsyncMock()
    bot.delete_message = AsyncMock()
    return bot


def _make_forwarded_photo(file_id: str) -> MagicMock:
    """Build a minimal forwarded message mock with a single photo."""
    photo = MagicMock()
    photo.file_id = file_id

    msg = MagicMock()
    msg.message_id = 9999
    msg.photo = [photo]       # list of PhotoSize
    msg.video = None
    msg.document = None
    msg.animation = None
    msg.video_note = None
    msg.audio = None
    msg.voice = None
    return msg


# ---------------------------------------------------------------------------
# Cache hit
# ---------------------------------------------------------------------------

class TestGetBotSpecificFileIdCacheHit:
    """Verify that a warm database cache is used without forwarding."""

    @pytest.mark.asyncio
    async def test_returns_cached_file_id_without_forwarding(self):
        """When a valid cached file_id exists, forwarding must NOT be called."""
        bot = _make_bot()
        # get_file succeeds → cached id is valid
        bot.get_file.return_value = MagicMock()

        cached_id = "CACHED_FILE_ID_XYZ"

        with patch("utils.helpers._db") as mock_db:
            mock_db.get_bot_file_id = AsyncMock(return_value=cached_id)

            from utils.helpers import get_bot_specific_file_id
            result = await get_bot_specific_file_id(
                bot_or_context=bot,
                source_chat_id=-1001234567890,
                source_message_id=42,
                file_type="photo",
                file_index=0,
            )

        assert result == cached_id
        bot.forward_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_key_includes_bot_username(self):
        """The DB lookup must be keyed by bot username, chat, message, and index."""
        bot = _make_bot(username="myspecialbot")
        bot.get_file.return_value = MagicMock()

        with patch("utils.helpers._db") as mock_db:
            mock_db.get_bot_file_id = AsyncMock(return_value="FILEID_FOR_MYBOT")

            from utils.helpers import get_bot_specific_file_id
            await get_bot_specific_file_id(
                bot_or_context=bot,
                source_chat_id=-1001,
                source_message_id=7,
                file_type="video",
                file_index=2,
            )

            mock_db.get_bot_file_id.assert_awaited_once_with(-1001, 7, 2, "myspecialbot")


# ---------------------------------------------------------------------------
# Cache miss (forward path)
# ---------------------------------------------------------------------------

class TestGetBotSpecificFileIdCacheMiss:
    """Verify that a cache miss triggers forwarding and caches the result."""

    @pytest.mark.asyncio
    async def test_forwards_and_caches_on_miss(self):
        """On cache miss, the message is forwarded and the file_id is cached."""
        bot = _make_bot()
        new_file_id = "FRESHLY_FORWARDED_FILE_ID"
        forwarded_msg = _make_forwarded_photo(new_file_id)
        bot.forward_message.return_value = forwarded_msg

        with patch("utils.helpers._db") as mock_db, \
             patch("utils.helpers.get_admin_ids", return_value=[111]):
            mock_db.get_bot_file_id = AsyncMock(return_value=None)   # cache miss
            mock_db.cache_bot_file_id = AsyncMock()

            from utils.helpers import get_bot_specific_file_id
            result = await get_bot_specific_file_id(
                bot_or_context=bot,
                source_chat_id=-1001,
                source_message_id=7,
                file_type="photo",
                file_index=0,
            )

        assert result == new_file_id
        bot.forward_message.assert_awaited_once()
        mock_db.cache_bot_file_id.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_none_when_no_admin_ids(self):
        """When no admin IDs are configured, forwarding cannot happen → returns None."""
        bot = _make_bot()

        with patch("utils.helpers._db") as mock_db, \
             patch("utils.helpers.get_admin_ids", return_value=[]):
            mock_db.get_bot_file_id = AsyncMock(return_value=None)

            from utils.helpers import get_bot_specific_file_id
            result = await get_bot_specific_file_id(
                bot_or_context=bot,
                source_chat_id=-1001,
                source_message_id=7,
                file_type="photo",
                file_index=0,
            )

        assert result is None
        bot.forward_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_deletes_forwarded_message_after_caching(self):
        """After extracting the file_id, the forwarded message must be deleted."""
        bot = _make_bot()
        forwarded_msg = _make_forwarded_photo("SOME_FILE_ID")
        bot.forward_message.return_value = forwarded_msg

        with patch("utils.helpers._db") as mock_db, \
             patch("utils.helpers.get_admin_ids", return_value=[222]):
            mock_db.get_bot_file_id = AsyncMock(return_value=None)
            mock_db.cache_bot_file_id = AsyncMock()

            from utils.helpers import get_bot_specific_file_id
            await get_bot_specific_file_id(
                bot_or_context=bot,
                source_chat_id=-1001,
                source_message_id=7,
                file_type="photo",
                file_index=0,
            )

        bot.delete_message.assert_awaited_once_with(
            chat_id=222,
            message_id=forwarded_msg.message_id,
        )


# ---------------------------------------------------------------------------
# Invalid cached file_id (cache invalidation)
# ---------------------------------------------------------------------------

class TestGetBotSpecificFileIdInvalidCache:
    """
    Verify that a stale / invalid cached file_id is cleared and the file is
    re-fetched via forwarding.
    """

    @pytest.mark.asyncio
    async def test_clears_invalid_cache_and_reforwards(self):
        """When the cached file_id fails get_file(), cache is cleared and message forwarded."""
        from telegram.error import TelegramError

        bot = _make_bot()
        # First call: get_file raises (cached id expired)
        bot.get_file.side_effect = TelegramError("file not found")

        new_file_id = "NEW_FILE_ID_AFTER_INVALIDATION"
        forwarded_msg = _make_forwarded_photo(new_file_id)
        bot.forward_message.return_value = forwarded_msg

        with patch("utils.helpers._db") as mock_db, \
             patch("utils.helpers.get_admin_ids", return_value=[333]):
            mock_db.get_bot_file_id = AsyncMock(return_value="STALE_FILE_ID")
            mock_db.delete_bot_file_id_cache_entry = AsyncMock()
            mock_db.cache_bot_file_id = AsyncMock()

            from utils.helpers import get_bot_specific_file_id
            result = await get_bot_specific_file_id(
                bot_or_context=bot,
                source_chat_id=-1001,
                source_message_id=7,
                file_type="photo",
                file_index=0,
            )

        # Cache entry must be deleted
        mock_db.delete_bot_file_id_cache_entry.assert_awaited_once()
        # New file_id obtained via forwarding
        assert result == new_file_id
        bot.forward_message.assert_awaited_once()


# ---------------------------------------------------------------------------
# Database-level cache operations
# ---------------------------------------------------------------------------

class TestBotFileIdDbCache:
    """Direct tests for Database.cache_bot_file_id / get_bot_file_id."""

    @pytest.fixture
    def db(self, tmp_path):
        from database import Database
        return Database(str(tmp_path / "filecache_test.db"))

    @pytest.mark.asyncio
    async def test_store_and_retrieve(self, db):
        """Storing a file_id and then retrieving it should return the same value."""
        await db.init_db()

        await db.cache_bot_file_id(
            source_chat_id=-1001,
            source_message_id=42,
            file_index=0,
            file_type="photo",
            bot_username="testbot",
            file_id="STORED_FILE_ID",
        )

        result = await db.get_bot_file_id(-1001, 42, 0, "testbot")
        assert result == "STORED_FILE_ID"

    @pytest.mark.asyncio
    async def test_miss_returns_none(self, db):
        """Retrieving a non-existent entry should return None."""
        await db.init_db()

        result = await db.get_bot_file_id(-1001, 99, 0, "nonexistentbot")
        assert result is None

    @pytest.mark.asyncio
    async def test_different_bots_independent_cache(self, db):
        """Two bots sharing the same source message get independent cache entries."""
        await db.init_db()

        await db.cache_bot_file_id(-1001, 42, 0, "photo", "bot_alpha", "FILE_ALPHA")
        await db.cache_bot_file_id(-1001, 42, 0, "photo", "bot_beta", "FILE_BETA")

        assert await db.get_bot_file_id(-1001, 42, 0, "bot_alpha") == "FILE_ALPHA"
        assert await db.get_bot_file_id(-1001, 42, 0, "bot_beta") == "FILE_BETA"

    @pytest.mark.asyncio
    async def test_delete_entry_clears_cache(self, db):
        """Deleting a specific cache entry must make it return None."""
        await db.init_db()

        await db.cache_bot_file_id(-1001, 42, 0, "photo", "testbot", "DELETEME")
        await db.delete_bot_file_id_cache_entry(-1001, 42, 0, "testbot")

        result = await db.get_bot_file_id(-1001, 42, 0, "testbot")
        assert result is None
