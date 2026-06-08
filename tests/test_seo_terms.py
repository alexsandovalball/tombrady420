import pytest

from utils.seo_terms import build_query_variants, get_learn_page, parse_import_terms


def test_build_query_variants_maps_slang_to_category_terms():
    variants = [item.lower() for item in build_query_variants("za", limit=8)]

    assert "za" in variants
    assert "flower" in variants
    assert any("exotic" in item for item in variants)


def test_parse_import_terms_accepts_google_trends_style_csv():
    csv_text = "Related queries,Rising\nhash rosin,Breakout\nlive resin,+120%\n"

    assert parse_import_terms(csv_text) == ["hash rosin", "live resin"]


def test_learn_page_lookup_is_slug_tolerant():
    page = get_learn_page("Hash Rosin")

    assert page is not None
    assert page["slug"] == "hash-rosin"
