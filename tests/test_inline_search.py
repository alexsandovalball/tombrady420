from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram import InlineQueryResultArticle, InlineQueryResultCachedPhoto


def _product(**overrides):
    data = {
        "id": 10,
        "caption": "Static hash rosin",
        "category": "CONCENTRATES",
        "file_id": "",
        "file_type": "",
    }
    data.update(overrides)
    return data


def test_inline_debounce_stays_responsive():
    import handlers.inline as inline

    assert inline._DEBOUNCE_DELAY <= 0.2


@pytest.mark.asyncio
async def test_inline_search_uses_strict_non_seo_fuzzy(monkeypatch):
    import handlers.inline as inline

    product = _product()
    search = MagicMock(return_value=[product])
    build_article = AsyncMock(return_value="inline-result")

    monkeypatch.setattr(inline, "_get_cached_products", AsyncMock(return_value=[product]))
    monkeypatch.setattr(inline, "fuzzy_search_products", search)
    monkeypatch.setattr(inline, "_build_product_article", build_article)

    results = await inline._handle_query_search("statix", "caliplukbot", MagicMock())

    assert results == ["inline-result"]
    search.assert_called_once_with(
        [product],
        "statix",
        score_cutoff=75,
        limit=inline.INLINE_PAGE_SIZE,
        include_seo_terms=False,
        rank_by_score=True,
    )
    build_article.assert_awaited_once()


@pytest.mark.asyncio
async def test_media_inline_result_keeps_text_content(monkeypatch):
    import handlers.inline as inline

    product = _product(file_id="RAW_PHOTO", file_type="photo", bot_username="caliplukbot")
    context = MagicMock()
    context.bot.username = "caliplukbot"

    monkeypatch.setattr(inline, "_resolve_file_id_for_inline", AsyncMock(return_value="RESOLVED_PHOTO"))

    result = await inline._build_product_article(product, "caliplukbot", context)

    assert isinstance(result, InlineQueryResultCachedPhoto)
    assert result.title == "Static hash rosin"
    assert result.description == "Category: CONCENTRATES"
    assert result.input_message_content.message_text == "📦 *Static hash rosin*\n📂 CONCENTRATES"
    assert result.show_caption_above_media is True


@pytest.mark.asyncio
async def test_no_media_inline_result_has_fallback_thumbnail(monkeypatch):
    import handlers.inline as inline

    context = MagicMock()
    context.bot.username = "caliplukbot"
    monkeypatch.setattr(inline.Config, "STOREFRONT_DOMAIN", "https://shop.example")
    monkeypatch.setattr(inline.Config, "WEBHOOK_URL", "")

    result = await inline._build_product_article(_product(), "caliplukbot", context)

    assert isinstance(result, InlineQueryResultArticle)
    assert result.title == "Static hash rosin"
    assert result.thumbnail_url == "https://shop.example/og-image.png"
