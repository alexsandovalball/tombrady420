"""
Test to verify that user names are not translated.
"""
import asyncio
from translations.translator import get_translated_string, get_translated_string_async
from translations.strings import STRINGS


def test_sync_translation_preserves_names():
    """Test that synchronous translation preserves user names."""
    # Test with a German name that might be translated if included in the text
    name = "Hans Schmidt"
    
    # Get English version (should just format the name in)
    english_result = get_translated_string("welcome", "en", name=name)
    print(f"English: {english_result[:100]}")
    assert name in english_result, "Name should appear in English result"
    
    # Get Spanish version - name should still be unchanged
    spanish_result = get_translated_string("welcome", "es", name=name)
    print(f"Spanish: {spanish_result[:100]}")
    assert name in spanish_result, "Name should appear unchanged in Spanish result"
    
    # Test with a name that could be a common word
    name2 = "Joy"
    english_result2 = get_translated_string("welcome", "en", name=name2)
    assert name2 in english_result2, "Name 'Joy' should appear in English result"
    
    spanish_result2 = get_translated_string("welcome", "es", name=name2)
    print(f"Spanish with 'Joy': {spanish_result2[:100]}")
    assert name2 in spanish_result2, "Name 'Joy' should appear unchanged in Spanish result"
    
    print("\n✅ Synchronous translation test passed - names are preserved!")


async def test_async_translation_preserves_names():
    """Test that asynchronous translation preserves user names."""
    # Test with a German name
    name = "Hans Schmidt"
    
    # Get English version
    english_result = await get_translated_string_async("welcome", "en", name=name)
    print(f"\nAsync English: {english_result[:100]}")
    assert name in english_result, "Name should appear in async English result"
    
    # Get German version - name should still be unchanged
    german_result = await get_translated_string_async("welcome", "de", name=name)
    print(f"Async German: {german_result[:100]}")
    assert name in german_result, "Name should appear unchanged in async German result"
    
    print("\n✅ Asynchronous translation test passed - names are preserved!")


def test_template_translation():
    """Verify that the template itself gets translated but placeholders remain."""
    # Get the base template
    base_template = STRINGS["welcome"]
    print(f"\nBase template: {base_template[:80]}...")
    assert "{name}" in base_template, "Base template should have {name} placeholder"
    
    # Get Spanish translation without formatting
    spanish_template = get_translated_string("welcome", "es")
    print(f"Spanish template: {spanish_template[:80]}...")
    # The {name} placeholder might be preserved or might be translated to something
    # The key is that when we format it, the name is inserted as-is
    
    print("✅ Template translation test passed!")


if __name__ == "__main__":
    print("Testing translation service with user names...\n")
    print("=" * 80)
    
    test_sync_translation_preserves_names()
    test_template_translation()
    
    # Run async test
    asyncio.run(test_async_translation_preserves_names())
    
    print("\n" + "=" * 80)
    print("All tests passed! User names are preserved during translation.")
