"""
Demo script showing how user names are preserved during translation.
This demonstrates the fix for the translation service issue.
"""
from translations.translator import get_translated_string


def demo_name_preservation():
    """Demonstrate that user names are preserved during translation."""
    
    print("=" * 80)
    print("DEMONSTRATION: User Name Preservation in Translation Service")
    print("=" * 80)
    print()
    
    # Test cases with different names
    test_names = [
        "Hans Schmidt",     # German name
        "José García",      # Spanish name
        "Jean Dupont",      # French name
        "Joy",              # Common word that might be translated
        "April May",        # Month names that might be translated
    ]
    
    # Test with different languages
    languages = {
        "en": "English",
        "es": "Spanish",
        "de": "German",
        "fr": "French"
    }
    
    print("Testing welcome message with various user names:\n")
    
    for name in test_names:
        print(f"User name: '{name}'")
        print("-" * 80)
        
        for lang_code, lang_name in languages.items():
            welcome = get_translated_string("welcome", lang_code, name=name)
            # Extract just the first line with the name
            first_line = welcome.split('\n')[0]
            print(f"  {lang_name:10} ({lang_code}): {first_line}")
            
            # Verify the name appears unchanged
            if name not in welcome:
                print(f"    ⚠️  WARNING: Name '{name}' not found in output!")
            else:
                print(f"    ✅ Name preserved correctly")
        
        print()
    
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print()
    print("The translation service now works as follows:")
    print("1. Get the template string with placeholders (e.g., 'Welcome, {name}!')")
    print("2. Translate the template (placeholders stay intact)")
    print("3. Insert actual user data after translation")
    print()
    print("This ensures that user names appear exactly as they do in users' profiles,")
    print("preventing confusion from translated or altered names.")
    print("=" * 80)


if __name__ == "__main__":
    demo_name_preservation()
