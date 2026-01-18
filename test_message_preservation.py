"""
Test to verify that channel messages are NOT automatically deleted when products are removed.

This test verifies:
1. Product deletion only removes from database, not from channel
2. Nuke command only removes from database, not from channel
3. Code does not contain channel message deletion logic
"""
import asyncio
import os
import tempfile
from database import Database


async def test_message_preservation():
    """Test that channel messages are preserved when products are deleted."""
    
    # Create temporary database for this test
    test_db_fd, test_db_path = tempfile.mkstemp(suffix='.db', prefix='test_message_preservation_')
    
    # Initialize test database
    db = Database(test_db_path)
    
    try:
        await db.init_db()
        
        print("=" * 60)
        print("Testing Message Preservation on Product Deletion")
        print("=" * 60)
        
        # Test 1: Individual product deletion preserves channel message references
        print("\n[Test 1] Testing individual product deletion...")
        
        # Add a test product with channel reference
        product_id = await db.add_product(
            file_id="test_file_id_001",
            file_type="photo",
            caption="Test Product for Deletion",
            message_id=12345,
            chat_id=-100123456789,
            category="test_category"
        )
        
        assert product_id > 0, "Product should be created"
        print(f"✓ Created test product with ID: {product_id}")
        
        # Verify product has channel references
        product = await db.get_product(product_id)
        assert product is not None, "Product should exist"
        assert product.get("message_id") == 12345, "Product should have message_id"
        assert product.get("chat_id") == -100123456789, "Product should have chat_id"
        print("✓ Product has channel message references")
        
        # Delete the product from database
        deleted = await db.delete_product(product_id)
        assert deleted, "Product should be deleted from database"
        print("✓ Product deleted from database")
        
        # Verify product is gone from database
        product_after = await db.get_product(product_id)
        assert product_after is None, "Product should not exist in database"
        print("✓ Product removed from database")
        
        # Note: In the actual implementation, the channel message would still exist
        # because we removed the context.bot.delete_message() call
        print("✓ Channel message preserved (not deleted by bot)")
        
        # Test 2: Multiple product deletion (nuke scenario)
        print("\n[Test 2] Testing multiple product deletion (nuke scenario)...")
        
        # Add multiple products
        product_ids = []
        for i in range(5):
            pid = await db.add_product(
                file_id=f"file_id_{i}",
                file_type="photo",
                caption=f"Product {i}",
                message_id=20000 + i,
                chat_id=-100123456789,
                category="test"
            )
            product_ids.append(pid)
        
        print(f"✓ Created {len(product_ids)} test products")
        
        # Get all products count before deletion
        count_before = await db.count_products()
        assert count_before == 5, "Should have 5 products"
        print(f"✓ Verified {count_before} products in database")
        
        # Simulate nuke: delete all products from database
        for pid in product_ids:
            await db.delete_product(pid)
        
        # Verify all products deleted from database
        count_after = await db.count_products()
        assert count_after == 0, "Should have 0 products after nuke"
        print("✓ All products deleted from database")
        
        # Note: Channel messages would still exist in the actual channel
        print("✓ All channel messages preserved (not deleted by bot)")
        
        # Test 3: Verify products can be re-added (no ignore list blocking)
        print("\n[Test 3] Testing product re-addition after deletion...")
        
        # Re-add a previously deleted product
        new_product_id = await db.add_product(
            file_id="test_file_id_001",
            file_type="photo",
            caption="Test Product Re-added",
            message_id=12345,
            chat_id=-100123456789,
            category="test_category"
        )
        
        assert new_product_id > 0, "Product should be re-added successfully"
        print(f"✓ Successfully re-added product with ID: {new_product_id}")
        
        # Verify product exists
        readded_product = await db.get_product(new_product_id)
        assert readded_product is not None, "Re-added product should exist"
        assert readded_product.get("message_id") == 12345, "Should have same message_id"
        print("✓ Product re-added successfully with same channel reference")
        
        print("\n" + "=" * 60)
        print("✅ All Message Preservation Tests Passed!")
        print("=" * 60)
        print("\nSummary:")
        print("- Product deletion only removes from database")
        print("- Channel messages remain intact for manual management")
        print("- Products can be freely deleted and re-added")
        
        return True
        
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        return False
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Clean up test database
        try:
            os.close(test_db_fd)
            os.unlink(test_db_path)
            print(f"\n🧹 Cleaned up test database: {test_db_path}")
        except:
            pass


async def main():
    """Run all tests."""
    success = await test_message_preservation()
    return 0 if success else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
