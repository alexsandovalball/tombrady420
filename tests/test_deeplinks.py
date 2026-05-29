from utils.deeplinks import build_bot_product_url, build_webapp_product_url


def test_build_bot_product_url():
    assert build_bot_product_url(976, "TomBrady007bot") == "https://t.me/TomBrady007bot?start=product_976"


def test_build_webapp_product_url_adds_product_id():
    assert (
        build_webapp_product_url("https://example.test/webapp/index.html", 976)
        == "https://example.test/webapp/index.html?product_id=976"
    )


def test_build_webapp_product_url_preserves_existing_query():
    assert (
        build_webapp_product_url("https://example.test/webapp/index.html?lang=en", 976)
        == "https://example.test/webapp/index.html?lang=en&product_id=976"
    )


def test_build_webapp_product_url_replaces_stale_product_id():
    assert (
        build_webapp_product_url("https://example.test/webapp/index.html?product_id=1&lang=en", 976)
        == "https://example.test/webapp/index.html?product_id=976&lang=en"
    )
