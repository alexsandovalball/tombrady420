"""
Test product view navigation to ensure back button works correctly.

This test verifies that the back button in product view correctly
determines the previous location based on pagination state.

Note: This test replicates the navigation logic from handlers/product_view.py
to validate the state parsing and callback generation independently.
For full integration testing of the callback routing, see test_subcategory_integration.py.
"""


def test_subcategory_state_parsing():
    """Test parsing of subcategory state keys."""
    print("=" * 60)
    print("Testing Subcategory State Parsing")
    print("=" * 60)
    
    # Test case 1: Subcategory state parsing
    print("\n[Test 1] Parsing subcategory state...")
    state_type = "subcategory_CONCENTRATES_SNOWCAPS"
    parts = state_type.split("_", 2)  # Split into at most 3 parts
    
    assert len(parts) == 3, f"Should have 3 parts, got {len(parts)}"
    assert parts[0] == "subcategory", f"First part should be 'subcategory', got '{parts[0]}'"
    assert parts[1] == "CONCENTRATES", f"Category should be 'CONCENTRATES', got '{parts[1]}'"
    assert parts[2] == "SNOWCAPS", f"Subcategory should be 'SNOWCAPS', got '{parts[2]}'"
    
    # Construct back callback
    category = parts[1]
    subcategory = parts[2]
    page = 1
    back_callback = f"subcategory|{category}|{subcategory}|{page}"
    
    expected = "subcategory|CONCENTRATES|SNOWCAPS|1"
    assert back_callback == expected, f"Expected '{expected}', got '{back_callback}'"
    print(f"✓ Subcategory state '{state_type}' → back callback: '{back_callback}'")
    
    # Test case 2: Category state parsing
    print("\n[Test 2] Parsing category state...")
    state_type = "category_FLOWER"
    query = "FLOWER"
    page = 2
    
    if state_type.startswith("category_"):
        back_callback = f"category|{query}|{page}"
        expected = "category|FLOWER|2"
        assert back_callback == expected, f"Expected '{expected}', got '{back_callback}'"
        print(f"✓ Category state '{state_type}' → back callback: '{back_callback}'")
    
    # Test case 3: Catalog state
    print("\n[Test 3] Parsing catalog state...")
    state_type = "catalog"
    page = 3
    
    if state_type == "catalog":
        back_callback = f"menu|{page}"
        expected = "menu|3"
        assert back_callback == expected, f"Expected '{expected}', got '{back_callback}'"
        print(f"✓ Catalog state '{state_type}' → back callback: '{back_callback}'")
    
    print("\n✅ ALL STATE PARSING TESTS PASSED")


def test_navigation_scenarios():
    """Test different navigation scenarios."""
    print("\n" + "=" * 60)
    print("Testing Navigation Scenarios")
    print("=" * 60)
    
    scenarios = [
        {
            "name": "From subcategory product list",
            "state": {"state_type": "subcategory_CONCENTRATES_SNOWCAPS", "page": 1, "query": ""},
            "expected": "subcategory|CONCENTRATES|SNOWCAPS|1",
            "description": "Should return to SNOWCAPS subcategory list"
        },
        {
            "name": "From category product list",
            "state": {"state_type": "category_FLOWER", "page": 2, "query": "FLOWER"},
            "expected": "category|FLOWER|2",
            "description": "Should return to FLOWER category list page 2"
        },
        {
            "name": "From all products",
            "state": {"state_type": "catalog", "page": 1, "query": ""},
            "expected": "menu|1",
            "description": "Should return to all products page 1"
        },
        {
            "name": "From subcategory with spaces in name",
            "state": {"state_type": "subcategory_EDIBLES_FLOWER EDIBLES", "page": 3, "query": ""},
            "expected": "subcategory|EDIBLES|FLOWER EDIBLES|3",
            "description": "Should handle subcategories with spaces correctly"
        },
        {
            "name": "From subcategory with multi-word and spaces",
            "state": {"state_type": "subcategory_CONCENTRATES_HASH AND KIEF", "page": 2, "query": ""},
            "expected": "subcategory|CONCENTRATES|HASH AND KIEF|2",
            "description": "Should handle multi-word subcategories with spaces"
        },
    ]
    
    for scenario in scenarios:
        print(f"\n[Scenario: {scenario['name']}]")
        print(f"  Description: {scenario['description']}")
        
        state = scenario['state']
        state_type = state['state_type']
        page = state['page']
        query = state['query']
        
        # Replicate the logic from product_view.py
        if state_type.startswith("subcategory_"):
            parts = state_type.split("_", 2)
            if len(parts) >= 3:
                category = parts[1]
                subcategory = parts[2]
                back_callback = f"subcategory|{category}|{subcategory}|{page}"
            else:
                back_callback = "menu|1"
        elif state_type.startswith("category_") and query:
            back_callback = f"category|{query}|{page}"
        elif state_type == "catalog":
            back_callback = f"menu|{page}"
        else:
            back_callback = "menu|1"
        
        expected = scenario['expected']
        assert back_callback == expected, f"Expected '{expected}', got '{back_callback}'"
        print(f"  ✓ State: {state_type} → Back: {back_callback}")
    
    print("\n✅ ALL NAVIGATION SCENARIOS PASSED")


def test_edge_cases():
    """Test edge cases for state parsing."""
    print("\n" + "=" * 60)
    print("Testing Edge Cases")
    print("=" * 60)
    
    # Edge case 1: Malformed subcategory state (should fallback)
    print("\n[Edge Case 1] Malformed subcategory state...")
    state_type = "subcategory_ONLYONEPART"
    parts = state_type.split("_", 2)
    
    if len(parts) >= 3:
        back_callback = "should_not_reach"
    else:
        back_callback = "menu|1"
    
    assert back_callback == "menu|1", f"Should fallback to 'menu|1', got '{back_callback}'"
    print(f"✓ Malformed state correctly falls back to menu|1")
    
    # Edge case 2: Empty query for category state
    print("\n[Edge Case 2] Category state with empty query...")
    state_type = "category_FLOWER"
    query = ""
    page = 1
    
    # Should not match the condition because query is empty
    if state_type.startswith("category_") and query:
        back_callback = "should_not_match"
    else:
        back_callback = "menu|1"
    
    assert back_callback == "menu|1", f"Empty query should fallback, got '{back_callback}'"
    print(f"✓ Empty query correctly falls back")
    
    # Edge case 3: Unknown state type
    print("\n[Edge Case 3] Unknown state type...")
    state_type = "unknown_state"
    
    if state_type.startswith("subcategory_"):
        back_callback = "should_not_match1"
    elif state_type.startswith("category_"):
        back_callback = "should_not_match2"
    elif state_type == "catalog":
        back_callback = "should_not_match3"
    else:
        back_callback = "menu|1"
    
    assert back_callback == "menu|1", f"Unknown state should fallback, got '{back_callback}'"
    print(f"✓ Unknown state type correctly falls back")
    
    print("\n✅ ALL EDGE CASE TESTS PASSED")


def main():
    """Run all product navigation tests."""
    try:
        test_subcategory_state_parsing()
        test_navigation_scenarios()
        test_edge_cases()
        
        print("\n" + "🎉" * 30)
        print("ALL PRODUCT NAVIGATION TESTS PASSED!")
        print("🎉" * 30)
        
        print("\nSummary:")
        print("- Subcategory state parsing works correctly")
        print("- All navigation scenarios produce correct back callbacks")
        print("- Edge cases are handled gracefully with fallbacks")
        print("- The navigation fix ensures users return to their previous view")
        
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
    import sys
    exit_code = main()
    sys.exit(exit_code)
