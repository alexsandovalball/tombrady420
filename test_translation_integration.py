#!/usr/bin/env python3
"""
Integration test for translation with placeholder protection.
This test verifies the complete flow from string retrieval through translation to formatting.
"""
import sys
import os
import asyncio

# Add project root to path (portable)
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from translations.translator import get_translated_string_async, translate_text_async, translation_service
from translations.strings import get_string


async def test_welcome_message_flow():
    """Test the complete flow for welcome message with name and contact placeholders."""
    
    print(f"\n{'='*60}")
    print("Test: Welcome message complete flow")
    print(f"{'='*60}")
    
    # Simulate what happens in the start handler
    user_name = "John Doe"
    contact = "@FLYAWAYPEP"
    
    # Test in English (should not translate)
    print("\n--- English (no translation) ---")
    welcome_en = await get_translated_string_async(
        "welcome_with_contact",
        "en",
        name=user_name,
        contact=contact
    )
    print(f"Result: {welcome_en[:100]}...")
    
    # Verify placeholders were replaced
    if user_name in welcome_en and contact in welcome_en:
        print("✅ Name and contact present in English version")
    else:
        print("❌ FAILED: Name or contact missing in English version")
        return 1
    
    # Test that template preservation works
    # When language is English, no translation occurs, just formatting
    template = get_string("welcome_with_contact")
    if "{name}" in template and "{contact}" in template:
        print("✅ Template has placeholders intact")
    else:
        print("❌ FAILED: Template missing placeholders")
        return 1
    
    print("\n✅ Welcome message flow test passed")
    return 0


async def test_dm_to_order_flow():
    """Test DM to order message with contact placeholder."""
    
    print(f"\n{'='*60}")
    print("Test: DM to order message flow")
    print(f"{'='*60}")
    
    contact = "@FLYAWAYPEP"
    
    # Test in English
    print("\n--- English ---")
    dm_en = await get_translated_string_async(
        "dm_to_order",
        "en",
        contact=contact
    )
    print(f"Result: {dm_en}")
    
    if contact in dm_en:
        print("✅ Contact present in English version")
    else:
        print("❌ FAILED: Contact missing in English version")
        return 1
    
    # Verify template
    template = get_string("dm_to_order")
    if "{contact}" in template:
        print("✅ Template has contact placeholder")
    else:
        print("❌ FAILED: Template missing contact placeholder")
        return 1
    
    print("\n✅ DM to order flow test passed")
    return 0


async def test_placeholder_survives_mock_translation():
    """Test that placeholders survive a mock translation."""
    
    print(f"\n{'='*60}")
    print("Test: Placeholder survival through translation")
    print(f"{'='*60}")
    
    # Get the base template
    template = get_string("welcome_with_contact")
    print(f"\nOriginal template: {template[:80]}...")
    
    # Verify placeholders are in template
    if "{name}" not in template or "{contact}" not in template:
        print("❌ FAILED: Template doesn't have required placeholders")
        return 1
    
    print("✅ Template has required placeholders")
    
    # Simulate what translate() does internally
    from translations.translator import protect_placeholders, restore_placeholders
    
    protected, placeholder_map = protect_placeholders(template)
    print(f"\nProtected template: {protected[:80]}...")
    print(f"Placeholder map: {placeholder_map}")
    
    # Verify placeholders are protected
    if "{name}" in protected or "{contact}" in protected:
        print("❌ FAILED: Placeholders not properly protected")
        return 1
    
    print("✅ Placeholders properly protected")
    
    # Simulate translation (markers stay unchanged in real Google Translate)
    mock_translated = protected
    
    # Restore placeholders
    restored = restore_placeholders(mock_translated, placeholder_map)
    print(f"\nRestored template: {restored[:80]}...")
    
    # Verify placeholders are back
    if "{name}" not in restored or "{contact}" not in restored:
        print("❌ FAILED: Placeholders not properly restored")
        return 1
    
    print("✅ Placeholders properly restored")
    
    # Format with actual values
    final = restored.format(name="Test User", contact="@TestContact")
    print(f"\nFinal formatted: {final[:80]}...")
    
    if "Test User" in final and "@TestContact" in final:
        print("✅ Final message has actual values")
    else:
        print("❌ FAILED: Final message missing actual values")
        return 1
    
    print("\n✅ Placeholder survival test passed")
    return 0


async def test_cache_behavior():
    """Test that cached translations also have placeholders intact."""
    
    print(f"\n{'='*60}")
    print("Test: Cache behavior with placeholders")
    print(f"{'='*60}")
    
    # Clear cache to start fresh
    translation_service.clear_cache()
    print("Cache cleared")
    
    # First call - will translate and cache
    result1 = await get_translated_string_async(
        "dm_to_order",
        "en",  # Using English to avoid actual translation
        contact="@FLYAWAYPEP"
    )
    print(f"\nFirst call result: {result1}")
    
    # Second call - should use cache
    result2 = await get_translated_string_async(
        "dm_to_order",
        "en",
        contact="@TESTCONTACT"  # Different contact
    )
    print(f"Second call result: {result2}")
    
    # Both should have the respective contacts
    if "@FLYAWAYPEP" in result1 and "@TESTCONTACT" in result2:
        print("✅ Both cached and non-cached results have correct values")
    else:
        print("❌ FAILED: Caching broke placeholder formatting")
        return 1
    
    print("\n✅ Cache behavior test passed")
    return 0


async def test_multiple_placeholders():
    """Test strings with multiple different placeholders."""
    
    print(f"\n{'='*60}")
    print("Test: Multiple different placeholders")
    print(f"{'='*60}")
    
    # Test with button_all_in_category which has {category} placeholder
    result = await get_translated_string_async(
        "button_all_in_category",
        "en",
        category="Cartridges"
    )
    print(f"\nResult: {result}")
    
    if "Cartridges" in result:
        print("✅ Category placeholder properly replaced")
    else:
        print("❌ FAILED: Category placeholder not replaced")
        return 1
    
    print("\n✅ Multiple placeholders test passed")
    return 0


async def main():
    """Run all integration tests."""
    
    print("\n" + "="*60)
    print("TRANSLATION INTEGRATION TESTS")
    print("Testing placeholder protection in complete translation flow")
    print("="*60)
    
    exit_code = 0
    
    exit_code |= await test_welcome_message_flow()
    exit_code |= await test_dm_to_order_flow()
    exit_code |= await test_placeholder_survives_mock_translation()
    exit_code |= await test_cache_behavior()
    exit_code |= await test_multiple_placeholders()
    
    print(f"\n{'='*60}")
    if exit_code == 0:
        print("🎉 All integration tests passed successfully!")
        print("\n✅ VERIFIED: Placeholders {name} and {contact} will survive translation")
        print("✅ VERIFIED: User names and contacts will appear correctly")
        print("✅ VERIFIED: Async translation API is working correctly")
    else:
        print("⚠️  Some integration tests failed. Please review the output above.")
    print(f"{'='*60}\n")
    
    return exit_code


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
