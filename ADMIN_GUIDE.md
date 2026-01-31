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
View and manage bot users (admin only)
- Shows 10 users per page with pagination
- Displays: username, full name, ID, interaction count, last seen, **notification status**
- **Toggle notifications** for individual users with one click
- Navigate pages with ⬅️ Previous / ➡️ Next buttons
- Useful for monitoring bot engagement and controlling who gets notifications

### `/add_product`
Manually add a product to the catalog (admin only)
- **Usage**: Send media (photo/video/document) with caption `/add_product <CATEGORY> <description>`
- **Example**: Send a photo with caption `/add_product CARTRIDGES Premium Blue Dream Cart`
- Bot will prompt for subcategory if applicable
- Product is immediately added and **all subscribed users are notified**
- Categories: CARTRIDGES, EDIBLES, CONCENTRATES, PREROLLS, SHROOMS, FLOWER, DATEDPROOFS, CLIENTTOUCHDOWNS

### `/nuke`
Delete ALL products from catalog (admin only)
- **Double confirmation required!**
- First confirmation: "Are you sure?"
- Second confirmation: "Are you ABSOLUTELY SURE?"
- **Channel messages remain intact** - admins must manually clear channel if needed
- Use with extreme caution!

### `/recategorize`
Send categorization notifications for uncategorized products (admin only)
- Scans for products without categories
- Sends up to 10 categorization requests
- Run multiple times to process all uncategorized products

## New Product Workflows

### Workflow 1: Automatic from Channel

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
   - **All subscribed users are notified automatically**

### Workflow 2: Manual Addition by Admin

Using the `/add_product` command:

1. **Admin sends media with command**
   - Photo, video, or document with caption
   - Caption format: `/add_product <CATEGORY> <description>`
   - Example: `/add_product FLOWER Premium Blue Dream`

2. **Bot validates and prompts**
   - Checks if category is valid
   - If category has subcategories, shows selection menu
   - Admin selects subcategory or "No Subcategory"

3. **Product is added immediately**
   - Product saved with category/subcategory
   - Assigned unique product ID
   - Confirmation shown to admin

4. **Users are notified**
   - **All subscribed users receive notification**
   - Notification includes category and description
   - Users can view product immediately

## User Notification Management

### Viewing Users

Use `/users` command to see all bot users:
- **Paginated view**: 10 users per page
- **User details**: Username, full name, ID, interactions, last seen
- **Notification status**: 🔔 ON or 🔕 OFF for each user

### Toggling Notifications

Each user has a toggle button in the `/users` interface:
- **Click button** to toggle notifications for that user
- **Immediate effect**: Changes apply instantly
- **Visual feedback**: Button updates to show new status
- **Use cases**:
  - Disable notifications for inactive users
  - Prevent notifications to specific users
  - Re-enable for users who want to subscribe again

### Best Practices

**Selective Notifications:**
- Review user list regularly
- Disable notifications for users who don't interact
- Enable only for engaged/premium users if desired

**Spam Prevention:**
- System automatically limits to 5 notifications per user per hour
- Disable problem users manually via `/users`
- Users who block the bot are auto-unsubscribed

**User Privacy:**
- Users can self-manage via `/subscribe` and `/unsubscribe`
- Admin toggles override user preferences
- Use responsibly to maintain user trust

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

### 📢 Announcements
_(No subcategories)_

> **Note:** Products in ANNOUNCEMENTS, DATEDPROOFS, and CLIENTTOUCHDOWNS categories are **excluded from the "All Products" view** and can only be accessed by browsing their specific category.
> 
> **Important:** ANNOUNCEMENTS category **WILL trigger notifications** to subscribed users, while DATEDPROOFS and CLIENTTOUCHDOWNS will NOT.

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
- **Deleted products leave channel messages intact** - you can manually clear channel messages if desired

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
