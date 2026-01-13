# Admin Guide - Telegram Catalog Bot

## Overview
This guide explains how to use the admin features of the bot.

## Admin Commands

### `/start`
Welcome message - available to all users

### `/menu`
Browse products by category - available to all users
- Shows category selection menu
- Each category displays product count
- Click category to view filtered products

### `/stats`
View catalog statistics (admin only)
- Total products
- Products added today

### `/users`
View all bot users (admin only)
- Shows up to 50 users
- Displays: username, full name, ID, interaction count, last seen
- Useful for monitoring bot engagement

### `/nuke`
Delete ALL products (admin only)
- **Double confirmation required!**
- First confirmation: "Are you sure?"
- Second confirmation: "Are you ABSOLUTELY SURE?"
- Adds deleted products to ignore list to prevent re-addition
- Use with extreme caution!

### `/recategorize`
Send categorization notifications for uncategorized products (admin only)
- Scans for products without categories
- Sends up to 10 categorization requests
- Run multiple times to process all uncategorized products

## New Product Workflow

When a new product is posted to the monitored channel:

1. **Bot detects the post**
   - Single media OR media album (multiple photos/videos)
   - Caption is extracted

2. **Product is saved to database**
   - Status: UNCATEGORIZED

3. **Admin receives notification**
   - Message with product caption preview
   - Product ID displayed
   - Category selection buttons shown

4. **Admin selects category**
   - Click on category button (e.g., 🛒 Cartridges)

5. **If category has subcategories**
   - Subcategory selection menu appears
   - Choose appropriate subcategory OR
   - Select "Save without subcategory"

6. **Product is categorized**
   - Confirmation message shown
   - Product now appears in category filter
   - Users can browse by category

## Categories & Subcategories

### 🛒 Cartridges
- AUTHENTICS
- REPLICAS

### 🍫 Edibles
- FLOWER EDIBLES
- SHROOM EDIBLES

### 💎 Concentrates
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

### 🚬 Pre-Rolls
- FLOWER PREROLLS
- INFUSED FLOWER PREROLLS

### 🍄 Shrooms
_(No subcategories)_

### 🌸 Flower
- TOPSHELFCANDY
- PREMIUMEXOTICS
- EXOTICS
- PREMIUMLIGHTDEPS
- LIGHTDEPS
- LIGHTASSIST
- LOWS

### 📅 Dated Proofs
_(No subcategories)_

### ✈️ Client Touchdowns
_(No subcategories)_

## Tips for Admins

### Categorizing Products
- **Respond quickly** - Users expect products to be browsable soon after posting
- **Be consistent** - Use the same subcategory for similar products
- **Don't skip** - All products should be categorized for best user experience

### Media Albums
- Albums (multiple photos/videos) are stored as ONE product
- The caption from the first message is used
- All photos/videos are shown when users view the product

### Managing Users
- Check `/users` periodically to see engagement
- High interaction count = active user
- Last seen shows recent activity

### Safety
- **Never use `/nuke` unless absolutely necessary**
- The double confirmation exists for a reason
- Consider backing up catalog.db before nuking
- Deleted products are added to ignore list (won't be re-added if still in channel)

### Uncategorized Products
- Use `/recategorize` to find products that weren't categorized
- Check regularly to ensure complete catalog
- Process in batches of 10 to avoid notification spam

## Troubleshooting

### Product not showing in category
- Verify product was categorized (check notification history)
- Try `/recategorize` to send new categorization request
- Check if product was accidentally deleted

### Too many notifications
- This is normal when backlog exists
- Use `/recategorize` to control flow (10 at a time)
- Categorize promptly to avoid backlog

### Users not tracked
- User tracking is automatic
- Users must send a message or use /start
- Just browsing catalog via buttons doesn't trigger tracking

### Media album showing as single photo
- Check if all photos arrived at same time
- Bot waits 1.5 seconds to collect album
- If photos sent separately, they're separate products

## Environment Setup

Admin IDs are configured in `.env` file:
```
ADMIN_IDS=123456789,987654321
```

- Comma-separated list of admin user IDs
- Get your ID from @userinfobot on Telegram
- All listed IDs have admin privileges
