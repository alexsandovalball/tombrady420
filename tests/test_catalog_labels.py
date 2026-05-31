"""
Regression tests for fast catalog/category labels.
"""

import importlib


def test_catalog_labels_do_not_call_live_translation(monkeypatch):
    import translations.translator as translator
    import utils.categories as categories

    def fail_translation(*args, **kwargs):
        raise AssertionError("catalog labels must not call live translation")

    async def fail_translation_async(*args, **kwargs):
        raise AssertionError("catalog labels must not call live async translation")

    monkeypatch.setattr(translator, "get_translated_string", fail_translation, raising=False)
    monkeypatch.setattr(translator, "get_translated_string_async", fail_translation_async, raising=False)
    categories = importlib.reload(categories)

    assert categories.get_category_display_name("FLOWER", "nl-NL") == "🌸 Bloem"
    assert categories.get_subcategory_display_name("SOLVENTLESS", "nl") == "🌿 Oplosmiddelvrij"
    assert categories.get_subcategory_display_name("TOPSHELFCANDY", "nl") == "Top Shelf Candy"
    assert categories.get_catalog_ui_text(
        "button_all_in_category",
        "nl-NL",
        category="🌸 Bloem",
    ) == "📦 Alles in 🌸 Bloem"


def test_catalog_labels_fall_back_to_english_for_unknown_languages():
    from utils.categories import get_catalog_ui_text, get_category_display_name

    assert get_category_display_name("FLOWER", "zz") == "🌸 Flower"
    assert get_catalog_ui_text("page_indicator", "zz", page=2, total_pages=5) == "Page 2/5"
