import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import handlers.product_view as product_view
from utils.media_resolver import MediaResolutionResult


def _callback_data(markup):
    return [
        button.callback_data
        for row in markup.inline_keyboard
        for button in row
        if getattr(button, "callback_data", None)
    ]


def test_media_fallback_keyboard_keeps_admin_actions(monkeypatch):
    monkeypatch.setattr(product_view, "is_admin", lambda user_id: True)

    keyboard = product_view._build_media_fallback_keyboard(
        product_id=927,
        bot_username="tombrady007bot",
        back_callback="menu|1",
        user_id=8589411468,
    )

    callbacks = _callback_data(keyboard)
    assert "delete|927" in callbacks
    assert "recategorize|927" in callbacks
    assert "product|927" in callbacks


@pytest.mark.asyncio
async def test_product_view_delivers_partial_album_and_keeps_admin_actions(monkeypatch):
    product = {
        "id": 927,
        "file_id": "GOOD_FILE",
        "file_type": "photo",
        "caption": "Test product",
        "chat_id": -100123,
        "message_id": 500,
        "bot_username": "originbot",
        "category": "FLOWER",
        "subcategory": "EXOTICS",
        "media_group_id": "group-927",
        "additional_file_ids": json.dumps([["BAD_FILE", "photo"]]),
        "additional_message_ids": json.dumps([501]),
    }

    fake_db = SimpleNamespace(
        get_product=AsyncMock(return_value=product),
        get_latest_pagination_state=AsyncMock(return_value=None),
        get_order_contact=AsyncMock(return_value="@orders"),
    )
    monkeypatch.setattr(product_view, "db", fake_db)
    monkeypatch.setattr(product_view, "is_admin", lambda user_id: True)
    monkeypatch.setattr(product_view, "get_user_lang_async", AsyncMock(return_value="en-US"))
    monkeypatch.setattr(product_view, "get_translated_string_async", AsyncMock(return_value="DM to order"))

    class FakeResolver:
        def __init__(self, bot, db):
            pass

        async def resolve_media_for_bot(self, product, current_bot, media_index):
            if media_index == 0:
                return MediaResolutionResult(True, "GOOD_FILE_RESOLVED", "cache")
            return MediaResolutionResult(False, None, "forward", "source message missing")

    monkeypatch.setattr(product_view, "MediaResolver", FakeResolver)

    send_media_message = AsyncMock()
    safe_send_message = AsyncMock()
    monkeypatch.setattr(product_view, "send_media_message", send_media_message)
    monkeypatch.setattr(product_view, "safe_send_message", safe_send_message)

    update = MagicMock()
    update.effective_user.id = 8589411468
    update.effective_chat.id = 12345
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()

    context = MagicMock()
    context.bot.username = "originbot"
    context.bot.send_media_group = AsyncMock()

    await product_view.show_product_media(update, context, 927)

    send_media_message.assert_awaited_once()
    context.bot.send_media_group.assert_not_awaited()

    sent_file_id = send_media_message.await_args.args[2]
    sent_caption = send_media_message.await_args.kwargs["caption"]
    assert sent_file_id == "GOOD_FILE_RESOLVED"
    assert "1 of 2 media file(s)" in sent_caption

    assert safe_send_message.await_count == 1
    keyboard = safe_send_message.await_args.kwargs["reply_markup"]
    callbacks = _callback_data(keyboard)
    assert "delete|927" in callbacks
    assert "recategorize|927" in callbacks
