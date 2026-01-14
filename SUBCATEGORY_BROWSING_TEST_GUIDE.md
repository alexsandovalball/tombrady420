# Subcategory Browsing Feature - Manual Test Guide

## Overview
This document describes the new subcategory browsing feature and how to manually test it.

## Feature Description

The bot now supports subcategory browsing, allowing users to:
1. View categories from the main menu
2. If a category has subcategories, view a subcategory menu
3. Browse products filtered by category and subcategory
4. Navigate back to subcategory menu or category menu as needed

## User Flow

### Flow 1: Category WITHOUT Subcategories (e.g., SHROOMS)
```
/menu
  → 📂 Product Categories
    → Click "🍄 Shrooms (5)"
      → 📋 Shrooms (Product List)
        → Shows all products in SHROOMS category
        → Back button: "🔙 Back to Categories"
```

### Flow 2: Category WITH Subcategories (e.g., FLOWER)
```
/menu
  → 📂 Product Categories
    → Click "🌸 Flower (20)"
      → 📂 Flower (Subcategory Menu)
        → "📦 All Flower (20)" - Shows all FLOWER products
        → "Topshelfcandy (8)" - Shows only TOPSHELFCANDY products
        → "Exotics (5)" - Shows only EXOTICS products
        → "Premiumexotics (7)" - Shows only PREMIUMEXOTICS products
        → ... (other subcategories)
        → "🔙 Back to Categories"
      
      → User clicks "Topshelfcandy (8)"
        → 📋 Flower • Topshelfcandy (Product List)
          → Shows only products with category=FLOWER AND subcategory=TOPSHELFCANDY
          → Back button: "🔙 Back to Subcategories"
```

## Categories with Subcategories

Based on `utils/categories.py`, the following categories have subcategories:

1. **CARTRIDGES** (🛒 Cartridges)
   - AUTHENTICS
   - REPLICAS

2. **EDIBLES** (🍫 Edibles)
   - FLOWER EDIBLES
   - SHROOM EDIBLES

3. **CONCENTRATES** (💎 Concentrates)
   - SNOWCAPS
   - MOONROCKS
   - HASH AND KIEF
   - BADDER
   - SHATTER
   - DISTILLATE
   - THCAPOWDER
   - RSO
   - ROSIN
   - SUGAR
   - OTHERS

4. **PREROLLS** (🚬 Pre-Rolls)
   - FLOWER PREROLLS
   - INFUSED FLOWER PREROLLS

5. **FLOWER** (🌸 Flower)
   - TOPSHELFCANDY
   - PREMIUMEXOTICS
   - EXOTICS
   - PREMIUMLIGHTDEPS
   - LIGHTDEPS
   - LIGHTASSIST
   - LOWS

## Categories WITHOUT Subcategories

These categories will show products directly (no subcategory menu):

1. **SHROOMS** (🍄 Shrooms)
2. **DATEDPROOFS** (📅 Dated Proofs)
3. **CLIENTTOUCHDOWNS** (✈️ Client Touchdowns)

## Callback Data Format

The following callback data formats are used:

1. **category|{category}|{page}** - Browse all products in a category
   - Example: `category|FLOWER|1`
   
2. **browse_category|{category}** - Show subcategory menu for a category
   - Example: `browse_category|FLOWER`
   
3. **subcategory|{category}|{subcategory}|{page}** - Browse products in a subcategory
   - Example: `subcategory|FLOWER|TOPSHELFCANDY|1`

## Manual Testing Steps

### Prerequisites
1. Bot is running and connected to Telegram
2. Database has products categorized with categories and subcategories
3. You have access to send commands to the bot

### Test Case 1: Category Without Subcategories
1. Send `/menu` command to bot
2. Click on "🍄 Shrooms" (or another category without subcategories)
3. **Expected**: Should show product list directly
4. **Expected**: Back button should say "🔙 Back to Categories"
5. Click back button
6. **Expected**: Should return to category menu

