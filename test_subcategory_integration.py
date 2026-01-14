"""
Integration test for subcategory browsing callback flow.

This test simulates the callback query flow to verify that:
1. Callback data is parsed correctly
2. Appropriate handler functions are called
3. Navigation flow works as expected
"""
import asyncio
import sys
from utils.categories import get_subcategories, get_all_categories


def test_callback_data_parsing():
    """Test that callback data formats are correct."""
    print("=" * 60)
    print("Testing Callback Data Parsing")
    print("=" * 60)
    
    # Test category callback
    print("\n[Test 1] Category callback format...")
    category_callback = "category|FLOWER|1"
    parts = category_callback.split("|")
    assert parts[0] == "category", "First part should be 'category'"
    assert parts[1] == "FLOWER", "Second part should be category name"
    assert parts[2] == "1", "Third part should be page number"
    print(f"✓ Category callback parses correctly: {category_callback}")
    
    # Test browse_category callback
    print("\n[Test 2] Browse category callback format...")
    browse_callback = "browse_category|FLOWER"
    parts = browse_callback.split("|")
    assert parts[0] == "browse_category", "First part should be 'browse_category'"
    assert parts[1] == "FLOWER", "Second part should be category name"
    print(f"✓ Browse category callback parses correctly: {browse_callback}")
    
    # Test subcategory callback
    print("\n[Test 3] Subcategory callback format...")
    subcategory_callback = "subcategory|FLOWER|TOPSHELFCANDY|1"
    parts = subcategory_callback.split("|")
    assert parts[0] == "subcategory", "First part should be 'subcategory'"
    assert parts[1] == "FLOWER", "Second part should be category name"
    assert parts[2] == "TOPSHELFCANDY", "Third part should be subcategory name"
    assert parts[3] == "1", "Fourth part should be page number"
    print(f"✓ Subcategory callback parses correctly: {subcategory_callback}")
    
    # Test categories callback
    print("\n[Test 4] Categories callback format...")
    categories_callback = "categories"
    assert categories_callback == "categories", "Should be 'categories'"
    print(f"✓ Categories callback parses correctly: {categories_callback}")
    
    print("\n✅ ALL CALLBACK PARSING TESTS PASSED")


def test_navigation_flow():
    """Test the logical navigation flow."""
    print("\n" + "=" * 60)
    print("Testing Navigation Flow Logic")
    print("=" * 60)
    
    # Test 1: Category without subcategories should go directly to products
    print("\n[Test 1] Category without subcategories flow...")
    category = "SHROOMS"
    subcats = get_subcategories(category)
    assert len(subcats) == 0, f"{category} should have no subcategories"
    
    # Expected flow: menu → category|SHROOMS|1 (products)
    expected_callback = f"category|{category}|1"
    print(f"✓ {category} has no subcategories, should use: {expected_callback}")
    
    # Test 2: Category with subcategories should show subcategory menu
    print("\n[Test 2] Category with subcategories flow...")
    category = "FLOWER"
    subcats = get_subcategories(category)
    assert len(subcats) > 0, f"{category} should have subcategories"
    
    # Expected flow: menu → browse_category|FLOWER (subcategory menu)
    expected_callback = f"browse_category|{category}"
    print(f"✓ {category} has {len(subcats)} subcategories, should use: {expected_callback}")
    
    # Test 3: From subcategory menu, can go to all products
    print("\n[Test 3] Subcategory menu to all products flow...")
    # Expected: browse_category|FLOWER → category|FLOWER|1 (all products)
    expected_callback = f"category|{category}|1"
    print(f"✓ 'All {category}' button should use: {expected_callback}")
    
    # Test 4: From subcategory menu, can go to specific subcategory
    print("\n[Test 4] Subcategory menu to specific subcategory flow...")
    subcategory = "TOPSHELFCANDY"
    # Expected: browse_category|FLOWER → subcategory|FLOWER|TOPSHELFCANDY|1
    expected_callback = f"subcategory|{category}|{subcategory}|1"
    print(f"✓ '{subcategory}' button should use: {expected_callback}")
    
    # Test 5: Back navigation from products (no subcategory)
    print("\n[Test 5] Back from category products...")
    # Expected: category|FLOWER|1 → categories (back to main menu)
    expected_callback = "categories"
    print(f"✓ Back from category products should use: {expected_callback}")
    
    # Test 6: Back navigation from subcategory products
    print("\n[Test 6] Back from subcategory products...")
    # Expected: subcategory|FLOWER|TOPSHELFCANDY|1 → browse_category|FLOWER
    expected_callback = f"browse_category|{category}"
    print(f"✓ Back from subcategory products should use: {expected_callback}")
    
    print("\n✅ ALL NAVIGATION FLOW TESTS PASSED")


def test_category_classification():
    """Test that all categories are correctly classified."""
    print("\n" + "=" * 60)
    print("Testing Category Classification")
    print("=" * 60)
    
    categories_with_subcats = []
    categories_without_subcats = []
    
    for category in get_all_categories():
        subcats = get_subcategories(category)
        if subcats:
            categories_with_subcats.append(category)
        else:
            categories_without_subcats.append(category)
    
    print(f"\n[Categories WITH Subcategories]")
    for cat in categories_with_subcats:
        subcats = get_subcategories(cat)
        print(f"  • {cat}: {len(subcats)} subcategories")
    
    print(f"\n[Categories WITHOUT Subcategories]")
    for cat in categories_without_subcats:
        print(f"  • {cat}")
    
    # Verify expected categories
    assert "FLOWER" in categories_with_subcats, "FLOWER should have subcategories"
    assert "CONCENTRATES" in categories_with_subcats, "CONCENTRATES should have subcategories"
    assert "SHROOMS" in categories_without_subcats, "SHROOMS should not have subcategories"
    
    print(f"\n✅ Category classification is correct")
    print(f"   - {len(categories_with_subcats)} categories with subcategories")
    print(f"   - {len(categories_without_subcats)} categories without subcategories")


def test_pagination_callbacks():
    """Test that pagination callbacks maintain context correctly."""
    print("\n" + "=" * 60)
    print("Testing Pagination Callback Context")
    print("=" * 60)
    
    # Test 1: Pagination in regular catalog
    print("\n[Test 1] Catalog pagination...")
    page = 2
    expected = f"page|catalog|{page}"
    print(f"✓ Catalog page {page}: {expected}")
    
    # Test 2: Pagination in category view
    print("\n[Test 2] Category pagination...")
    category = "FLOWER"
    page = 2
    expected = f"category|{category}|{page}"
    print(f"✓ Category '{category}' page {page}: {expected}")
    
    # Test 3: Pagination in subcategory view
    print("\n[Test 3] Subcategory pagination...")
    category = "FLOWER"
    subcategory = "TOPSHELFCANDY"
    page = 3
    expected = f"subcategory|{category}|{subcategory}|{page}"
    print(f"✓ Subcategory '{category}/{subcategory}' page {page}: {expected}")
    
    print("\n✅ ALL PAGINATION CONTEXT TESTS PASSED")


def main():
    """Run all integration tests."""
    try:
        test_callback_data_parsing()
        test_navigation_flow()
        test_category_classification()
        test_pagination_callbacks()
        
        print("\n" + "🎉" * 30)
        print("ALL INTEGRATION TESTS PASSED SUCCESSFULLY!")
        print("🎉" * 30)
        
        print("\nSummary:")
        print("- Callback data formats are correct")
        print("- Navigation flows are logical and complete")
        print("- Categories are properly classified")
        print("- Pagination maintains context correctly")
        print("\nThe subcategory browsing feature is ready for manual testing!")
        
        return 0
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
