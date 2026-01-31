"""
Test the Announcements category functionality.
"""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.categories import (
    CATEGORIES, 
    CATEGORY_DISPLAY, 
    EXCLUDED_FROM_ALL_PRODUCTS,
    NOTIFICATION_EXCLUDED_CATEGORIES,
    get_category_display_name,
    get_subcategories
)


def test_announcements_category_exists():
    """Test that ANNOUNCEMENTS category exists."""
    assert "ANNOUNCEMENTS" in CATEGORIES, "ANNOUNCEMENTS category should exist in CATEGORIES"
    print("✓ ANNOUNCEMENTS category exists in CATEGORIES")


def test_announcements_has_no_subcategories():
    """Test that ANNOUNCEMENTS has no subcategories."""
    subcategories = CATEGORIES.get("ANNOUNCEMENTS", None)
    assert subcategories is not None, "ANNOUNCEMENTS should be in CATEGORIES"
    assert subcategories == [], "ANNOUNCEMENTS should have no subcategories"
    print("✓ ANNOUNCEMENTS has no subcategories (empty list)")


def test_announcements_display_name():
    """Test that ANNOUNCEMENTS has correct display name."""
    display_name = CATEGORY_DISPLAY.get("ANNOUNCEMENTS")
    assert display_name is not None, "ANNOUNCEMENTS should have a display name"
    assert display_name == "📢 Announcements", f"Display name should be '📢 Announcements', got '{display_name}'"
    print(f"✓ ANNOUNCEMENTS display name is correct: {display_name}")


def test_announcements_excluded_from_all_products():
    """Test that ANNOUNCEMENTS is excluded from All Products view."""
    assert "ANNOUNCEMENTS" in EXCLUDED_FROM_ALL_PRODUCTS, \
        "ANNOUNCEMENTS should be in EXCLUDED_FROM_ALL_PRODUCTS list (hidden from All Products)"
    print("✓ ANNOUNCEMENTS is excluded from All Products view")


def test_get_category_display_name():
    """Test the helper function returns correct display name."""
    display_name = get_category_display_name("ANNOUNCEMENTS")
    assert display_name == "📢 Announcements", \
        f"get_category_display_name should return '📢 Announcements', got '{display_name}'"
    print("✓ get_category_display_name('ANNOUNCEMENTS') works correctly")


def test_get_subcategories():
    """Test the helper function returns empty list for ANNOUNCEMENTS."""
    subcats = get_subcategories("ANNOUNCEMENTS")
    assert subcats == [], f"get_subcategories should return empty list, got {subcats}"
    print("✓ get_subcategories('ANNOUNCEMENTS') returns empty list")


def test_all_excluded_categories_exist():
    """Test that all excluded categories actually exist in CATEGORIES."""
    for category in EXCLUDED_FROM_ALL_PRODUCTS:
        assert category in CATEGORIES, \
            f"Excluded category '{category}' should exist in CATEGORIES"
    # Should have 3 excluded categories now (DATEDPROOFS, CLIENTTOUCHDOWNS, ANNOUNCEMENTS)
    assert len(EXCLUDED_FROM_ALL_PRODUCTS) == 3, \
        f"Should have exactly 3 excluded categories, found {len(EXCLUDED_FROM_ALL_PRODUCTS)}"
    print(f"✓ All {len(EXCLUDED_FROM_ALL_PRODUCTS)} excluded categories exist in CATEGORIES")


def test_announcements_notification_behavior():
    """Test that ANNOUNCEMENTS triggers notifications but is hidden from All Products."""
    # All three should be in EXCLUDED_FROM_ALL_PRODUCTS (hidden from "All Products" view)
    assert "ANNOUNCEMENTS" in EXCLUDED_FROM_ALL_PRODUCTS
    assert "DATEDPROOFS" in EXCLUDED_FROM_ALL_PRODUCTS
    assert "CLIENTTOUCHDOWNS" in EXCLUDED_FROM_ALL_PRODUCTS
    print("✓ All three categories are excluded from 'All Products' view")
    
    # BUT, only DATEDPROOFS and CLIENTTOUCHDOWNS should be in NOTIFICATION_EXCLUDED_CATEGORIES
    assert "ANNOUNCEMENTS" not in NOTIFICATION_EXCLUDED_CATEGORIES, \
        "ANNOUNCEMENTS should NOT be in notification exclusion list (should trigger notifications)"
    assert "DATEDPROOFS" in NOTIFICATION_EXCLUDED_CATEGORIES
    assert "CLIENTTOUCHDOWNS" in NOTIFICATION_EXCLUDED_CATEGORIES
    print("✓ ANNOUNCEMENTS WILL trigger notifications (different from DATEDPROOFS/CLIENTTOUCHDOWNS)")


if __name__ == "__main__":
    print("Running Announcements category tests...\n")
    
    test_announcements_category_exists()
    test_announcements_has_no_subcategories()
    test_announcements_display_name()
    test_announcements_excluded_from_all_products()
    test_get_category_display_name()
    test_get_subcategories()
    test_all_excluded_categories_exist()
    test_announcements_notification_behavior()
    
    print("\n✅ All tests passed!")
    print("\n📌 Summary:")
    print("   - ANNOUNCEMENTS category exists and has proper display name")
    print("   - ANNOUNCEMENTS is HIDDEN from 'All Products' view")
    print("   - ANNOUNCEMENTS WILL trigger user notifications")
    print("   - This is different from DATEDPROOFS and CLIENTTOUCHDOWNS:")
    print("     • All three are hidden from 'All Products'")
    print("     • Only ANNOUNCEMENTS triggers notifications")
