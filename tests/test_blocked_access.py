from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_update(user_id: int):
    user = MagicMock()
    user.id = user_id
    user.username = "blocked_user"

    message = MagicMock()
    message.reply_text = AsyncMock()

    update = MagicMock()
    update.effective_user = user
    update.message = message
    return update


@pytest.mark.asyncio
async def test_menu_command_rejects_blocked_user():
    import handlers.menu as menu

    update = _make_update(1410243747)
    context = MagicMock()

    with (
        patch("handlers.menu.is_admin", return_value=False),
        patch.object(menu.db, "is_user_blocked", new=AsyncMock(return_value=True)),
        patch("handlers.menu.show_category_menu", new=AsyncMock()) as show_category_menu,
    ):
        await menu.menu_command(update, context)

    update.message.reply_text.assert_awaited_once()
    assert "blocked" in update.message.reply_text.call_args.args[0].lower()
    show_category_menu.assert_not_awaited()


@pytest.mark.asyncio
async def test_language_command_rejects_blocked_user():
    import handlers.language as language

    update = _make_update(1410243747)
    context = MagicMock()

    with (
        patch("handlers.language.is_admin", return_value=False),
        patch.object(language.db, "is_user_blocked", new=AsyncMock(return_value=True)),
        patch("handlers.language.get_user_lang_async", new=AsyncMock()) as get_user_lang,
    ):
        await language.language_command(update, context)

    update.message.reply_text.assert_awaited_once()
    assert "blocked" in update.message.reply_text.call_args.args[0].lower()
    get_user_lang.assert_not_awaited()


@pytest.mark.asyncio
async def test_inline_query_rejects_blocked_user():
    import handlers.inline as inline

    inline_query = MagicMock()
    inline_query.from_user.id = 1410243747
    inline_query.answer = AsyncMock()

    update = MagicMock()
    update.inline_query = inline_query
    context = MagicMock()

    with patch.object(inline.db, "is_user_blocked", new=AsyncMock(return_value=True)):
        await inline.handle_inline_query(update, context)

    inline_query.answer.assert_awaited_once()
    assert inline_query.answer.call_args.kwargs["results"] == []
    assert inline_query.answer.call_args.kwargs["is_personal"] is True


@pytest.mark.asyncio
async def test_chosen_inline_result_ignores_blocked_user():
    import handlers.inline as inline

    chosen = MagicMock()
    chosen.from_user.id = 1410243747
    chosen.result_id = "product_802"

    update = MagicMock()
    update.chosen_inline_result = chosen
    context = MagicMock()

    with (
        patch.object(inline.db, "is_user_blocked", new=AsyncMock(return_value=True)),
        patch.object(inline.db, "log_chosen_inline_result", new=AsyncMock()) as log_chosen,
    ):
        await inline.handle_chosen_inline_result(update, context)

    log_chosen.assert_not_awaited()
