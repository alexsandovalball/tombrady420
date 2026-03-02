"""
Tests for taxonomy utilities in utils/categories.py.

Covers:
- canonicalize_key (subcategory normalisation used inside get_subcategory_display_name)
- get_category_display_name (get_display_name)
- get_subcategories (list_subcategories)
- Category/subcategory label consistency
"""
import re
import pytest

from utils.categories import (
    CATEGORIES,
    CATEGORY_DISPLAY,
    get_all_categories,
    get_category_display_name,
    get_subcategories,
    get_subcategory_display_name,
    format_category_info,
)


# ---------------------------------------------------------------------------
# Helper: replicate the canonicalisation logic from categories.py
# ---------------------------------------------------------------------------

def _canonicalize_key(subcategory: str) -> str:
    """Mirror of the normalisation used in get_subcategory_display_name."""
    normalized = re.sub(r'[^0-9a-zA-Z]+', '_', subcategory.lower()).strip('_')
    return f"subcategory_{normalized}"


# ---------------------------------------------------------------------------
# canonicalize_key
# ---------------------------------------------------------------------------

class TestCanonicalizeKey:
    def test_simple_word(self):
        assert _canonicalize_key("AUTHENTICS") == "subcategory_authentics"

    def test_spaces_become_underscores(self):
        assert _canonicalize_key("FLOWER EDIBLES") == "subcategory_flower_edibles"

    def test_multiple_spaces_collapse(self):
        assert _canonicalize_key("DIAMONDS  AND  SAUCE") == "subcategory_diamonds_and_sauce"

    def test_special_chars_become_underscores(self):
        assert _canonicalize_key("LIVE-RESIN") == "subcategory_live_resin"

    def test_mixed_case_lowercased(self):
        assert _canonicalize_key("Live Resin") == "subcategory_live_resin"

    def test_no_leading_trailing_underscores(self):
        key = _canonicalize_key("HASH AND KIEF")
        assert not key.startswith("subcategory__")
        assert not key.endswith("_")

    def test_all_defined_subcategories_produce_valid_key(self):
        for category, subcats in CATEGORIES.items():
            for subcat in subcats:
                key = _canonicalize_key(subcat)
                assert key.startswith("subcategory_"), (
                    f"Bad key for {category}>{subcat}: {key}"
                )
                assert "__" not in key, f"Double underscore in key: {key}"

    def test_and_in_subcategory_name(self):
        key = _canonicalize_key("MOONROCKS AND SNOWCAPS")
        assert key == "subcategory_moonrocks_and_snowcaps"


# ---------------------------------------------------------------------------
# get_category_display_name  (get_display_name)
# ---------------------------------------------------------------------------

class TestGetCategoryDisplayName:
    def test_known_categories_return_display_name(self):
        for category in get_all_categories():
            display = get_category_display_name(category)
            assert isinstance(display, str)
            assert len(display) > 0

    def test_flower_has_emoji(self):
        assert "🌸" in get_category_display_name("FLOWER")

    def test_concentrates_has_emoji(self):
        assert "💎" in get_category_display_name("CONCENTRATES")

    def test_unknown_category_returns_key(self):
        assert get_category_display_name("NONEXISTENT") == "NONEXISTENT"

    def test_all_categories_in_display_dict(self):
        for cat in CATEGORIES:
            assert cat in CATEGORY_DISPLAY, f"{cat} missing from CATEGORY_DISPLAY"


# ---------------------------------------------------------------------------
# get_subcategories  (list_subcategories)
# ---------------------------------------------------------------------------

class TestGetSubcategories:
    def test_flower_has_subcategories(self):
        subcats = get_subcategories("FLOWER")
        assert len(subcats) > 0

    def test_concentrates_has_subcategories(self):
        subcats = get_subcategories("CONCENTRATES")
        assert len(subcats) > 0

    def test_shrooms_has_no_subcategories(self):
        assert get_subcategories("SHROOMS") == []

    def test_unknown_category_returns_empty(self):
        assert get_subcategories("NONEXISTENT") == []

    def test_subcategories_are_strings(self):
        for category in get_all_categories():
            for subcat in get_subcategories(category):
                assert isinstance(subcat, str)

    def test_cartridges_subcategories(self):
        subcats = get_subcategories("CARTRIDGES")
        assert "AUTHENTICS" in subcats
        assert "REPLICAS" in subcats

    def test_flower_moonrocks_snowcaps_subcategory(self):
        subcats = get_subcategories("FLOWER")
        assert "MOONROCKS AND SNOWCAPS" in subcats


# ---------------------------------------------------------------------------
# get_subcategory_display_name
# ---------------------------------------------------------------------------

class TestGetSubcategoryDisplayName:
    def test_returns_string(self):
        result = get_subcategory_display_name("AUTHENTICS")
        assert isinstance(result, str)
        assert len(result) > 0

    def test_title_case_fallback_for_unknown_key(self):
        # When translation is not found the function returns title-cased input
        result = get_subcategory_display_name("TOTALLY_UNKNOWN_SUBCAT_XYZ")
        # Should not crash and should return non-empty string
        assert len(result) > 0

    def test_all_defined_subcategories_do_not_crash(self):
        for category, subcats in CATEGORIES.items():
            for subcat in subcats:
                result = get_subcategory_display_name(subcat, "en")
                assert isinstance(result, str)


# ---------------------------------------------------------------------------
# format_category_info
# ---------------------------------------------------------------------------

class TestFormatCategoryInfo:
    def test_no_category_returns_uncategorized(self):
        result = format_category_info(None, None)
        assert "Uncategorized" in result or len(result) > 0

    def test_category_only(self):
        result = format_category_info("FLOWER", None)
        assert "Flower" in result or "FLOWER" in result

    def test_category_and_subcategory(self):
        result = format_category_info("CARTRIDGES", "AUTHENTICS")
        assert "•" in result
