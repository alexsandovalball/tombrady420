from unittest.mock import AsyncMock, MagicMock

import pytest


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
    )
    build_article.assert_awaited_once()
