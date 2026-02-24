# Telegram Media Catalog Bot

A production-ready Telegram bot for managing and browsing a media product catalog. The bot automatically monitors a Telegram channel for new products and provides users with an intuitive interface for browsing, searching, and receiving notifications.

## 📚 Documentation

- **[User Guide](docs/USER_GUIDE.md)** - Complete guide for bot users
- **[Admin Guide](docs/ADMIN_GUIDE.md)** - Admin features and workflows
- **[Environment Configuration](configs/.env.example)** - All configuration options explained

## ✨ Features

### For Users
- 🌐 **Multi-Language Support** - 6 languages with automatic translation (English USA, German, Dutch, Italian, Spanish, French)
- 🔍 **Fuzzy Search** - Natural language search with typo tolerance
- 📂 **Category Browsing** - Organized product categories with subcategories
- 🔔 **Smart Notifications** - Opt-in notifications for new products with rate limiting
- 📱 **Interactive Interface** - Button-based navigation, no typing required
- 🎯 **Language Selection at Start** - New users choose their language immediately
- 🌍 **Easy Language Switching** - Change language anytime from start page or /language command
- 📄 **Pagination** - Smooth navigation through large catalogs
- 📋 **Command Menu** - All available commands displayed below input bar
- 🌐 **WebApp Catalog** - Full-featured Telegram mini-app with category/subcategory navigation, media gallery, and product browsing matching the bot interface

### For Admins
- 🤖 **Automatic Product Detection** - Monitors channel and captures new media automatically
- 🏷️ **Category Management** - Interactive categorization with subcategory support
- 👥 **User Management** - View users, manage notifications, block/unblock, delete users
- 🔧 **Multi-Bot Management** - Group users by bot instance, prune inactive bots
- 💬 **Custom Messaging** - Send messages to specific users or broadcast to all
- 🔄 **Recategorization** - Find and categorize uncategorized products
- 🗑️ **Product Management** - Delete individual products or bulk delete (with confirmation)

### Technical Features
- ⚡ **Async/Await Architecture** - High performance with asyncio and aiosqlite
- 🚀 **Optimized Codebase** - Loop-based database migrations, centralized logging, minimal redundancy
- 🔒 **Environment-based Configuration** - All settings via environment variables
- 🌐 **Webhook Support** - Production-ready for Render.com and other platforms
- 📦 **SQLite Database** - Lightweight, no external database required (WAL mode enabled)
- 🎯 **Performance Indexes** - Comprehensive database indexing for fast queries
- 🛡️ **Rate Limiting** - Anti-spam protection for notifications
- 🔄 **Automatic Migration** - Database schema updates on startup via optimized loop-based system
- 📝 **Centralized Logging** - Structured logs for monitoring with unified configuration
- ⚙️ **Optimized Performance** - 10-second API timeouts, connection pooling, efficient caching
- 🎨 **Emoji Support** - Consistent emoji usage across all user-facing messages and buttons
- 🔐 **Safe Message Delivery** - Automatic fallback for entity parsing errors with graceful degradation
- 🧹 **Auto User Cleanup** - Automatically removes deleted/deactivated Telegram accounts from database
- 🌐 **WebApp Support** - Full Telegram WebApp with category navigation, product filtering, media gallery, and HMAC-SHA256 security verification
- 🗜️ **Minimal Codebase** - Clean, well-organized structure with ~12K lines of optimized Python code

## 🚀 Quick Start

