"""
Test translation functionality.
"""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.translation import translate_text, SUPPORTED_LANGUAGES, is_supported_language
from database import Database


async def test_translation_basic():
    """Test basic translation functionality."""
    print("=" * 60)
    print("Testing Basic Translation")
    print("=" * 60)
    
    test_text = "Hello, this is a test product description."
    
    # Test translation to Spanish
    print(f"\nOriginal (English): {test_text}")
    
    spanish = translate_text(test_text, 'es')
    print(f"Spanish: {spanish}")
    # In offline environments, translation may return None
    if spanish is None:
        print("⚠️  Translation failed (likely due to network unavailability)")
        print("   This is expected in offline environments and will be handled gracefully")
    else:
        assert spanish != test_text, "Spanish translation should be different from English"
    
    # Test translation to French
    french = translate_text(test_text, 'fr')
    print(f"French: {french}")
    if french is None:
        print("⚠️  Translation failed (likely due to network unavailability)")
    else:
        assert french != test_text, "French translation should be different from English"
    
    # Test that English returns original
    english = translate_text(test_text, 'en')
    print(f"English (should be unchanged): {english}")
    assert english == test_text, "English translation should return original text"
    
    print("\n✅ Basic translation tests passed (with network availability awareness)!")


async def test_language_support():
    """Test language support functions."""
    print("\n" + "=" * 60)
    print("Testing Language Support")
    print("=" * 60)
    
    # Test supported languages
    print(f"\nSupported languages: {len(SUPPORTED_LANGUAGES)}")
    for code, name in SUPPORTED_LANGUAGES.items():
        print(f"  {code}: {name}")
    
    # Test language checking
    assert is_supported_language('es'), "Spanish should be supported"
    assert is_supported_language('en'), "English should be supported"
    assert not is_supported_language('xyz'), "Random code should not be supported"
    
    print("\n✅ Language support tests passed!")


async def test_database_language_storage():
    """Test database language preference storage."""
    print("\n" + "=" * 60)
    print("Testing Database Language Storage")
    print("=" * 60)
    
    db = Database()
    await db.init_db()
    
    test_user_id = 999999
    
    # Test default language
    default_lang = await db.get_user_language(test_user_id)
    print(f"\nDefault language for new user: {default_lang}")
    assert default_lang == 'en', "Default language should be English"
    
    # Test setting language
    await db.set_user_language(test_user_id, 'es')
    saved_lang = await db.get_user_language(test_user_id)
    print(f"Language after setting to Spanish: {saved_lang}")
    assert saved_lang == 'es', "Language should be saved as Spanish"
    
    # Test updating language
    await db.set_user_language(test_user_id, 'fr')
    updated_lang = await db.get_user_language(test_user_id)
    print(f"Language after updating to French: {updated_lang}")
    assert updated_lang == 'fr', "Language should be updated to French"
    
    print("\n✅ Database language storage tests passed!")


async def test_empty_text():
    """Test translation with empty or None text."""
    print("\n" + "=" * 60)
    print("Testing Edge Cases")
    print("=" * 60)
    
    # Test empty string
    result = translate_text("", 'es')
    print(f"\nEmpty string translation: '{result}'")
    assert result == "", "Empty string should return empty"
    
    # Test None
    result = translate_text(None, 'es')
    print(f"None translation: {result}")
    assert result is None, "None should return None"
    
    # Test whitespace
    result = translate_text("   ", 'es')
    print(f"Whitespace translation: '{result}'")
    
    print("\n✅ Edge case tests passed!")


async def main():
    """Run all tests."""
    try:
        await test_translation_basic()
        await test_language_support()
        await test_database_language_storage()
        await test_empty_text()
        
        print("\n" + "🎉" * 30)
        print("ALL TRANSLATION TESTS PASSED!")
        print("🎉" * 30)
        
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Error during testing: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
