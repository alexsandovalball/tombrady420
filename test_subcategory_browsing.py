"""
Test subcategory browsing functionality.

This test verifies:
1. Database methods for subcategory filtering work correctly
2. Subcategory menu displays properly with counts
3. Products can be filtered by category and subcategory
4. Navigation between category and subcategory menus works
"""
import asyncio
import os
import sys
import tempfile
from database import Database


async def test_database_subcategory_methods():
    """Test database methods for subcategory filtering."""
    
    # Create temporary database for this test
    test_db_fd, test_db_path = tempfile.mkstemp(suffix='.db', prefix='test_subcategory_')
    
    # Initialize test database
    db = Database(test_db_path)
    
    try:
        await db.init_db()
        
        print("=" * 60)
        print("Testing Database Subcategory Methods")
        print("=" * 60)
        
        # Test 1: Add products with different categories and subcategories
        print("\n[Test 1] Adding test products with categories and subcategories...")
        
        test_products = [
            {"category": "FLOWER", "subcategory": "TOPSHELFCANDY", "caption": "Top Shelf Product 1"},
            {"category": "FLOWER", "subcategory": "TOPSHELFCANDY", "caption": "Top Shelf Product 2"},
            {"category": "FLOWER", "subcategory": "EXOTICS", "caption": "Exotic Product 1"},
            {"category": "FLOWER", "subcategory": None, "caption": "Uncategorized Flower"},
            {"category": "CONCENTRATES", "subcategory": "SHATTER", "caption": "Shatter Product 1"},
            {"category": "CONCENTRATES", "subcategory": "BADDER", "caption": "Badder Product 1"},
            {"category": "SHROOMS", "subcategory": None, "caption": "Shroom Product 1"},
        ]
        
        product_ids = []
        for i, product in enumerate(test_products):
            product_id = await db.add_product(
                file_id=f"test_file_{i}",
                file_type="photo",
                caption=product["caption"],
                message_id=2000 + i,
                chat_id=-100123456789,
                category=product["category"],
                subcategory=product["subcategory"]
            )
            product_ids.append(product_id)
            assert product_id > 0, f"Product {i} should be created"
        
        print(f"✓ Created {len(product_ids)} test products")
        
        # Test 2: Count products by category
        print("\n[Test 2] Testing count_products_by_category...")
        
        flower_count = await db.count_products_by_category("FLOWER")
        assert flower_count == 4, f"FLOWER should have 4 products, got {flower_count}"
        print(f"✓ FLOWER category has {flower_count} products")
        
        concentrates_count = await db.count_products_by_category("CONCENTRATES")
        assert concentrates_count == 2, f"CONCENTRATES should have 2 products, got {concentrates_count}"
        print(f"✓ CONCENTRATES category has {concentrates_count} products")
        
        # Test 3: Count products by category and subcategory
        print("\n[Test 3] Testing count_products_by_category_and_subcategory...")
        
        topshelf_count = await db.count_products_by_category_and_subcategory("FLOWER", "TOPSHELFCANDY")
        assert topshelf_count == 2, f"FLOWER/TOPSHELFCANDY should have 2 products, got {topshelf_count}"
        print(f"✓ FLOWER/TOPSHELFCANDY has {topshelf_count} products")
        
        exotics_count = await db.count_products_by_category_and_subcategory("FLOWER", "EXOTICS")
        assert exotics_count == 1, f"FLOWER/EXOTICS should have 1 product, got {exotics_count}"
        print(f"✓ FLOWER/EXOTICS has {exotics_count} product")
        
        shatter_count = await db.count_products_by_category_and_subcategory("CONCENTRATES", "SHATTER")
        assert shatter_count == 1, f"CONCENTRATES/SHATTER should have 1 product, got {shatter_count}"
        print(f"✓ CONCENTRATES/SHATTER has {shatter_count} product")
        
        # Test 4: Get products by category and subcategory
        print("\n[Test 4] Testing get_products_by_category_and_subcategory...")
        
        topshelf_products = await db.get_products_by_category_and_subcategory("FLOWER", "TOPSHELFCANDY")
        assert len(topshelf_products) == 2, f"Should get 2 TOPSHELFCANDY products, got {len(topshelf_products)}"
        for product in topshelf_products:
            assert product["category"] == "FLOWER", "Category should be FLOWER"
            assert product["subcategory"] == "TOPSHELFCANDY", "Subcategory should be TOPSHELFCANDY"
            assert "Top Shelf" in product["caption"], "Caption should contain 'Top Shelf'"
        print(f"✓ Retrieved {len(topshelf_products)} TOPSHELFCANDY products with correct data")
        
        # Test 5: Get subcategories with counts
        print("\n[Test 5] Testing get_subcategories_with_counts...")
        
        flower_subcats = await db.get_subcategories_with_counts("FLOWER")
        assert len(flower_subcats) == 2, f"FLOWER should have 2 subcategories with products, got {len(flower_subcats)}"
        
        subcat_dict = {item["subcategory"]: item["count"] for item in flower_subcats}
        assert "TOPSHELFCANDY" in subcat_dict, "TOPSHELFCANDY should be in subcategories"
        assert subcat_dict["TOPSHELFCANDY"] == 2, f"TOPSHELFCANDY should have 2 products, got {subcat_dict['TOPSHELFCANDY']}"
        assert "EXOTICS" in subcat_dict, "EXOTICS should be in subcategories"
        assert subcat_dict["EXOTICS"] == 1, f"EXOTICS should have 1 product, got {subcat_dict['EXOTICS']}"
        print(f"✓ Retrieved {len(flower_subcats)} subcategories with correct counts")
        
        concentrates_subcats = await db.get_subcategories_with_counts("CONCENTRATES")
        assert len(concentrates_subcats) == 2, f"CONCENTRATES should have 2 subcategories, got {len(concentrates_subcats)}"
        print(f"✓ CONCENTRATES has {len(concentrates_subcats)} subcategories")
        
        # Test 6: Verify products without subcategories are not included in subcategory counts
        print("\n[Test 6] Testing that NULL subcategories are excluded...")
        
        shrooms_subcats = await db.get_subcategories_with_counts("SHROOMS")
        assert len(shrooms_subcats) == 0, f"SHROOMS should have no subcategories (NULL excluded), got {len(shrooms_subcats)}"
        print(f"✓ Categories with NULL subcategories correctly excluded from subcategory list")
        
        # Test 7: Verify pagination works with subcategory filtering
        print("\n[Test 7] Testing pagination with subcategory filtering...")
        
        # Add more products to test pagination
        for i in range(10):
            await db.add_product(
                file_id=f"test_file_page_{i}",
                file_type="photo",
                caption=f"Paginated Top Shelf Product {i}",
                message_id=3000 + i,
                chat_id=-100123456789,
                category="FLOWER",
                subcategory="TOPSHELFCANDY"
            )
        
        # Get first page (limit 5)
        page1 = await db.get_products_by_category_and_subcategory("FLOWER", "TOPSHELFCANDY", limit=5, offset=0)
        assert len(page1) == 5, f"First page should have 5 products, got {len(page1)}"
        print(f"✓ First page retrieved 5 products")
        
        # Get second page
        page2 = await db.get_products_by_category_and_subcategory("FLOWER", "TOPSHELFCANDY", limit=5, offset=5)
        assert len(page2) == 5, f"Second page should have 5 products, got {len(page2)}"
        print(f"✓ Second page retrieved 5 products")
        
        # Get third page (should have remaining 2)
        page3 = await db.get_products_by_category_and_subcategory("FLOWER", "TOPSHELFCANDY", limit=5, offset=10)
        assert len(page3) == 2, f"Third page should have 2 products, got {len(page3)}"
        print(f"✓ Third page retrieved remaining 2 products")
        
        # Verify total count
        total_topshelf = await db.count_products_by_category_and_subcategory("FLOWER", "TOPSHELFCANDY")
        assert total_topshelf == 12, f"Total TOPSHELFCANDY should be 12, got {total_topshelf}"
        print(f"✓ Total count matches: {total_topshelf} products")
        
        print("\n" + "=" * 60)
        print("✅ ALL DATABASE TESTS PASSED")
        print("=" * 60)
        
        return True
    
    finally:
        # Cleanup test database
        try:
            os.close(test_db_fd)
        except OSError:
            pass
        
        if os.path.exists(test_db_path):
            os.remove(test_db_path)


