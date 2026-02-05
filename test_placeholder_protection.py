#!/usr/bin/env python3
"""
Test placeholder protection during translation.
This test verifies that placeholders like {name} and {contact} survive translation.
"""
import sys
import os
import re
from typing import Tuple, Dict

# Add project root to path (portable)
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)
from translations.translator import protect_placeholders, restore_placeholders


def test_placeholder_protection():
    """Test that placeholders are protected during translation."""
    
    test_cases = [
        {
            "name": "Welcome message with name and contact",
            "input": "👋 Welcome, {name}!\n\nI'm your media product catalog bot.\n\n💬 DM {contact} TO ORDER",
            "expected_placeholders": ["{name}", "{contact}"],
        },
        {
            "name": "DM to order message",
            "input": "👆 DM TO ORDER: {contact}",
            "expected_placeholders": ["{contact}"],
        },
        {
            "name": "Category with placeholder",
            "input": "📦 All {category} products",
            "expected_placeholders": ["{category}"],
        },
        {
            "name": "Multiple same placeholders",
            "input": "Hello {name}, welcome {name}!",
            "expected_placeholders": ["{name}", "{name}"],
        },
        {
            "name": "No placeholders",
            "input": "This is a simple message without placeholders",
            "expected_placeholders": [],
        },
    ]
    
    all_passed = True
    
    for test_case in test_cases:
        print(f"\n{'='*60}")
        print(f"Test: {test_case['name']}")
        print(f"{'='*60}")
        
        input_text = test_case["input"]
        expected_placeholders = test_case["expected_placeholders"]
        
        print(f"\nInput: {input_text}")
        
        # Step 1: Protect placeholders
        protected, placeholder_map = protect_placeholders(input_text)
        print(f"\nProtected: {protected}")
        print(f"Placeholder map: {placeholder_map}")
        
        # Verify no placeholders remain in protected text
        remaining_placeholders = re.findall(r'\{[^}]+\}', protected)
        if remaining_placeholders:
            print(f"❌ FAILED: Placeholders still present in protected text: {remaining_placeholders}")
            all_passed = False
            continue
        
        # Verify all expected placeholders are in the map
        mapped_placeholders = list(placeholder_map.values())
        if len(mapped_placeholders) != len(expected_placeholders):
            print(f"❌ FAILED: Expected {len(expected_placeholders)} placeholders, got {len(mapped_placeholders)}")
            all_passed = False
            continue
        
        # Simulate translation (markers should survive)
        # In real translation, "XPLACEHOLDERX0X" would NOT be translated
        simulated_translation = protected  # Markers remain unchanged
        
        # Step 2: Restore placeholders
        restored = restore_placeholders(simulated_translation, placeholder_map)
        print(f"\nRestored: {restored}")
        
        # Verify restoration worked
        if restored != input_text:
            print(f"❌ FAILED: Restored text doesn't match original")
            print(f"   Expected: {input_text}")
            print(f"   Got: {restored}")
            all_passed = False
            continue
        
        # Verify placeholders are present in restored text
        restored_placeholders = re.findall(r'\{[^}]+\}', restored)
        if len(restored_placeholders) != len(expected_placeholders):
            print(f"❌ FAILED: Expected {len(expected_placeholders)} placeholders in restored text, got {len(restored_placeholders)}")
            all_passed = False
            continue
        
        print(f"\n✅ PASSED")
    
    print(f"\n{'='*60}")
    if all_passed:
        print("✅ All tests passed!")
        return 0
    else:
        print("❌ Some tests failed")
        return 1


def test_placeholder_formatting():
    """Test that placeholders can be formatted after restoration."""
    
    print(f"\n{'='*60}")
    print("Test: Formatting after placeholder restoration")
    print(f"{'='*60}")
    
    input_text = "Welcome, {name}! DM {contact} to order."
    
    # Protect and restore
    protected, placeholder_map = protect_placeholders(input_text)
    restored = restore_placeholders(protected, placeholder_map)
    
    # Format with actual values
    try:
        formatted = restored.format(name="John Doe", contact="@FLYAWAYPEP")
        expected = "Welcome, John Doe! DM @FLYAWAYPEP to order."
        
        print(f"\nTemplate: {restored}")
        print(f"Formatted: {formatted}")
        print(f"Expected: {expected}")
        
        if formatted == expected:
            print("\n✅ PASSED: Formatting works correctly")
            return 0
        else:
            print("\n❌ FAILED: Formatting produced unexpected result")
            return 1
    except Exception as e:
        print(f"\n❌ FAILED: Formatting raised exception: {e}")
        return 1


def test_edge_cases():
    """Test edge cases for placeholder protection."""
    
    print(f"\n{'='*60}")
    print("Test: Edge cases")
    print(f"{'='*60}")
    
    edge_cases = [
        {
            "name": "Empty string",
            "input": "",
            "should_work": True,
        },
        {
            "name": "Just a placeholder",
            "input": "{name}",
            "should_work": True,
        },
        {
            "name": "Nested braces (invalid)",
            "input": "Hello {{name}}",
            "should_work": True,  # Should handle gracefully
        },
        {
            "name": "Placeholder with spaces",
            "input": "Hello { name }",
            "should_work": True,  # Should handle as invalid placeholder
        },
        {
            "name": "Unicode in text",
            "input": "👋 Welcome, {name}! 🎉",
            "should_work": True,
        },
    ]
    
    all_passed = True
    
    for test_case in edge_cases:
        print(f"\nTest: {test_case['name']}")
        print(f"Input: {repr(test_case['input'])}")
        
        try:
            protected, placeholder_map = protect_placeholders(test_case["input"])
            restored = restore_placeholders(protected, placeholder_map)
            
            # For valid placeholders, restored should equal input
            # For edge cases, we just verify no exceptions
            print(f"✅ Handled without errors")
            
        except Exception as e:
            if test_case["should_work"]:
                print(f"❌ FAILED: Unexpected exception: {e}")
                all_passed = False
            else:
                print(f"✅ Expected failure: {e}")
    
    return 0 if all_passed else 1


if __name__ == "__main__":
    exit_code = 0
    
    exit_code |= test_placeholder_protection()
    exit_code |= test_placeholder_formatting()
    exit_code |= test_edge_cases()
    
    print(f"\n{'='*60}")
    if exit_code == 0:
        print("🎉 All tests passed successfully!")
    else:
        print("⚠️  Some tests failed. Please review the output above.")
    print(f"{'='*60}\n")
    
    sys.exit(exit_code)
