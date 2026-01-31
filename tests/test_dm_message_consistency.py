"""
Test DM message consistency in product view handler.
"""
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_dm_message_present_in_code():
    """Verify that DM message is present in the product view handler."""
    with open('handlers/product_view.py', 'r') as f:
        content = f.read()
    
    # Check that the DM message appears in the file
    dm_message = "DM TO ORDER:@FLYAWAYPEP"
    assert dm_message in content, f"DM message '{dm_message}' should be in product_view.py"
    print(f"✓ DM message '{dm_message}' found in product_view.py")
    
    # Count occurrences - should appear at least once for media groups and once for single media
    count = content.count(dm_message)
    assert count >= 2, f"DM message should appear at least 2 times (media groups + single media), found {count}"
    print(f"✓ DM message appears {count} times in the code (good for consistency)")


def test_send_message_with_dm_text():
    """Verify send_message calls include the DM text."""
    with open('handlers/product_view.py', 'r') as f:
        lines = f.readlines()
    
    # Find all send_message calls with DM text
    dm_send_calls = []
    for i, line in enumerate(lines, 1):
        if "send_message" in line:
            # Look for DM message in nearby lines (within 5 lines)
            context = ''.join(lines[max(0, i-5):min(len(lines), i+5)])
            if "DM TO ORDER:@FLYAWAYPEP" in context:
                dm_send_calls.append(i)
    
    assert len(dm_send_calls) >= 2, \
        f"Should have at least 2 send_message calls with DM text, found {len(dm_send_calls)}"
    print(f"✓ Found {len(dm_send_calls)} send_message calls with DM text")


def test_keyboard_sent_with_dm_message():
    """Verify that keyboard is sent together with DM message."""
    with open('handlers/product_view.py', 'r') as f:
        content = f.read()
    
    # Both DM message blocks should have keyboard
    # Looking for lines with DM message
    lines = content.split('\n')
    dm_with_keyboard_count = 0
    
    for i, line in enumerate(lines):
        if "DM TO ORDER:@FLYAWAYPEP" in line:
            # Check if reply_markup=keyboard appears within next 5 lines
            context_after = '\n'.join(lines[i:min(i+5, len(lines))])
            if "reply_markup=keyboard" in context_after:
                dm_with_keyboard_count += 1
    
    # We expect 2 occurrences (one for media groups, one for single media)
    assert dm_with_keyboard_count == 2, \
        f"Expected 2 DM messages with keyboard, found {dm_with_keyboard_count}"
    print(f"✓ Found {dm_with_keyboard_count} DM message blocks with keyboard")


def test_single_media_gets_dm_message():
    """Verify that single media products get the DM message (not just media groups)."""
    with open('handlers/product_view.py', 'r') as f:
        content = f.read()
    
    # Look for the section after send_media_message that should have the DM message
    # This is the fix we implemented - single media should now get DM message too
    
    # Find send_media_message call
    assert "send_media_message" in content, "send_media_message should be called"
    
    # After send_media_message, there should be a send_message with DM text
    # Split content at send_media_message
    parts = content.split("send_media_message")
    
    # Check that at least one part after send_media_message contains the DM message
    has_dm_after_single = False
    for part in parts[1:]:  # Skip first part (before first send_media_message)
        # Look within reasonable distance (e.g., 500 characters)
        if "DM TO ORDER:@FLYAWAYPEP" in part[:500]:
            has_dm_after_single = True
            break
    
    assert has_dm_after_single, \
        "DM message should appear after send_media_message for single media products"
    print("✓ Single media products now get DM message (fix implemented correctly)")


if __name__ == "__main__":
    print("Running DM message consistency tests...\n")
    
    test_dm_message_present_in_code()
    test_send_message_with_dm_text()
    test_keyboard_sent_with_dm_message()
    test_single_media_gets_dm_message()
    
    print("\n✅ All DM message tests passed!")
