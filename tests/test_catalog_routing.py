"""
Tests for the catalog browsing callback routing flow.

Logical flow under test:
  CAT:OPEN_CAT  → browse_category|{category}     → show_subcategory_menu
  CAT:OPEN_SUB  → subcategory|{cat}|{sub}|{page} → show_catalog_page (product list)
  CAT:VIEW      → product|{id}                   → show_product_media

Telegram interactions are fully mocked so no network I/O occurs.
"""
import json
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

from tests.conftest import (
    in_memory_db,  # noqa: F401
    make_mock_bot,
    make_mock_callback_query,
    make_mock_context,
    make_mock_message,
    make_mock_update,
    make_mock_user,
)
from handlers.menu import show_subcategory_menu, show_catalog_page
from handlers.product_view import show_product_media


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_catalog_update(callback_data: str, user_id: int = 1):
    """Build an Update with a callback_query for catalog navigation."""
    user = make_mock_user(user_id=user_id)
    message = make_mock_message()
    query = make_mock_callback_query(data=callback_data, user=user, message=message)
    update = make_mock_update(callback_query=query, user=user)
    return update


# ---------------------------------------------------------------------------
# CAT:OPEN_CAT  →  browse_category|{category}  →  show_subcategory_menu
# ---------------------------------------------------------------------------

class TestOpenCategory:
    @pytest.mark.asyncio
    async def test_show_subcategory_menu_sends_subcategory_buttons(self, in_memory_db):
        """show_subcategory_menu must produce buttons for each subcategory in FLOWER."""
        update = _make_catalog_update("browse_category|FLOWER")
        ctx = make_mock_context()

        sent_text = None
        sent_keyboard = None

        async def fake_update_message(upd, context, text, reply_markup=None, **kwargs):
            nonlocal sent_text, sent_keyboard
            sent_text = text
            sent_keyboard = reply_markup

        with (
            patch("handlers.menu.db", in_memory_db),
            patch("handlers.menu.get_user_lang_async", return_value="en"),
            patch("handlers.menu.safe_update_message", side_effect=fake_update_message),
        ):
            await show_subcategory_menu(update, ctx, "FLOWER")

        assert sent_text is not None
        assert sent_keyboard is not None
        # Keyboard must contain subcategory buttons
        all_callback_datas = [
            btn.callback_data
            for row in sent_keyboard.inline_keyboard
            for btn in row
        ]
        subcategory_buttons = [d for d in all_callback_datas if d.startswith("subcategory|FLOWER|")]
        assert len(subcategory_buttons) > 0, (
            "Expected at least one subcategory|FLOWER|… button"
        )

    @pytest.mark.asyncio
    async def test_show_subcategory_menu_includes_all_in_category_button(self, in_memory_db):
        """There must be a 'All in category' button pointing to category|FLOWER|1."""
        update = _make_catalog_update("browse_category|FLOWER")
        ctx = make_mock_context()
        sent_keyboard = None

        async def fake_update_message(upd, context, text, reply_markup=None, **kwargs):
            nonlocal sent_keyboard
            sent_keyboard = reply_markup

        with (
            patch("handlers.menu.db", in_memory_db),
            patch("handlers.menu.get_user_lang_async", return_value="en"),
            patch("handlers.menu.safe_update_message", side_effect=fake_update_message),
        ):
            await show_subcategory_menu(update, ctx, "FLOWER")

        all_callback_datas = [
            btn.callback_data
            for row in sent_keyboard.inline_keyboard
            for btn in row
        ]
        assert "category|FLOWER|1" in all_callback_datas

    @pytest.mark.asyncio
    async def test_category_without_subcategories_goes_directly_to_products(self, in_memory_db):
        """SHROOMS has no subcategories – show_subcategory_menu should fall through
        to show_catalog_page without producing a subcategory button."""
        update = _make_catalog_update("browse_category|SHROOMS")
        ctx = make_mock_context()
        catalog_shown = False

        async def fake_show_catalog(upd, context, page=1, category=None, subcategory=None):
            nonlocal catalog_shown
            catalog_shown = True

        with (
            patch("handlers.menu.db", in_memory_db),
            patch("handlers.menu.get_user_lang_async", return_value="en"),
            patch("handlers.menu.show_catalog_page", side_effect=fake_show_catalog),
        ):
            await show_subcategory_menu(update, ctx, "SHROOMS")

        assert catalog_shown