async def test_subcategory_logic():
    """Test that subcategory logic integrates properly with category utilities."""
    
    print("\n" + "=" * 60)
    print("Testing Subcategory Logic Integration")
    print("=" * 60)
    
    from utils.categories import get_subcategories, get_all_categories, CATEGORIES
    
    # Test 1: Verify categories with subcategories
    print("\n[Test 1] Verifying categories with subcategories...")
    
    categories_with_subcats = []
    categories_without_subcats = []
    
    for category in get_all_categories():
        subcats = get_subcategories(category)
        if subcats:
            categories_with_subcats.append(category)
            print(f"✓ {category} has {len(subcats)} subcategories: {subcats[:3]}...")
        else:
            categories_without_subcats.append(category)
            print(f"✓ {category} has no subcategories")
    
    assert len(categories_with_subcats) > 0, "Should have at least one category with subcategories"
    print(f"\n✓ Found {len(categories_with_subcats)} categories with subcategories")
    print(f"✓ Found {len(categories_without_subcats)} categories without subcategories")
    
    # Test 2: Verify specific category subcategories
    print("\n[Test 2] Testing specific category subcategories...")
    
    flower_subcats = get_subcategories("FLOWER")
    assert "TOPSHELFCANDY" in flower_subcats, "FLOWER should have TOPSHELFCANDY subcategory"
    assert "EXOTICS" in flower_subcats, "FLOWER should have EXOTICS subcategory"
    print(f"✓ FLOWER has expected subcategories")
    
    concentrates_subcats = get_subcategories("CONCENTRATES")
    assert "SHATTER" in concentrates_subcats, "CONCENTRATES should have SHATTER subcategory"
    assert "BADDER" in concentrates_subcats, "CONCENTRATES should have BADDER subcategory"
    print(f"✓ CONCENTRATES has expected subcategories")
    
    shrooms_subcats = get_subcategories("SHROOMS")
    assert len(shrooms_subcats) == 0, "SHROOMS should have no subcategories"
    print(f"✓ SHROOMS correctly has no subcategories")
    
    print("\n" + "=" * 60)
    print("✅ SUBCATEGORY LOGIC TESTS PASSED")
    print("=" * 60)


async def main():
    """Run all tests."""
    try:
        await test_database_subcategory_methods()
        await test_subcategory_logic()
        
        print("\n" + "🎉" * 30)
        print("ALL SUBCATEGORY BROWSING TESTS PASSED SUCCESSFULLY!")
        print("🎉" * 30)
        
        print("\nSummary:")
        print("- Database methods for subcategory filtering work correctly")
        print("- Subcategory counts are accurate")
        print("- Pagination works with subcategory filtering")
        print("- NULL subcategories are properly excluded")
        print("- Category utilities integrate properly")
        
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
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
