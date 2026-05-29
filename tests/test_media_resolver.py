from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.error import TelegramError

from utils.media_resolver import MediaResolver


def _product(**overrides):
    data = {
        "id": 10,
        "file_id": "ORIGINAL",
        "file_type": "photo",
        "chat_id": -1001,
        "message_id": 77,
        "bot_username": "originbot",
        "additional_file_ids": "[]",
        "additional_message_ids": "[]",
    }
    data.update(overrides)
    return data


def _bot(username="currentbot"):
    bot = MagicMock()
    bot.username = username
    bot.get_file = AsyncMock(return_value=MagicMock())
    return bot


@pytest.mark.asyncio
async def test_cross_bot_resolution_does_not_use_original_file_id():
    bot = _bot("currentbot")
    db = MagicMock()
    db.get_bot_file_id = AsyncMock(return_value=None)
    db.delete_bot_file_id_cache_entry = AsyncMock()

    with patch("utils.media_resolver.get_bot_specific_file_id", AsyncMock(return_value=None)):
        result = await MediaResolver(bot, db).resolve_media_for_bot(
            _product(bot_username="originbot"),
            "currentbot",
            0,
        )

    assert result.success is False
    assert result.file_id is None
    bot.get_file.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_hit_is_validated_and_returned():
    bot = _bot("currentbot")
    db = MagicMock()
    db.get_bot_file_id = AsyncMock(return_value="CACHED")
    db.delete_bot_file_id_cache_entry = AsyncMock()

    result = await MediaResolver(bot, db).resolve_media_for_bot(_product(), "currentbot", 0)

    assert result.success is True
    assert result.file_id == "CACHED"
    assert result.source == "cache"
    bot.get_file.assert_awaited_once_with("CACHED")


@pytest.mark.asyncio
async def test_invalid_cached_file_id_is_evicted():
    bot = _bot("currentbot")
    bot.get_file = AsyncMock(side_effect=TelegramError("file_id is invalid"))
    db = MagicMock()
    db.get_bot_file_id = AsyncMock(return_value="STALE")
    db.delete_bot_file_id_cache_entry = AsyncMock()

    with patch("utils.media_resolver.get_bot_specific_file_id", AsyncMock(return_value=None)):
        result = await MediaResolver(bot, db).resolve_media_for_bot(_product(), "currentbot", 0)

    assert result.success is False
    db.delete_bot_file_id_cache_entry.assert_awaited()


@pytest.mark.asyncio
async def test_same_bot_original_file_id_is_allowed_after_validation():
    bot = _bot("originbot")
    db = MagicMock()

    result = await MediaResolver(bot, db).resolve_media_for_bot(
        _product(bot_username="originbot"),
        "originbot",
        0,
    )

    assert result.success is True
    assert result.file_id == "ORIGINAL"
    assert result.source == "original"
    bot.get_file.assert_awaited_once_with("ORIGINAL")


@pytest.mark.asyncio
async def test_validated_file_id_is_cached_for_repeated_views():
    bot = _bot("repeatbot")
    db = MagicMock()

    resolver = MediaResolver(bot, db)
    product = _product(bot_username="repeatbot", file_id="REPEATED")

    first = await resolver.resolve_media_for_bot(product, "repeatbot", 0)
    second = await resolver.resolve_media_for_bot(product, "repeatbot", 0)

    assert first.success is True
    assert second.success is True
    assert bot.get_file.await_count == 1