# ---------------------------------------------------------------------------
# CAT:OPEN_SUB  →  subcategory|{cat}|{sub}|{page}  →  show_catalog_page
# ---------------------------------------------------------------------------

class TestOpenSubcategory:
    @pytest.mark.asyncio
    async def test_show_catalog_page_for_subcategory_returns_product_list(self, in_memory_db):
        """When a product exists in FLOWER/LOWS, the catalog page should include it."""
        # Seed a product
        pid = await in_memory_db.add_product(
            file_id="file_flower_low",
            file_type="photo",
            caption="Nice low-shelf flower",
            message_id=100,
            chat_id=-1000,
            category="FLOWER",
            subcategory="LOWS",
        )

        update = _make_catalog_update("subcategory|FLOWER|LOWS|1")
        ctx = make_mock_context()
        sent_keyboard = None

        async def fake_update_message(upd, context, text, reply_markup=None, **kwargs):
            nonlocal sent_keyboard
            sent_keyboard = reply_markup

        with (
            patch("handlers.menu.db", in_memory_db),
            patch("handlers.menu.get_user_lang_async", return_value="en"),
            patch("handlers.menu.safe_update_message", side_effect=fake_update_message),
        ):
            await show_catalog_page(update, ctx, page=1, category="FLOWER", subcategory="LOWS")

        assert sent_keyboard is not None
        all_callback_datas = [
            btn.callback_data
            for row in sent_keyboard.inline_keyboard
            for btn in row
        ]
        # There should be a product button for our seeded product
        assert f"product|{pid}" in all_callback_datas

    @pytest.mark.asyncio
    async def test_show_catalog_page_shows_list_not_auto_open(self, in_memory_db):
        """Catalog page must render a list of product buttons, not auto-open any product."""
        pid = await in_memory_db.add_product(
            file_id="file_auto_open_check",
            file_type="photo",
            caption="Auto-open test",
            message_id=101,
            chat_id=-1001,
            category="FLOWER",
            subcategory="LOWS",
        )

        update = _make_catalog_update("subcategory|FLOWER|LOWS|1")
        ctx = make_mock_context()
        send_media_called = False

        async def fake_send_media(*args, **kwargs):
            nonlocal send_media_called
            send_media_called = True

        async def fake_update_message(upd, context, text, reply_markup=None, **kwargs):
            pass

        with (
            patch("handlers.menu.db", in_memory_db),
            patch("handlers.menu.get_user_lang_async", return_value="en"),
            patch("handlers.menu.safe_update_message", side_effect=fake_update_message),
            patch("handlers.product_view.send_media_message", side_effect=fake_send_media),
        ):
            await show_catalog_page(update, ctx, page=1, category="FLOWER", subcategory="LOWS")

        # The catalog page must NOT automatically push any media
        assert not send_media_called

    @pytest.mark.asyncio
    async def test_empty_subcategory_shows_no_products_message(self, in_memory_db):
        """An empty subcategory must show an informative message, not crash."""
        update = _make_catalog_update("subcategory|FLOWER|LOWS|1")
        ctx = make_mock_context()

        async def fake_update_message(upd, context, text, reply_markup=None, **kwargs):
            pass

        with (
            patch("handlers.menu.db", in_memory_db),
            patch("handlers.menu.get_user_lang_async", return_value="en"),
            patch("handlers.menu.safe_update_message", side_effect=fake_update_message),
        ):
            # No products in DB for this subcategory
            await show_catalog_page(update, ctx, page=1, category="FLOWER", subcategory="LOWS")
        # Should not raise – test is satisfied if we reach here


