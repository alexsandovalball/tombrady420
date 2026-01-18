# Recategorize Button Feature

## Overview
This feature adds a "Recategorize" button to the product view for admin users, allowing them to change a product's category after it has been initially categorized.

## User Flow

### 1. Admin Views Product
When an admin views a product, they see two buttons:
- 🔄 **Recategorize** (new)
- 🗑 **Delete this product** (existing)

### 2. Admin Clicks Recategorize
The callback data sent is: `recategorize|{product_id}`

### 3. Category Selection Menu Appears
The bot displays the category selection menu with all available categories:
```
📂 Recategorize Product #123

Please select a new category:

🛒 Cartridges
🍫 Edibles
💎 Concentrates
🚬 Pre-Rolls
🍄 Shrooms
🌸 Flower
📅 Dated Proofs
✈️ Client Touchdowns
```

### 4. Admin Selects Category
After selecting a category (e.g., "Cartridges"), the bot checks if the category has subcategories.

### 5. Subcategory Selection (if applicable)
If the category has subcategories, the bot shows them:
```
📂 Category: 🛒 Cartridges

Select a subcategory or save without one:

Authentics
Replicas
✅ Save without subcategory
```

### 6. Confirmation
After selecting a subcategory (or choosing to save without one), a confirmation dialog appears:
```
📂 Category: 🛒 Cartridges
📁 Subcategory: Authentics

Confirm categorization?

[✅ Confirm] [🔙 Back]
```

### 7. Success Message
After confirmation, the product is recategorized:
```
✅ Product #123 categorized successfully!

📂 🛒 Cartridges • Authentics
```

## Implementation Details

### Files Modified

#### 1. `handlers/product_view.py`
Added the recategorize button to the admin button row (alongside the delete button).

**Location:** Lines 74-85
```python
# Add delete and recategorize buttons for admins
if is_admin(user_id):
    keyboard_buttons.append([
        InlineKeyboardButton(
            "🔄 Recategorize",
            callback_data=f"recategorize|{product_id}"
        ),
        InlineKeyboardButton(
            "🗑 Delete this product",
            callback_data=f"delete|{product_id}"
        )
    ])
```

#### 2. `main.py`
Added callback handler for the recategorize action.

**Location:** Lines 557-581
- Checks if user is admin
- Shows category selection menu (same as initial categorization)
- Reuses existing `setcat`, `setsubcat`, and `savecat` handlers for the rest of the flow

**Import Addition:** Line 33
- Added `is_admin` to top-level imports for better performance

#### 3. `test_recategorize.py` (new file)
Comprehensive tests covering:
- Button availability in product view
- Callback handler functionality
- Category update integration

## Security Considerations

✅ **Admin-only feature**: Only users with admin privileges can access the recategorize button
✅ **Callback validation**: The callback handler verifies admin status before showing category menu
✅ **No security vulnerabilities**: CodeQL scan found 0 alerts

## Testing

All tests pass successfully:
- ✅ Unit tests for button integration
- ✅ Callback handler tests
- ✅ Integration tests for category updates
- ✅ Manual verification of complete flow

## Compatibility

This feature integrates seamlessly with the existing categorization system:
- Uses the same category selection flow as initial categorization
- Reuses existing callback handlers (`setcat`, `setsubcat`, `savecat`)
- No breaking changes to existing functionality
- Minimal code additions (surgical implementation)

## Benefits

1. **Flexibility**: Admins can fix categorization mistakes without deleting and re-adding products
2. **Efficiency**: Simple one-click access from product view
3. **Consistency**: Uses the same categorization UI as initial categorization
4. **User-friendly**: Clear labels and familiar workflow
