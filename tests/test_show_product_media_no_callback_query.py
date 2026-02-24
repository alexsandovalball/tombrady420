"""
Regression test: show_product_media must not raise AttributeError when
update.callback_query is None (e.g. when invoked via /start deeplink).
"""
import asyncio
import sys
import os
import types
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_update(*, with_callback_query: bool):
    """Build a minimal fake Update object."""
    update = types.SimpleNamespace()
    update.callback_query = None

    user = types.SimpleNamespace(id=12345)
    chat = types.SimpleNamespace(id=12345)
    message = types.SimpleNamespace(
        from_user=user,
        chat=chat,
    )

    update.effective_user = user
    update.effective_chat = chat
    update.message = message

    if with_callback_query:
        answered = []

        async def answer(text=None, show_alert=False):
            answered.append(text)

        cq = types.SimpleNamespace(
            data="product|1",
            answer=answer,
            _answered=answered,
        )
        update.callback_query = cq

    return update


def _make_context(bot_username="testbot"):
    bot = types.SimpleNamespace(
        username=bot_username,
        send_message=_noop_coro,
        send_photo=_noop_coro,
        send_video=_noop_coro,
        send_document=_noop_coro,
        send_animation=_noop_coro,
        send_media_group=_noop_coro,
    )
    return types.SimpleNamespace(bot=bot)


async def _noop_coro(*args, **kwargs):
    return None


@pytest.mark.asyncio
async def test_no_attribute_error_without_callback_query(monkeypatch):
    """show_product_media must not raise when update.callback_query is None."""
    import handlers.product_view as pv

    # Stub out db calls and helpers so the function runs end-to-end
    async def fake_get_product(product_id):
        return None  # triggers the early-return branch (product not found)

    monkeypatch.setattr(pv.db, "get_product", fake_get_product)

    update = _make_update(with_callback_query=False)
    context = _make_context()

    # Must not raise AttributeError
    await pv.show_product_media(update, context, product_id=1)


@pytest.mark.asyncio
async def test_callback_query_answered_when_present(monkeypatch):
    """When callback_query is present it should still be answered."""
    import handlers.product_view as pv

    async def fake_get_product(product_id):
        return None  # product-not-found branch

    monkeypatch.setattr(pv.db, "get_product", fake_get_product)

    update = _make_update(with_callback_query=True)
    context = _make_context()

    await pv.show_product_media(update, context, product_id=1)

    # The callback query should have been answered with the "not found" message
    assert update.callback_query._answered, "callback_query.answer() was never called"
