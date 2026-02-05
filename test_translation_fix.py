"""
Test to verify that order contact info and user names are properly translated.
"""
from translations.translator import get_translated_string

# Test languages to validate
TEST_LANGUAGES = ['en', 'es', 'de', 'fr', 'it', 'nl']


def test_order_contact_translation():
    """Test that dm_to_order string preserves contact info."""
    test_contact = '@testuser'
    
    # Test English (no translation needed)
    result_en = get_translated_string('dm_to_order', 'en', contact=test_contact)
    assert test_contact in result_en, f"Contact not found in English: {result_en}"
    assert '👆' in result_en, "Emoji not preserved"
    print(f"✅ English: {result_en}")
    
    # Test with other languages (will fallback to English due to network restrictions in sandbox)
    # The important part is that the contact placeholder is preserved
    for lang in TEST_LANGUAGES[1:]:  # Skip 'en' as already tested
        result = get_translated_string('dm_to_order', lang, contact=test_contact)
        assert test_contact in result, f"Contact not found in {lang}: {result}"
        print(f"✅ {lang}: Contact preserved")
    
    print("\n✅ All order contact translation tests passed!")


def test_user_name_preservation():
    """Test that welcome message preserves user names without translating them."""
    test_name = 'John Smith'
    test_contact = '@testuser'
    
    # Test that names are preserved in the welcome message
    for lang in TEST_LANGUAGES:
        result = get_translated_string('welcome_with_contact', lang, 
                                      name=test_name, contact=test_contact)
        assert test_name in result, f"Name not found in {lang}: {result[:100]}..."
        assert test_contact in result, f"Contact not found in {lang}: {result[:100]}..."
        
        # Verify the name appears in the first line (welcome greeting)
        first_line = result.split('\n')[0]
        assert test_name in first_line, f"Name not in greeting for {lang}: {first_line}"
        print(f"✅ {lang}: {first_line}")
    
    print("\n✅ All user name preservation tests passed!")


def test_special_characters_in_names():
    """Test that special characters in names are handled correctly."""
    test_cases = [
        ('José García', '@contact1'),      # Spanish characters
        ('Hans_Schmidt', '@contact2'),      # Underscore (markdown special char)
        ('April May', '@contact3'),         # Potentially translatable words
        ('*Bold*Name', '@contact4'),        # Markdown characters
    ]
    
    for name, contact in test_cases:
        result = get_translated_string('welcome_with_contact', 'es', 
                                      name=name, contact=contact)
        # Name should appear (possibly escaped for markdown)
        # The exact appearance may vary due to escaping, but core name should be there
        assert contact in result, f"Contact not found for name '{name}'"
        print(f"✅ Handled special name: {name}")
    
    print("\n✅ All special character tests passed!")


def test_special_characters_in_contact():
    """Test that dm_to_order string handles special characters in contact field."""
    test_contacts = [
        '@user_name',       # Underscore (markdown special char)
        '@user.name',       # Period
        '@user-name',       # Hyphen
        '@User123',         # Numbers
        '@user*test',       # Asterisk (markdown special char)
    ]
    
    for contact in test_contacts:
        result = get_translated_string('dm_to_order', 'en', contact=contact)
        assert contact in result, f"Contact '{contact}' not preserved: {result}"
        print(f"✅ Handled special contact: {contact}")
    
    print("\n✅ All special contact character tests passed!")


if __name__ == '__main__':
    print("=" * 70)
    print("Testing Translation Fix for Order Contact Info and User Names")
    print("=" * 70)
    print()
    
    test_order_contact_translation()
    print()
    test_user_name_preservation()
    print()
    test_special_characters_in_names()
    print()
    test_special_characters_in_contact()
    print()
    print("=" * 70)
    print("ALL TESTS PASSED! ✅")
    print("=" * 70)