### Test Case 2: Category With Subcategories
1. Send `/menu` command to bot
2. Click on "🌸 Flower" (or another category with subcategories)
3. **Expected**: Should show subcategory menu with:
   - "📦 All Flower (count)" option at top
   - List of subcategories with counts
   - "🔙 Back to Categories" button at bottom
4. Click "🔙 Back to Categories"
5. **Expected**: Should return to category menu

### Test Case 3: Browse All Products in Category (via subcategory menu)
1. Send `/menu` command to bot
2. Click on "🌸 Flower"
3. Click "📦 All Flower (count)"
4. **Expected**: Should show all FLOWER products (regardless of subcategory)
5. **Expected**: Back button should say "🔙 Back to Categories"

### Test Case 4: Browse Products by Subcategory
1. Send `/menu` command to bot
2. Click on "🌸 Flower"
3. Click on "Topshelfcandy (count)"
4. **Expected**: Should show only TOPSHELFCANDY products
5. **Expected**: Title should be "📋 Flower • Topshelfcandy"
6. **Expected**: Back button should say "🔙 Back to Subcategories"
7. Click back button
8. **Expected**: Should return to subcategory menu for FLOWER

### Test Case 5: Pagination in Subcategory View
1. Navigate to a subcategory with >5 products (e.g., FLOWER > TOPSHELFCANDY)
2. **Expected**: Should show pagination buttons (← Previous, Page X/Y, Next →)
3. Click "Next →"
4. **Expected**: Should load next page of products in same subcategory
5. **Expected**: Pagination callback should maintain category and subcategory

### Test Case 6: Product Counts
1. Send `/menu` command to bot
2. **Expected**: Each category should show total count of products
3. Click a category with subcategories
4. **Expected**: Each subcategory should show count of products
5. **Expected**: "All [Category]" should show total count matching category menu

## Database Verification

You can verify the database methods work correctly by running:

```bash
python test_subcategory_browsing.py
```

This will:
- Create test products with various category/subcategory combinations
- Test counting and filtering by category and subcategory
- Test pagination with subcategory filtering
- Verify NULL subcategories are handled correctly

## Troubleshooting

### Issue: Category with subcategories shows products directly
- **Cause**: `get_subcategories()` not returning subcategories for that category
- **Solution**: Check `utils/categories.py` CATEGORIES dict

### Issue: Subcategory menu shows 0 counts for all subcategories
- **Cause**: No products have been categorized with subcategories
- **Solution**: Use admin categorization feature to add subcategories to products

### Issue: Back button from product list goes to wrong place
- **Cause**: Callback data not properly set based on subcategory presence
- **Solution**: Check `show_catalog_page` back button logic

### Issue: Pagination doesn't work in subcategory view
- **Cause**: Callback data for pagination not including subcategory
- **Solution**: Check pagination callback data format in `show_catalog_page`

## Implementation Details

### New Database Methods
1. `get_products_by_category_and_subcategory(category, subcategory, limit, offset)`
2. `count_products_by_category_and_subcategory(category, subcategory)`
3. `get_subcategories_with_counts(category)`

### New Handler Functions
1. `show_subcategory_menu(update, context, category)` - in handlers/menu.py

### Modified Functions
1. `show_category_menu()` - Now uses `browse_category` callback for categories with subcategories
2. `show_catalog_page()` - Now accepts `subcategory` parameter and handles filtering
3. `handle_catalog_pagination()` - Now accepts `subcategory` parameter

### New Callback Handlers (in main.py)
1. `browse_category|{category}` - Shows subcategory menu
2. `subcategory|{category}|{subcategory}|{page}` - Shows subcategory product list

## Success Criteria

✅ All automated tests pass (test_subcategory_browsing.py)
✅ Categories without subcategories show products directly
✅ Categories with subcategories show subcategory menu
✅ Subcategory menu shows accurate product counts
✅ Products are correctly filtered by category and subcategory
✅ Navigation flows work correctly (forward and back)
✅ Pagination works in subcategory views
✅ Back buttons navigate to the correct previous screen
