"""
Test filtering of excluded categories from All Products view.

This test verifies:
1. Products from DATEDPROOFS category are excluded from All Products
2. Products from CLIENTTOUCHDOWNS category are excluded from All Products
3. Products from other categories are included in All Products
4. Products with no category assigned are included in All Products
5. Count methods correctly reflect filtered products
6. Edge cases are handled properly
"""
import asyncio
import os
import tempfile
from database import Database
from utils.categories import EXCLUDED_FROM_ALL_PRODUCTS


async def test_all_products_exclusion():
    """Test that excluded categories are properly filtered from All Products view."""
    
    # Create temporary database for this test
    test_db_fd, test_db_path = tempfile.mkstemp(suffix='.db', prefix='test_all_products_')
    
    # Initialize test database
    db = Database(test_db_path)
    
    try:
        await db.init_db()
        
        print("=" * 60)
        print("Testing All Products Category Exclusion")
        print("=" * 60)
        
        # Test 1: Add products from various categories
        print("\n[Test 1] Adding test products from various categories...")
        
        test_products = [
            {"category": "FLOWER", "subcategory": "TOPSHELFCANDY", "caption": "Flower Product 1"},
            {"category": "FLOWER", "subcategory": "EXOTICS", "caption": "Flower Product 2"},
            {"category": "CONCENTRATES", "subcategory": "SHATTER", "caption": "Concentrate Product 1"},
            {"category": "CARTRIDGES", "subcategory": "AUTHENTICS", "caption": "Cartridge Product 1"},
            {"category": "DATEDPROOFS", "subcategory": None, "caption": "Dated Proof Product 1"},
            {"category": "DATEDPROOFS", "subcategory": None, "caption": "Dated Proof Product 2"},
            {"category": "CLIENTTOUCHDOWNS", "subcategory": None, "caption": "Client Touchdown Product 1"},
            {"category": "CLIENTTOUCHDOWNS", "subcategory": None, "caption": "Client Touchdown Product 2"},
            {"category": None, "subcategory": None, "caption": "Uncategorized Product 1"},
            {"category": None, "subcategory": None, "caption": "Uncategorized Product 2"},
            {"category": "", "subcategory": None, "caption": "Empty Category Product 1"},
        ]
        
        product_ids = []
        for i, product in enumerate(test_products):
            product_id = await db.add_product(
                file_id=f"test_file_{i}",
                file_type="photo",
                caption=product["caption"],
                message_id=1000 + i,
                chat_id=-1001234567890,
                category=product["category"],
                subcategory=product["subcategory"]
            )
            product_ids.append(product_id)
            print(f"  Added product: {product['caption']} (Category: {product['category']})")
        
        print(f"✓ Added {len(product_ids)} test products")
        
        # Test 2: Verify count of all products (without filter)
        print("\n[Test 2] Counting all products without filter...")
        total_count = await db.count_products()
        print(f"  Total products: {total_count}")
        assert total_count == 11, f"Expected 11 total products, got {total_count}"
        print("✓ Total count correct")
        
        # Test 3: Verify count with exclusion filter
        print("\n[Test 3] Counting products with exclusion filter...")
        filtered_count = await db.count_products_excluding_categories(EXCLUDED_FROM_ALL_PRODUCTS)
        print(f"  Filtered products: {filtered_count}")
        print(f"  Excluded categories: {EXCLUDED_FROM_ALL_PRODUCTS}")
        # Should exclude 2 DATEDPROOFS + 2 CLIENTTOUCHDOWNS = 4 products
        # Should include 4 regular + 2 uncategorized + 1 empty string = 7 products
        assert filtered_count == 7, f"Expected 7 filtered products, got {filtered_count}"
        print("✓ Filtered count correct")
        
        # Test 4: Verify get_all_products_excluding_categories returns correct products
        print("\n[Test 4] Fetching products with exclusion filter...")
        filtered_products = await db.get_all_products_excluding_categories(EXCLUDED_FROM_ALL_PRODUCTS)
        print(f"  Retrieved {len(filtered_products)} products")
        
        # Check that no DATEDPROOFS or CLIENTTOUCHDOWNS products are in the result
        excluded_found = []
        for product in filtered_products:
            if product['category'] in EXCLUDED_FROM_ALL_PRODUCTS:
                excluded_found.append(product)
        
        assert len(excluded_found) == 0, f"Found {len(excluded_found)} products from excluded categories: {excluded_found}"
        print("✓ No excluded category products in result")
        
        # Test 5: Verify uncategorized products are included
        print("\n[Test 5] Verifying uncategorized and empty category products are included...")
        uncategorized_count = sum(1 for p in filtered_products if p['category'] is None or p['category'] == '')
        print(f"  Uncategorized/empty products in result: {uncategorized_count}")
        assert uncategorized_count == 3, f"Expected 3 uncategorized/empty products, got {uncategorized_count}"
        print("✓ Uncategorized and empty category products included correctly")
        
        # Test 6: Verify specific categories are included
        print("\n[Test 6] Verifying specific included categories...")
        flower_count = sum(1 for p in filtered_products if p['category'] == 'FLOWER')
        concentrates_count = sum(1 for p in filtered_products if p['category'] == 'CONCENTRATES')
        cartridges_count = sum(1 for p in filtered_products if p['category'] == 'CARTRIDGES')
        
        print(f"  FLOWER products: {flower_count}")
        print(f"  CONCENTRATES products: {concentrates_count}")
        print(f"  CARTRIDGES products: {cartridges_count}")
        
        assert flower_count == 2, f"Expected 2 FLOWER products, got {flower_count}"
        assert concentrates_count == 1, f"Expected 1 CONCENTRATES product, got {concentrates_count}"
        assert cartridges_count == 1, f"Expected 1 CARTRIDGES product, got {cartridges_count}"
        print("✓ Included categories present correctly")
        
        # Test 7: Test with empty exclusion list
        print("\n[Test 7] Testing with empty exclusion list...")
        all_products = await db.get_all_products_excluding_categories([])
        print(f"  Products with empty exclusion: {len(all_products)}")
        assert len(all_products) == 11, f"Expected 11 products with empty exclusion, got {len(all_products)}"
        print("✓ Empty exclusion list handled correctly")
        
        # Test 8: Test count with empty exclusion list
        print("\n[Test 8] Testing count with empty exclusion list...")
        count_all = await db.count_products_excluding_categories([])
        print(f"  Count with empty exclusion: {count_all}")
        assert count_all == 11, f"Expected 11 products count with empty exclusion, got {count_all}"
        print("✓ Empty exclusion count handled correctly")
        
        # Test 9: Verify excluded categories still accessible directly
        print("\n[Test 9] Verifying excluded categories are still accessible when requested directly...")
        datedproofs_products = await db.get_products_by_category("DATEDPROOFS")
        clienttouchdowns_products = await db.get_products_by_category("CLIENTTOUCHDOWNS")
        
        print(f"  DATEDPROOFS products (direct access): {len(datedproofs_products)}")
        print(f"  CLIENTTOUCHDOWNS products (direct access): {len(clienttouchdowns_products)}")
        
        assert len(datedproofs_products) == 2, f"Expected 2 DATEDPROOFS products, got {len(datedproofs_products)}"
        assert len(clienttouchdowns_products) == 2, f"Expected 2 CLIENTTOUCHDOWNS products, got {len(clienttouchdowns_products)}"
        print("✓ Excluded categories still accessible directly")
        
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
        
    finally:
        # Clean up
        if os.path.exists(test_db_path):
            os.close(test_db_fd)
            os.unlink(test_db_path)
            print(f"\n🧹 Cleaned up test database: {test_db_path}")


if __name__ == "__main__":
    asyncio.run(test_all_products_exclusion())