### Prerequisites
- Python 3.8 or higher
- Telegram Bot Token from [@BotFather](https://t.me/botfather)
- A Telegram channel (bot must be admin with "Post messages" permission)
- Your Telegram user ID (get from [@userinfobot](https://t.me/userinfobot))

### Installation

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd tombrady420
   ```

2. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

3. **Configure environment variables**
   ```bash
   cp configs/.env.example .env
   ```
   
   Edit `.env` with your values:
   ```env
   BOT_TOKEN=your_bot_token_from_botfather
   CHANNEL_ID=-1001234567890
   ADMIN_IDS=123456789,987654321
   PRIMARY_ADMIN_ID=123456789  # Optional: Admin who receives categorization requests
   ORDER_CONTACT=@yourusername
   ```
   
   See [configs/.env.example](configs/.env.example) for all available options.

4. **Run the bot**
   ```bash
   python main.py
   ```

The bot will:
- Initialize the database automatically
- Validate your configuration
- Start listening for commands and channel posts

## 🌐 Deployment on Render.com

Perfect for free hosting with persistent storage:

1. **Fork this repository** to your GitHub account

2. **Create a new Web Service** on [Render.com](https://render.com)
   - Connect your GitHub repository
   - Select "Python" environment
   - Build command: `pip install -r requirements.txt`
   - Start command: `python webhook_server.py`

3. **Set environment variables** in Render dashboard:
   ```
   BOT_TOKEN=your_bot_token
   CHANNEL_ID=-1001234567890
   ADMIN_IDS=123456789,987654321
   USE_WEBHOOK=true
   WEBHOOK_URL=https://your-app-name.onrender.com
   ORDER_CONTACT=@yourusername
   ```

4. **Optional: Add persistent disk** for database
   - Create a disk in Render dashboard
   - Mount to `/opt/render/project/data`
   - Set `DB_PATH=/opt/render/project/data/catalog.db`

5. **Configure webhook** for your bot:
   ```bash
   curl "https://api.telegram.org/bot<YOUR_BOT_TOKEN>/setWebhook?url=https://your-app-name.onrender.com/webhook"
   ```

### Multi-Bot Deployment (Single Webservice)

Run multiple bot instances from the **same** Render webservice:

1. **Add additional bot tokens** as environment variables:
   ```
   BOT_TOKEN=primary_bot_token
   BOT_TOKEN_1=secondary_bot_1_token
   BOT_TOKEN_2=secondary_bot_2_token
   BOT_TOKEN_3=secondary_bot_3_token
   ```

2. **Each bot gets its own webhook endpoint**:
   - Primary bot: `https://your-app.onrender.com/webhook`
   - Bot 1: `https://your-app.onrender.com/webhook/1`
   - Bot 2: `https://your-app.onrender.com/webhook/2`
   - Bot 3: `https://your-app.onrender.com/webhook/3`

3. **Configure webhooks for each bot**:
   ```bash
   curl "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=https://your-app.onrender.com/webhook"
   curl "https://api.telegram.org/bot<BOT_TOKEN_1>/setWebhook?url=https://your-app.onrender.com/webhook/1"
   curl "https://api.telegram.org/bot<BOT_TOKEN_2>/setWebhook?url=https://your-app.onrender.com/webhook/2"
   ```

4. **Benefits**:
   - ✅ All bots share the same database
   - ✅ Single deployment - easier to manage
   - ✅ Lower costs - one webservice for multiple bots
   - ✅ Easy scaling - just add more `BOT_TOKEN_N` variables

See [configs/.env.example](configs/.env.example) for complete Render.com deployment notes.

## 📱 Telegram WebApp Demo

The bot includes a demonstration Telegram WebApp (mini-app) that showcases WebApp capabilities:

### Features

- **WebApp Interface** - Runs inside Telegram with native feel
- **InitData Display** - Shows Telegram user information and authentication data
- **Demo Catalog** - Sample product display
- **Send Data** - Demonstrates sending data from WebApp to bot via `web_app_data` updates
- **Server-side Verification** - Implements HMAC-SHA256 initData verification per Telegram docs

### How to Access

1. **After deployment**, the WebApp is automatically served at `/webapp`
2. **Send `/start`** to your bot - you'll see a "🌐 Open WebApp" button
3. **Tap the button** to open the WebApp inside Telegram
4. **Try the demo** - view initData, send sample order data, expand/close WebApp

### Configuration

The WebApp URL is automatically configured:
- **Auto-derived**: If `WEBHOOK_URL` is set, `WEB_APP_URL` = `WEBHOOK_URL + '/webapp/index.html'`
- **Manual override**: Set `WEB_APP_URL` environment variable explicitly
- **Example**: `WEB_APP_URL=https://your-app.onrender.com/webapp/index.html`

### Testing Locally

To test the WebApp locally with ngrok:

```bash
# 1. Install ngrok (https://ngrok.com)
# 2. Start your bot with webhook mode
python webhook_server.py

# 3. In another terminal, expose local server
ngrok http 8000

# 4. Set webhook and WebApp URL to ngrok URL
export WEBHOOK_URL=https://your-ngrok-id.ngrok.io
export WEB_APP_URL=https://your-ngrok-id.ngrok.io/webapp/index.html

# 5. Configure bot webhook
curl "https://api.telegram.org/bot<BOT_TOKEN>/setWebhook?url=https://your-ngrok-id.ngrok.io/webhook"

# 6. Test the WebApp from Telegram
```

### Implementation Details

- **Static files**: WebApp is served from `webapp/` directory (HTML, JS, CSS)
- **API endpoints**: 
  - `/api/categories` - Get category structure with product counts
  - `/api/products` - Get products filtered by category/subcategory
  - `/api/catalog/{product_id}/media/{media_index}` - Stream product media files
- **Features**:
  - Hierarchical category/subcategory navigation matching bot interface
  - Product detail modal showing all media for a product
  - Breadcrumb navigation for easy browsing
  - Secure media streaming without exposing bot tokens
- **Handler**: `handlers/webapp_data.py` processes `web_app_data` updates
- **Security**: `utils/webapp_security.py` implements initData HMAC-SHA256 verification on all API endpoints
- **Integration**: WebApp button added to `/start` command when `WEB_APP_URL` is configured

See [Telegram WebApp Documentation](https://core.telegram.org/bots/webapps) for more information.

## 📖 User Commands

The most commonly used commands are displayed in the Telegram command menu below the input bar for easy access:

- `/start` - Welcome message and language selection (for new users)
- `/menu` - Browse the full product catalog

Additional commands (not shown in menu but still available):
- `/language` - Change your preferred language  
- `/subscribe` - Enable notifications for new products
- `/unsubscribe` - Disable notifications
- **Search** - Just type keywords to search (e.g., "blue cart", "edibles")

See the [User Guide](docs/USER_GUIDE.md) for detailed instructions.

## 🔧 Admin Commands

Admin commands appear in the command menu for users with admin permissions:

- `/users` - View and manage bot users
- `/recategorize` - Send categorization prompts for uncategorized products
- `/nuke` - Delete all products (double confirmation required)
- `/send` - Broadcast a message to a specific user (interactive workflow)
- `/broadcast` - Send a message to all users (interactive workflow)

Additional admin commands (not shown in menu but still available):
- `/block <user_id>` - Block a user from using the bot
- `/unblock <user_id>` - Unblock a user

See the [Admin Guide](docs/ADMIN_GUIDE.md) for detailed workflows.

## 📦 Product Categories

Products are organized into categories for easy browsing:

- 🛒 **Cartridges** - Vape cartridges with subcategories (Authentics, Replicas)
- 🍫 **Edibles** - Consumable products (Flower Edibles, Shroom Edibles)
- 💎 **Concentrates** - Various extract types (Live Resin, Rosin, Diamonds & Sauce, Shatter, Badder, Crumble, Sugar, Distillate, Hash, Kief, THCa Powder, RSO, Others)
- 🚬 **Pre-Rolls** - Ready-to-smoke products (Flower Prerolls, Infused Prerolls)
- 🍄 **Shrooms** - Mushroom products
- 🌸 **Flower** - Cannabis flower with quality tiers and infused options (Top Shelf Candy, Premium Exotics, Exotics, Premium Light Deps, Light Deps, Light Assist, Lows, Infused Flower)
- 📅 **Dated Proofs** - Verification photos (no notifications)
- ✈️ **Client Touchdowns** - Delivery confirmations (no notifications)
- 📢 **Announcements** - Important updates (triggers notifications)


## 🔄 How It Works

### For Users

1. Send `/start` to the bot
2. Select your preferred language (first time only)
3. Browse categories or search for products
4. View products with full media and details
5. Optional: Subscribe to notifications for new products

### For Admins

1. Post media to your monitored channel
2. Bot automatically detects and saves the product
3. You receive a categorization prompt
4. Select the appropriate category and subcategory
5. Bot notifies all subscribed users automatically

### Product Detection

When media is posted to the monitored channel:
- Bot captures photos, videos, documents, and animations
- Supports media albums (multiple photos/videos in one post)
- Extracts captions and stores all metadata
- Waits for admin to categorize via interactive buttons
- Sends notifications to users once categorized

## 🗄️ Database

The bot uses SQLite with automatic schema management:

- **Automatic initialization** - Creates tables on first run
- **Auto-migration** - Adds new columns to existing databases
- **No manual setup** - Everything handled automatically
- **Persistent storage** - Use Render.com persistent disks for production

Tables: products, bot_users, pagination_state, media_groups, pending_categorization, notification_queue, and more.

## 🌐 Language Support

### Supported Languages

- 🇺🇸 English (USA) (default)
- 🇩🇪 German  
- 🇳🇱 Dutch
- 🇮🇹 Italian
- 🇪🇸 Spanish
- 🇫🇷 French

### How Translation Works

1. All base strings are in English
2. New users select preferred language via interactive language menu at `/start`
3. Existing users can change language via "Change Language" button on start page or `/language` command
4. Bot automatically translates:
   - All messages and instructions
   - Button texts and navigation labels
   - Category and subcategory names
   - Command descriptions
5. Translations are cached for performance
6. Each user sees the interface in their chosen language
7. Product captions and all user-facing text are translated
8. **Important**: Button callbacks use internal category codes, so correct commands are sent regardless of the displayed translation

### Translation System Details

The bot uses a smart translation system that:
- **Preserves user names**: User names always appear in their original form, never translated
- **Preserves contact information**: Order contact usernames (e.g., @FLYAWAYPEP) remain unchanged
- **Template-based translation**: Translates message templates first, then inserts user data
- **Example**: "Welcome, {name}!" → Translates to "Bienvenido, {name}!" → Displays as "Bienvenido, Hans!" (name preserved)
- This ensures user names appear exactly as they do in Telegram profiles, preventing confusion

## 🔔 Notification System

- Opt-in via `/subscribe` command
- Rate limited to 5 per hour per user
- Automatic delivery when products are categorized
- Respects user's language preference
- Excluded categories: Dated Proofs, Client Touchdowns

## 🔐 Security & Privacy

### Environment Variables

All sensitive configuration managed through environment variables:
- Bot token never hardcoded
- Admin IDs configurable
- No credentials in source code
- Safe for public repositories

See [configs/.env.example](configs/.env.example) for all options.

### User Privacy

**Data collected:**
- Telegram user ID, username, first/last name
- Language and notification preferences
- Interaction statistics

**Data NOT collected:**
- Message content (except search queries)
- Personal information beyond Telegram profile
- Payment or location data

## 🛡️ Error Handling & Reliability

### Robust Message Delivery

The bot includes comprehensive error handling for Telegram API operations:

- **Entity Parsing Fallback**: If a message fails to parse (e.g., due to special characters in product descriptions), the bot automatically:
  1. Retries with escaped text
  2. Falls back to plain text without formatting
  3. Ensures users always receive content, even if formatting fails

- **Automatic User Cleanup**: The bot detects and automatically removes users whose Telegram accounts have been:
  - Deleted or deactivated
  - Blocked the bot
  - Are otherwise permanently unreachable
  - This prevents wasted API calls and keeps the database clean

- **Safe Send Wrappers**: All message sending operations use safe wrappers that:
  - Handle Telegram API errors gracefully
  - Log detailed error information for debugging
  - Continue processing other messages on individual failures
  - Never crash the bot due to a single user error

### Emoji Support

- **Consistent Icons**: Uses a centralized emoji module for uniform appearance
- **User-Friendly**: Emojis enhance readability and provide visual cues
- **Accessible**: Emojis complement text, never replace important information
- **Examples**: 
  - 🔔 Notifications
  - ✅ Confirmations
  - ❌ Errors and cancellations
  - 📋 Menus and catalogs
  - 🌐 Language settings

## 🛠️ Development

### Project Structure

```
tombrady420/
├── main.py                 # Main bot entry point (polling mode)
├── webhook_server.py       # Webhook server for production
├── database.py             # Database models and operations
├── requirements.txt        # Python dependencies
├── configs/
│   ├── config.py          # Configuration management
│   └── .env.example       # Environment variables template
├── handlers/
│   ├── start.py           # /start command with language selection
│   ├── language.py        # /language command
│   ├── menu.py            # Catalog browsing
│   ├── search.py          # Search functionality
│   ├── product_view.py    # Product display
│   └── admin.py           # Admin commands
├── utils/
│   ├── helpers.py         # Utility functions
│   ├── categories.py      # Category management
│   ├── pagination.py      # Pagination logic
│   ├── fuzzy_search.py    # Search implementation
│   └── notifications.py   # Notification service
├── translations/
│   ├── language_config.py # Supported languages
│   ├── translator.py      # Translation service
│   └── strings.py         # Translatable strings
└── docs/
    ├── USER_GUIDE.md      # Complete user documentation
    └── ADMIN_GUIDE.md     # Admin documentation
```

### Running Tests

```bash
python test_language_migration.py
python test_start_command_integration.py
```

### Local Development

```bash
pip install -r requirements.txt
cp configs/.env.example .env
# Edit .env with your values
python main.py
```

## 📝 Environment Variables Reference

All configuration via environment variables - see [configs/.env.example](configs/.env.example):

**Required:**
- `BOT_TOKEN` - Telegram bot token
- `CHANNEL_ID` - Channel where products are posted
- `ADMIN_IDS` - Comma-separated admin user IDs

**Optional:**
- `CHANNEL_USERNAME` - Alternative to CHANNEL_ID
- `DB_PATH` - Database file path (default: catalog.db)
- `USE_WEBHOOK` - Enable webhook mode (default: false)
- `WEBHOOK_URL` - Webhook URL for production
- `WEB_APP_URL` - WebApp URL (auto-derived from WEBHOOK_URL if not set)
- `ORDER_CONTACT` - Contact info for orders (default: @FLYAWAYPEP)

## 🐛 Troubleshooting

**Bot doesn't start:**
- Check `.env` file has required variables
- Verify BOT_TOKEN is correct
- Check logs for error messages

**Products not detected:**
- Verify bot is admin in channel
- Check CHANNEL_ID matches
- Review logs for errors

**Notifications not working:**
- Users must subscribe with `/subscribe`
- Check rate limits (5 per hour)
- Ensure users haven't blocked bot

**Webhook issues (Render.com):**
- Set `USE_WEBHOOK=true`
- Set `WEBHOOK_URL=https://your-app.onrender.com`
- Verify HTTPS (not HTTP)

## ⚡ Performance & Optimization

The codebase has been optimized for speed, efficiency, and maintainability:

**Database Optimizations:**
- WAL mode enabled for concurrent read/write operations
- Comprehensive indexes on all frequently queried columns
- Loop-based migration system (reduced from ~75 to ~28 lines)
- Efficient query patterns with proper filtering

**Code Optimizations:**
- Centralized logging configuration eliminates duplication
- Async/await architecture throughout for maximum concurrency
- Translation caching reduces redundant API calls
- Media file caching with LRU eviction (configurable size and TTL)
- Optimized safe_send wrappers with automatic fallback

**Resource Efficiency:**
- Minimal memory footprint with bounded caches
- Rate limiting prevents API overload
- Pagination for large datasets
- Automatic cleanup of deleted users
- Connection pooling and 10-second API timeouts

**Codebase Metrics:**
- ~12,277 lines of clean, optimized Python code
- Loop-based database migrations (saved 47 lines)
- Centralized logging configuration (saved 14 lines)
- No unused imports or dead code
- Consistent coding patterns throughout
- Well-organized module structure with clear separation of concerns

## 🎯 Use Cases

Perfect for:
- Product catalogs
- Media galleries
- Service listings
- Portfolio showcases
- Announcement channels
- Document libraries
- Any organized media collection

---

**Built with Python, python-telegram-bot, SQLite, and deep-translator**
