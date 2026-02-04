# User Guide - Telegram Catalog Bot

## Getting Started

Welcome to the Telegram Media Catalog Bot! This bot helps you browse and search through a product catalog directly in Telegram.

### First Time Using the Bot

1. **Start the bot** - Send `/start` to the bot
2. **Select your language** - Choose from 7 supported languages:
   - 🇬🇧 English (UK)
   - 🇺🇸 English (USA)
   - 🇩🇪 German
   - 🇳🇱 Dutch
   - 🇮🇹 Italian
   - 🇪🇸 Spanish
   - 🇫🇷 French
3. **Browse or search** - Use the menu to browse categories or type keywords to search

## Accessing Commands

The most commonly used commands are conveniently displayed in the Telegram command menu below the text input bar:
- `/start` - Start the bot
- `/menu` - Browse the catalog

Additional commands are available by typing them directly (not shown in menu):
- `/language` - Change language
- `/subscribe` - Enable notifications
- `/unsubscribe` - Disable notifications

Simply tap the menu icon (☰) or type `/` to see available commands.

## Available Commands

Commands are automatically displayed in Telegram's command menu for easy access. Just tap the menu icon or type `/`.

### `/start`
Shows the welcome message and main menu. For new users, you'll first select your preferred language. The start page includes:
- **View Catalog** button - Browse products by category
- **Change Language** button - Switch to a different language anytime
- **Resubscribe to Notifications** button (if you've unsubscribed)

### `/menu`
Opens the product catalog browser where you can:
- View all products
- Browse by category
- Navigate through pages

### `/language`
Change your language preference at any time. All bot messages, buttons, navigation, and product information will automatically translate to your selected language. You can also access language settings from the "Change Language" button on the start page.

### `/subscribe`
Enable notifications for new products. You'll receive a message whenever new items are added to the catalog.

### `/unsubscribe`
Disable product notifications. You can re-enable them later with `/subscribe`.

### Search
Just type what you're looking for! Examples:
- "blue cart"
- "edibles"
- "premium flower"

The bot uses fuzzy search, so it can handle typos and partial matches.

## Browsing Products

### By Category

Categories organize products into logical groups:

- 🛒 **Cartridges** - Vape cartridges (Authentics, Replicas)
- 🍫 **Edibles** - Consumable products (Flower Edibles, Shroom Edibles)
- 💎 **Concentrates** - Extracts and concentrates (Snowcaps, Moonrocks, Hash, Badder, Shatter, Distillate, THC Powder, RSO, Rosin, Sugar, Others)
- 🚬 **Pre-Rolls** - Ready-to-smoke products (Flower Prerolls, Infused Flower Prerolls)
- 🍄 **Shrooms** - Mushroom products
- 🌸 **Flower** - Cannabis flower (Top Shelf Candy, Premium Exotics, Exotics, Premium Light Deps, Light Deps, Light Assist, Lows)
- 📅 **Dated Proofs** - Verification photos
- ✈️ **Client Touchdowns** - Delivery confirmations
- 📢 **Announcements** - Important updates

### Pagination

When browsing products:
- Use **Next »** and **« Previous** buttons to navigate pages
- The page indicator shows your current position (e.g., "Page 2/5")
- Each page shows up to 10 products

### Viewing Products

Click **👁️ View** on any product to see:
- Full-size media (photos, videos, or documents)
- Complete product description
- Category and subcategory
- Product ID for reference
- Order contact information

## Search Feature

The search feature supports:
- **Natural language** - Type phrases like "looking for blue cartridges"
- **Keywords** - Single words like "edibles" or "premium"
- **Partial matches** - "cart" will find "cartridges"
- **Typo tolerance** - "edibls" will still find "edibles"

Search results show:
- Number of matches found
- Products sorted by relevance
- Same navigation as category browsing

## Notifications

### Enabling Notifications

Use `/subscribe` to receive notifications when:
- New products are added to the catalog
- Products are categorized and ready to view

### Managing Notifications

- **Rate limited** - Maximum 5 notifications per hour to prevent spam
- **User controlled** - Use `/unsubscribe` to opt out anytime
- **Category filtering** - Some categories (Dated Proofs, Client Touchdowns) don't trigger notifications
- **Instant delivery** - Notifications sent immediately when products are added

### Notification Content

Each notification includes:
- Product category
- Brief description
- Direct link to view the product

## Language Support

### Supported Languages

The bot supports 6 languages with automatic translation:
1. English (default)
2. German
3. Dutch
4. Italian
5. Spanish
6. French

### Changing Language

1. Send `/language` command
2. Select your preferred language from the menu
3. All future messages will be in your selected language
4. Your preference is saved permanently

### Translation Features

- **Automatic** - All bot messages are translated
- **Real-time** - Interface updates immediately
- **Consistent** - Same language across all features
- **User-specific** - Each user can have their own language preference

## Tips for Best Experience

### Browsing
- Start with categories if you know what type of product you want
- Use search when looking for something specific
- Check "All Products" to see the entire catalog

### Searching
- Be specific to get better results
- Try different keywords if first search doesn't match
- Use category names to filter results

### Notifications
- Enable notifications to stay updated on new products
- Disable if you prefer to check manually
- Notifications respect your language preference

### Product Information
- Product IDs help you reference specific items when ordering
- Media shows exactly what you're getting
- Categories help you find similar products

## Ordering Products

Products displayed in the bot are for browsing only. To place an order:

1. View the product you want
2. Note the product ID and details
3. Contact the seller using the contact information shown in the product view
4. Provide the product ID when ordering

## Troubleshooting

### Bot not responding
- Check your internet connection
- Try sending `/start` to restart
- Contact bot owner if issue persists

### Search returns no results
- Try different keywords
- Check spelling (fuzzy search helps but isn't perfect)
- Browse categories instead

### Notifications not working
- Verify you're subscribed with `/subscribe`
- Check that you haven't blocked the bot
- New product notifications only apply to future additions

### Language not changing
- Make sure you clicked on a language in the menu
- Try `/language` command again
- Contact bot owner if issue persists

## Privacy

### Data Collected
The bot stores:
- Your Telegram user ID
- Username (if public)
- First and last name
- Language preference
- Notification preference
- Search queries (for pagination)
- Last interaction time

### Data Usage
Your data is used only to:
- Provide bot functionality
- Remember your preferences
- Send notifications (if enabled)
- Show admin statistics (anonymized)

### Data Control
You control:
- Language preference via `/language`
- Notification preference via `/subscribe` and `/unsubscribe`
- When you interact with the bot

The bot owner can:
- View user count and statistics
- Manage notification settings for all users
- Block/unblock users if necessary

## Support

If you encounter issues or have questions:
1. Check this guide for answers
2. Try restarting with `/start`
3. Contact the bot administrator

## Updates

The bot is regularly updated with:
- Bug fixes
- New features
- Performance improvements
- Additional language support

No action required from users - updates apply automatically.
