# Admin Guide - Telegram Catalog Bot

## Overview
This guide explains how to use the admin features of the bot.

## Accessing Admin Commands

Admin commands are automatically displayed in your Telegram command menu. The bot sets up personalized command lists:
- **User commands** - Visible to all users (start, menu)
- **Admin commands** - Additional commands visible only to admins

The command menu appears below the text input bar. Tap the menu icon (☰) or type `/` to see available commands.

**Note:** Some commands like `/language`, `/subscribe`, `/unsubscribe`, `/block`, and `/unblock` are not shown in the command menu but are still available by typing them directly.

## Admin Commands

### `/start`
Welcome message - available to all users

### `/menu`
Browse products by category - available to all users
- Shows category selection menu
- Each category displays product count
- Click category to view filtered products

### `/users`
View and manage bot users grouped by bot instance (admin only)
- **Multi-bot setups**: Shows bot selection menu, choose which bot's users to manage
- **Single-bot setups**: Automatically shows users (no selection needed)
- Displays: username, full name, ID, interaction count, last seen, notification status
- **Toggle notifications** for individual users with one click
- **Block/unblock users** to prevent/allow bot access
- Navigate pages with ⬅️ Previous / ➡️ Next buttons
- Useful for monitoring bot engagement and controlling who gets notifications

### `/botusers`
View users grouped by bot instance (admin only)
- Shows users organized by which bot instance they interacted with
- Active bots marked with ✅
- Inactive/deleted bots marked with ⚠️ (users should be pruned)
- **Individual user management**:
  - Delete individual users with confirmation
  - View user details (ID, username, name, notification status, block status)
- **Bulk operations**:
  - Delete all users from a specific bot instance
- **Pagination**: 10 users per page
- **Use case**: Clean up users from old/deleted bot instances

### `/prunebots`
Prune users from inactive/deleted bots (admin only)
- **Preview mode** (default): Shows what would be deleted without actually deleting
- **Execute mode**: Use confirmation button to actually delete
- Automatically removes:
  - Users from inactive bot instances
  - Products posted by inactive bots
  - Pending notifications for those users
  - Queued custom messages
- **Safety**: Preserves all data from active bots
- **Use case**: Run after replacing or removing bot tokens

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

### `/send`
Send a message to a specific user (admin only) - **Interactive Workflow**

**New 3-Step Process:**
1. **Start Command**: Type `/send`
2. **Enter User ID**: Bot asks for user ID, you reply with the ID
3. **Enter Message**: Bot asks for message, you type your message
4. **Confirm & Send**: Bot shows confirmation with buttons - click "Confirm & Send" to complete

**Features:**
- Validates user exists and is not blocked
- Shows clear confirmation before sending
- Can cancel at any step
- Rate limited to prevent spam

### `/broadcast`
Send a message to all users (admin only) - **Interactive Workflow**

**New 2-Step Process:**
1. **Start Command**: Type `/broadcast`
2. **Enter Message**: Bot asks for message, you type your broadcast
3. **Confirm & Send**: Bot shows confirmation with user count - click "Confirm & Broadcast" to complete

**Features:**
- Shows how many users will receive the message
- Automatically excludes blocked users and admins
- Rate limiting applies per user
- Can cancel before sending
- Messages are queued and delivered with delays

### `/block` and `/unblock`
Block/unblock users from using the bot (admin only)

**Note:** These commands are not shown in the command menu but are still available.

**Usage:**
- `/block <user_id>` - Block a user (e.g., `/block 123456789`)
- `/unblock <user_id>` - Unblock a user (e.g., `/unblock 123456789`)
- Use `/users` to find user IDs

### `/clearcache`
Clear the bot-specific file ID cache (admin only)
- Clears all cached bot-specific media file IDs from persistent database
- **New in latest version**: Cache is now persistent across bot restarts
- Shows count of cleared entries
- Useful when:
  - Adding new secondary bot instances
  - Media stops displaying correctly
  - After configuration changes
  - Testing media file resolution
- **Multi-bot setup**: Each bot automatically caches its own file IDs when viewing products
- After clearing cache, media will be automatically re-cached on next product view
- **Important**: Admins should `/start` each secondary bot to enable proper file ID caching via message forwarding

### `/setcontact`
Set the order contact username (admin only)
- Interactive workflow asks for new contact username
- Must start with `@` (e.g., `@username`)
- This contact appears in product views for users to place orders

## New Product Workflows

