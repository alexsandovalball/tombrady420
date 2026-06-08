import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import Response
from fastapi.testclient import TestClient


def _product(**overrides):
    data = {
        "id": 927,
        "caption": "Blue Nerdz\nFresh batch available.",
        "file_id": "PHOTO",
        "file_type": "photo",
        "additional_file_ids": json.dumps([["VIDEO", "video"], ["PHOTO2", "photo"]]),
        "category": "FLOWER",
        "subcategory": "EXOTICS",
        "created_at": "2026-06-08 12:00:00",
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_short_product_url_redirects_to_canonical_slug(monkeypatch):
    import webhook_server

    monkeypatch.setattr(
        webhook_server,
        "_catalog_db",
        SimpleNamespace(get_product=AsyncMock(return_value=_product())),
    )

    response = await webhook_server.serve_product_page(927, SimpleNamespace())

    assert response.status_code == 301
    assert response.headers["location"] == "/product/927/blue-nerdz"


@pytest.mark.asyncio
async def test_wrong_product_slug_redirects(monkeypatch):
    import webhook_server

    monkeypatch.setattr(
        webhook_server,
        "_catalog_db",
        SimpleNamespace(get_product=AsyncMock(return_value=_product())),
    )

    response = await webhook_server.serve_product_page_with_slug(
        927,
        "old-title",
        SimpleNamespace(),
    )

    assert response.status_code == 301
    assert response.headers["location"] == "/product/927/blue-nerdz"


@pytest.mark.asyncio
async def test_deleted_product_url_redirects_to_remembered_subcategory(monkeypatch):
    import webhook_server

    monkeypatch.setattr(
        webhook_server,
        "_catalog_db",
        SimpleNamespace(
            get_product=AsyncMock(return_value=None),
            get_deleted_product_redirect=AsyncMock(return_value={
                "product_id": 927,
                "caption": "Blue Nerdz",
                "category": "FLOWER",
                "subcategory": "EXOTICS",
            }),
        ),
    )

    response = await webhook_server.serve_product_page_with_slug(
        927,
        "blue-nerdz",
        SimpleNamespace(),
    )

    assert response.status_code == 301
    assert response.headers["location"] == "/category/flower/exotics"


@pytest.mark.asyncio
async def test_unknown_missing_product_url_redirects_home(monkeypatch):
    import webhook_server

    monkeypatch.setattr(
        webhook_server,
        "_catalog_db",
        SimpleNamespace(
            get_product=AsyncMock(return_value=None),
            get_deleted_product_redirect=AsyncMock(return_value=None),
        ),
    )

    response = await webhook_server.serve_product_page_with_slug(
        999999,
        "missing-product",
        SimpleNamespace(),
    )

    assert response.status_code == 302
    assert response.headers["location"] == "/"


@pytest.mark.asyncio
async def test_category_url_redirects_to_lowercase_slug():
    import webhook_server

    request = SimpleNamespace(url=SimpleNamespace(path="/category/FLOWER"))
    response = await webhook_server.serve_category_page("FLOWER", request)

    assert response.status_code == 301
    assert response.headers["location"] == "/category/flower"


def test_product_primary_image_uses_public_media_url(monkeypatch):
    import webhook_server

    webhook_server._media_info_cache.clear()
    monkeypatch.setattr(webhook_server, "_STOREFRONT_DOMAIN", "https://example.com")

    assert (
        webhook_server._product_primary_image_url(
            _product(file_type="video", additional_file_ids=json.dumps([["PHOTO2", "photo"]])),
            absolute=True,
        )
        == "https://example.com/media/products/927/1"
    )


@pytest.mark.asyncio
async def test_robots_allows_public_media_and_blocks_api():
    import webhook_server

    body = (await webhook_server.serve_robots_txt()).body.decode()

    assert "Allow: /media/products/" in body
    assert "Disallow: /api/" in body
    assert "Disallow: /admin" in body


@pytest.mark.asyncio
async def test_sitemap_uses_canonical_category_subcategory_and_product_urls(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "_STOREFRONT_DOMAIN", "https://example.com")
    monkeypatch.setattr(
        webhook_server,
        "_catalog_db",
        SimpleNamespace(get_all_products_excluding_categories=AsyncMock(return_value=[_product()])),
    )

    response = await webhook_server.serve_sitemap_xml()
    body = response.body.decode()

    assert "https://example.com/category/flower" in body
    assert "https://example.com/category/flower/exotics" in body
    assert "https://example.com/product/927/blue-nerdz" in body


@pytest.mark.asyncio
async def test_public_media_route_reuses_existing_streamer(monkeypatch):
    import webhook_server

    stream = AsyncMock(return_value=Response(content=b"ok", media_type="text/plain"))
    monkeypatch.setattr(webhook_server, "stream_catalog_media", stream)

    response = await webhook_server.stream_public_product_media(SimpleNamespace(), 927, 0)

    stream.assert_awaited_once()
    assert response.body == b"ok"


def test_product_page_renders_crawlable_html(monkeypatch):
    import webhook_server

    webhook_server._media_info_cache.clear()
    fake_db = SimpleNamespace(
        get_product=AsyncMock(return_value=_product()),
        get_related_products=AsyncMock(return_value=[_product(id=928, caption="Related Item")]),
    )
    monkeypatch.setattr(webhook_server, "_catalog_db", fake_db)
    monkeypatch.setattr(webhook_server, "_STOREFRONT_DOMAIN", "https://example.com")
    monkeypatch.setattr(webhook_server.Config, "WEB_APP_URL", "https://example.com/webapp/index.html")

    client = TestClient(webhook_server.app, raise_server_exceptions=True)
    response = client.get("/product/927/blue-nerdz")

    assert response.status_code == 200
    assert '<link rel="canonical" href="https://example.com/product/927/blue-nerdz">' in response.text
    assert 'src="/media/products/927/0"' in response.text
    assert 'src="/media/products/928/0"' in response.text
    assert "Blue Nerdz" in response.text
    assert "Related Item" in response.text
    fake_db.get_related_products.assert_awaited_once_with(
        927,
        "FLOWER",
        subcategory="EXOTICS",
        limit=6,
    )


@pytest.mark.asyncio
async def test_learn_page_and_sitemap_include_approved_guides(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "_STOREFRONT_DOMAIN", "https://example.com")
    monkeypatch.setattr(
        webhook_server,
        "_catalog_db",
        SimpleNamespace(get_all_products_excluding_categories=AsyncMock(return_value=[])),
    )

    sitemap = await webhook_server.serve_sitemap_xml()
    assert "https://example.com/learn/hash-rosin" in sitemap.body.decode()

    client = TestClient(webhook_server.app, raise_server_exceptions=True)
    response = client.get("/learn/hash-rosin")
    assert response.status_code == 200
    assert "Hash Rosin" in response.text
    assert "/category/concentrates/solventless" in response.text


@pytest.mark.asyncio
async def test_webapp_search_expands_seo_terms_before_fuzzy_fallback(monkeypatch):
    import webhook_server

    product = _product(id=321, caption="Cold Cure Hash Rosin", category="CONCENTRATES", subcategory="SOLVENTLESS")

    async def fake_search(term, excluded_categories, limit=24, offset=0):
        return [product] if "hash rosin" in term else []

    fake_db = SimpleNamespace(
        get_approved_seo_query_mappings=AsyncMock(return_value=[]),
        search_products_filtered=AsyncMock(side_effect=fake_search),
        get_all_products_for_search=AsyncMock(return_value=[]),
        log_search_query=AsyncMock(),
        get_order_contact=AsyncMock(return_value="@orders"),
    )
    monkeypatch.setattr(webhook_server, "_catalog_db", fake_db)
    monkeypatch.setattr(webhook_server, "_get_validated_init_data", lambda request, allow_preview=True: ({}, None))
    monkeypatch.setattr(webhook_server, "_check_user_not_blocked", AsyncMock())

    request = SimpleNamespace(headers={}, client=SimpleNamespace(host="127.0.0.1"))
    result = await webhook_server.search_products(request, q="rosin", limit=10, offset=0)

    searched_terms = [call.args[0] for call in fake_db.search_products_filtered.await_args_list]
    assert "rosin" in searched_terms
    assert any("hash rosin" in term for term in searched_terms)
    assert result["total"] == 1
    assert result["items"][0]["id"] == 321
    fake_db.log_search_query.assert_awaited_once()
