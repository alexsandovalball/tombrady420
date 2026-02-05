# Telegram Media Catalog Bot

A production-ready Telegram bot for managing and browsing a media product catalog. The bot automatically monitors a Telegram channel for new products and provides users with an intuitive interface for browsing, searching, and receiving notifications.

## 📚 Documentation

- **[User Guide](docs/USER_GUIDE.md)** - Complete guide for bot users
- **[Admin Guide](docs/ADMIN_GUIDE.md)** - Admin features and workflows
- **[Multi-Bot Deployment](docs/MULTIBOT_DEPLOYMENT.md)** - Scale with multiple bot instances ⭐ NEW
- **[Environment Configuration](.env.example)** - All configuration options explained

## 🆕 What's New: Multi-Bot Support

Scale your bot to serve thousands of users! The catalog bot now supports running multiple bot instances simultaneously with:

- **🤖 Multi-Instance Architecture** - Primary bot + unlimited secondary bots
- **📡 Cross-Bot Admin Messaging** - Broadcasts reach ALL users across ALL bots
- **⚡ 10x Performance** - WAL mode, FTS5 search, query caching
- **🔄 Backward Compatible** - Single bot deployments still work perfectly

See **[Multi-Bot Deployment Guide](docs/MULTIBOT_DEPLOYMENT.md)** for setup instructions.

## ✨ Features

### For Users
- 🌐 **Multi-Language Support** - 7 languages with automatic translation (English UK, English USA, German, Dutch, Italian, Spanish, French)
- 🔍 **Fuzzy Search** - Natural language search with typo tolerance
- 📂 **Category Browsing** - Organized product categories with subcategories
- 🔔 **Smart Notifications** - Opt-in notifications for new products with rate limiting
- 📱 **Interactive Interface** - Button-based navigation, no typing required
- 🎯 **Language Selection at Start** - New users choose their language immediately
- 🌍 **Easy Language Switching** - Change language anytime from start page or /language command
- 📄 **Pagination** - Smooth navigation through large catalogs
- 📋 **Command Menu** - All available commands displayed below input bar

### For Admins
- 🤖 **Automatic Product Detection** - Monitors channel and captures new media automatically
- 🏷️ **Category Management** - Interactive categorization with subcategory support
- 👥 **User Management** - View users, manage notifications, block/unblock
- 💬 **Custom Messaging** - Send messages to specific users or broadcast to all
- 📡 **Cross-Bot Broadcasting** - Admin broadcasts reach users across all bot instances ⭐ NEW
- 🔄 **Recategorization** - Find and categorize uncategorized products
- 🗑️ **Product Management** - Delete individual products or bulk delete (with confirmation)

### Technical Features
- ⚡ **Async/Await Architecture** - High performance with asyncio and aiosqlite
- 🔒 **Environment-based Configuration** - All settings via environment variables
- 🤖 **Multi-Bot Scaling** - Run multiple bot instances with shared database ⭐ NEW
- 🚀 **WAL Mode** - SQLite Write-Ahead Logging for better concurrent access ⭐ NEW
- 🔍 **FTS5 Search** - Full-text search 10x faster than LIKE queries ⭐ NEW
- 🌐 **Webhook Support** - Production-ready for Render.com and other platforms
- 📦 **SQLite Database** - Lightweight, no external database required
- 🛡️ **Rate Limiting** - Anti-spam protection for notifications
- 🔄 **Automatic Migration** - Database schema updates on startup
- 📝 **Comprehensive Logging** - Structured logs for monitoring

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

See [configs/.env.example](configs/.env.example) for complete Render.com deployment notes.

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
- 💎 **Concentrates** - Various extract types (Snowcaps, Moonrocks, Hash, Badder, Shatter, etc.)
- 🚬 **Pre-Rolls** - Ready-to-smoke products (Flower Prerolls, Infused Prerolls)
- 🍄 **Shrooms** - Mushroom products
- 🌸 **Flower** - Cannabis flower with quality tiers
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

- 🇬🇧 English (UK) (default)
- 🇺🇸 English (USA)
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

## ⚡ Performance

- Async architecture for concurrent users
- Translation caching for speed  
- Rate limiting prevents overload
- Pagination for large catalogs
- Efficient SQLite database
- Minimal resource usage

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