### Automatic Product Detection from Channel

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
> **Important:** All categories **WILL trigger notifications** to subscribed users.

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
PRIMARY_ADMIN_ID=123456789  # Optional: receives categorization requests
```

- **ADMIN_IDS**: Comma-separated list of admin user IDs who can use admin commands
- **PRIMARY_ADMIN_ID**: (Optional) The admin who receives new product categorization requests
  - If not set, the first admin in ADMIN_IDS will receive categorization requests
  - All admins can still categorize products via `/recategorize` command
- Get your ID from @userinfobot on Telegram
- All listed IDs in ADMIN_IDS have full admin privileges

## Translation System

The bot supports 7 languages with automatic translation. Key features:

### User Names and Contact Information
- **User names are never translated** - they appear exactly as in Telegram profiles
- **Order contact usernames remain unchanged** - e.g., @FLYAWAYPEP stays the same in all languages
- This prevents confusion and maintains proper communication channels

### How It Works
1. Message templates are translated with placeholders intact (e.g., "Welcome, {name}!")
2. User data is inserted after translation, preserving original values
3. Example: Spanish translation becomes "Bienvenido, {name}!" then displays as "Bienvenido, Hans!"
4. Translations are cached for performance

### Setting Order Contact
Use `/setcontact` to update the order contact username (default: @FLYAWAYPEP). This contact appears in welcome messages and product views across all languages.

## Multi-Bot Setup and Analytics

### Using Multiple Bot Instances

The bot supports running multiple bot instances (primary + secondary bots) for:
- **Load distribution** across different bot accounts
- **Testing** new features on a secondary bot
- **Regional deployment** with separate bots per region
- **Analytics** to track which bot users prefer

### Bot Username Tracking

When using multiple bot instances, the bot automatically tracks which bot posted each product:
- **Products remember their source bot** - stored in database with `bot_username` field
- **Automatic file ID resolution** - when viewing from a different bot, file IDs are automatically converted
- **Persistent cache** - bot-specific file IDs are cached in the database (survives restarts)
- **First bot a user interacts with is recorded** in user tracking

The `/users` command shows which bot each user interacted with:
- **🤖 Bot: @botusername** - Displayed in user details
- Helps understand user distribution across bot instances
- Useful for analyzing which bot is more popular

### Setting Up Secondary Bots

**Configuration:**
1. Add multiple bot tokens to `.env`:
   ```
   BOT_TOKEN=primary_bot_token
   BOT_TOKEN_1=secondary_bot_token_1
   BOT_TOKEN_2=secondary_bot_token_2
   ```
2. Each bot gets its own webhook endpoint: `/webhook`, `/webhook/1`, `/webhook/2`

**Important for Media Display:**
- Telegram file IDs are **bot-specific** (a file ID from Bot A won't work with Bot B)
- **New products automatically save the posting bot's username**
- When viewing products from a different bot, file IDs are **automatically converted**
- **Admins must `/start` each secondary bot** to enable file ID caching via message forwarding
- Conversion happens transparently - users won't notice any difference
- File ID cache is **persistent** in database and survives bot restarts

**After Adding a Secondary Bot:**
1. Send `/start` to the new bot from your admin account (required for file ID forwarding)
2. Add the bot as admin to your channel (if posting new products)
3. Test by viewing products through the secondary bot
4. Use `/clearcache` if you experience any media display issues

### Troubleshooting Multi-Bot Issues

**Media not displaying on secondary bot:**
- **Most common cause**: Admin hasn't `/start`ed the secondary bot yet
- **Solution**: Send `/start` to the secondary bot from your admin account
- Check logs for "bot can't initiate conversation" errors
- Use `/clearcache` to force re-caching of file IDs if needed

**File ID Resolution Process:**
1. User views product through Bot B (product was posted by Bot A)
2. Bot B detects file ID is from Bot A (checks `bot_username` in database)
3. Bot B checks its persistent cache for a cached file ID
4. If not cached, Bot B forwards the original message to admin to get Bot B's file ID
5. Bot B caches the new file ID in database for future use
6. Bot B displays the media using its own file ID

**Cache warnings on startup:**
- Warning: "File ID cache is empty"
- This is normal on first startup with multiple bots
- Follow the guidance to `/start` each bot

**Analytics not showing bot username:**
- Bot username is recorded on first interaction
- Only shows for users who interacted after this feature was added
- Existing users will show bot username on their next interaction