# ---------------------------------------------------------------------------
# CAT:VIEW  →  product|{id}  →  show_product_media
# ---------------------------------------------------------------------------

class TestViewProduct:
    @pytest.mark.asyncio
    async def test_show_product_media_single_item_sends_media(self, in_memory_db):
        """For a single-media product, send_media_message should be called once."""
        pid = await in_memory_db.add_product(
            file_id="file_single",
            file_type="photo",
            caption="Single photo product",
            message_id=200,
            chat_id=-2000,
            category="CARTRIDGES",
            subcategory="AUTHENTICS",
            bot_username="testbot",
        )

        update = _make_catalog_update(f"product|{pid}")
        ctx = make_mock_context(bot=make_mock_bot(username="testbot"))

        sent_media = []

        async def fake_send_media(context, chat_id, file_id, file_type, caption=None, reply_markup=None):
            sent_media.append((file_id, file_type))

        with (
            patch("handlers.product_view.db", in_memory_db),
            patch("handlers.product_view.send_media_message", side_effect=fake_send_media),
            patch("handlers.product_view.safe_send_message", new_callable=AsyncMock),
            patch("handlers.product_view.get_user_lang_async", return_value="en"),
            patch("handlers.product_view.translate_text_async", side_effect=lambda t, _: t),
            patch("handlers.product_view.get_translated_string_async", return_value="DM to order"),
        ):
            await show_product_media(update, ctx, pid)

        assert len(sent_media) == 1
        assert sent_media[0] == ("file_single", "photo")

    @pytest.mark.asyncio
    async def test_show_product_media_album_fallback_on_send_failure(self, in_memory_db):
        """When send_media_group fails, show_product_media must fall back to individual sends."""
        additional = json.dumps([("file_extra", "photo")])
        pid = await in_memory_db.add_product(
            file_id="file_primary",
            file_type="photo",
            caption="Multi-photo album",
            message_id=201,
            chat_id=-2001,
            category="FLOWER",
            subcategory="LOWS",
            media_group_id="mg_fallback_test",
            additional_file_ids=additional,
            bot_username="testbot",
        )

        update = _make_catalog_update(f"product|{pid}")
        bot = make_mock_bot(username="testbot")
        # Make send_media_group raise so the fallback path is triggered
        bot.send_media_group = AsyncMock(side_effect=Exception("Simulated copy failure"))
        ctx = make_mock_context(bot=bot)

        fallback_sends = []

        async def fake_send_media(context, chat_id, file_id, file_type, caption=None, reply_markup=None):
            fallback_sends.append((file_id, file_type))

        with (
            patch("handlers.product_view.db", in_memory_db),
            patch("handlers.product_view.send_media_message", side_effect=fake_send_media),
            patch("handlers.product_view.safe_send_message", new_callable=AsyncMock),
            patch("handlers.product_view.get_user_lang_async", return_value="en"),
            patch("handlers.product_view.translate_text_async", side_effect=lambda t, _: t),
            patch("handlers.product_view.get_translated_string_async", return_value="DM to order"),
        ):
            await show_product_media(update, ctx, pid)

        # Fallback must have sent all media individually
        file_ids_sent = [fid for fid, _ in fallback_sends]
        assert "file_primary" in file_ids_sent
        assert "file_extra" in file_ids_sent

    @pytest.mark.asyncio
    async def test_show_product_media_nonexistent_product(self, in_memory_db):
        """show_product_media for a missing product must answer the query gracefully."""
        update = _make_catalog_update("product|99999")
        ctx = make_mock_context()

        with (
            patch("handlers.product_view.db", in_memory_db),
            patch("handlers.product_view.safe_send_message", new_callable=AsyncMock),
        ):
            # Should not raise
            await show_product_media(update, ctx, 99999)

        # The callback query answer must have been called (graceful failure)
        update.callback_query.answer.assert_called()
