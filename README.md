# Telegram Media Catalog Bot

A complete, production-ready Telegram bot that acts as a media product catalog. The bot automatically monitors a public channel for media posts, saves them as products, and allows users to browse and search the catalog.

📋 **[See recent changes in CHANGELOG.md](CHANGELOG.md)**

## Features

- ✅ **Automatic Product Detection**: Monitors a public channel and automatically saves media posts as products
- ✅ **Full Catalog Browsing**: View all products with pagination and category filtering
- ✅ **Fuzzy Search**: Natural language search with typo tolerance
- ✅ **Product Viewing**: View individual products with original media and captions
- ✅ **Category & Subcategory Support**: Organized browsing with hierarchical categories
- ✅ **Product Notifications**: Notify subscribed users about new products with anti-spam rate limiting
  - Note: DATEDPROOFS and CLIENTTOUCHDOWNS categories are excluded from notifications
  - Note: ANNOUNCEMENTS category WILL trigger notifications but is hidden from "All Products" view
- ✅ **Manual Product Addition**: Admins can manually add products with `/add_product` and notify subscribed users
- ✅ **User Management**: Admins can control notification preferences and block users
- ✅ **Custom Messaging**: Admins can send direct messages or broadcast to all users
- ✅ **User Blocking**: Admins can block/unblock users from using the bot
- ✅ **Admin Features**: Delete products, view statistics, manage categorization
- ✅ **Rate Limiting**: Prevents spam and abuse
- ✅ **Pagination**: Smart pagination for catalog and search results
- ✅ **Error Handling**: Robust error handling and logging

## Requirements

- Python 3.8+
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))
- A public Telegram channel where the bot is an admin with "Can post messages" rights

## Installation

### 1. Clone or Download the Project

```bash
cd media-catalog-bot
```

### 2. Install Dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure Environment Variables

The bot uses `python-decouple` for secure configuration management. **Never hardcode sensitive data like bot tokens in your source code.**

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Edit `.env` with your actual values:

```env
BOT_TOKEN=your_bot_token_here
CHANNEL_ID=-1001234567890
ADMIN_IDS=123456789,987654321
```

**How to get these values:**

- **BOT_TOKEN**: 
  1. Open Telegram and message [@BotFather](https://t.me/botfather)
  2. Send `/newbot` command and follow the instructions
  3. Copy the bot token provided (format: `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`)
  4. ⚠️ **Keep this token secret!** Anyone with this token can control your bot

- **CHANNEL_ID**: 
  1. Forward any message from your channel to [@userinfobot](https://t.me/userinfobot)
  2. The bot will show the channel ID (must be a negative number, e.g., `-1001234567890`)
  3. Alternatively, use [@getidsbot](https://t.me/getidsbot)
  
- **ADMIN_IDS**: 
  1. Message [@userinfobot](https://t.me/userinfobot) to get your Telegram user ID
  2. Add multiple admin IDs separated by commas with no spaces (e.g., `123456789,987654321`)
  3. These users will have admin privileges in the bot

**Security Note:** The `.env` file is already in `.gitignore` and will not be committed to Git. Never share your `.env` file or commit it to version control.

### 4. Set Up the Bot in Your Channel

1. Add the bot to your public channel as an **Administrator**
2. Grant the bot these permissions:
   - ✅ Can post messages
3. Make sure the channel is public or the bot has access

## Usage

### Running the Bot

```bash
python main.py
```

The bot will:
- Initialize the database
- Start monitoring the channel
- Begin accepting user commands

### User Commands

- `/start` - Welcome message and catalog button
- `/menu` - View full catalog with pagination
- `/subscribe` - Enable product notifications
- `/unsubscribe` - Disable product notifications
- Natural language search - Just type keywords like "blue tshirt", "red shoes", etc.

### Admin Commands

- `/add_product` - Manually add a product with category (send as caption to media)
- `/users` - View and manage users (notifications, blocking)
- `/block <user_id>` - Block a user from using the bot
- `/unblock <user_id>` - Unblock a user
- `/send <user_id> <message>` - Send a custom message to a specific user
- `/broadcast <message>` - Send a message to all users
- `/stats` - View catalog statistics (total products, added today)
- `/recategorize` - Send categorization prompts for uncategorized products
- `/nuke` - Delete all products (with double confirmation)

For detailed admin instructions, see [ADMIN_GUIDE.md](ADMIN_GUIDE.md).

### How It Works

1. **Product Creation**: When you (or anyone) posts a message with media in the monitored channel, the bot automatically:
   - Detects the media (photo, video, document, animation)
   - Saves the file_id, caption, and metadata to the database
   - Stores the message_id and chat_id for later deletion

2. **User Interaction**: Users interact with the bot privately:
   - Browse the catalog with pagination
   - Search products using natural language
   - View individual products with original media

3. **Admin Actions**: Admins can:
   - Delete products from catalog (channel messages remain intact for manual cleanup)
   - View statistics

## Deployment

### Render.com (Webhook Mode - Recommended for Production)

The bot now supports webhook mode for better performance on serverless platforms like Render.com.

1. Create a new Web Service on [Render.com](https://render.com)
2. Connect your GitHub repository
3. Configure the service:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn webhook_server:app --host 0.0.0.0 --port $PORT`
4. Add environment variables:
   - `BOT_TOKEN` - Your bot token
   - `CHANNEL_ID` - Your channel ID
   - `ADMIN_IDS` - Admin user IDs (comma-separated)
   - `USE_WEBHOOK=true` - Enable webhook mode
   - `WEBHOOK_URL` - Your Render app URL (e.g., https://your-app.onrender.com)
5. Deploy!

For detailed webhook deployment instructions, see [WEBHOOK_DEPLOYMENT.md](WEBHOOK_DEPLOYMENT.md).

### PythonAnywhere

1. Upload all files to your PythonAnywhere account
2. Install dependencies in a Bash console:
   ```bash
   pip3.10 install --user -r requirements.txt
   ```
3. Create a `.env` file with your configuration
4. Create a scheduled task (Tasks tab) to run:
   ```bash
   python3.10 main.py
   ```
   Or use a "Always-on task" for continuous running

### Railway

1. Create a new project on [Railway](https://railway.app)
2. Connect your GitHub repository or upload files
3. Add environment variables in Railway dashboard
4. Railway will automatically detect Python and install dependencies
5. The bot will start automatically

### VPS (Ubuntu/Debian)

1. SSH into your VPS
2. Install Python and pip:
   ```bash
   sudo apt update
   sudo apt install python3 python3-pip
   ```
3. Clone/upload the project
4. Install dependencies:
   ```bash
   pip3 install -r requirements.txt
   ```
5. Create `.env` file
6. Run with screen or systemd:

   **Using screen:**
   ```bash
   screen -S bot
   python3 main.py
   # Press Ctrl+A then D to detach
   ```

   **Using systemd (recommended):**
   
   Create `/etc/systemd/system/telegram-bot.service`:
   ```ini
   [Unit]
   Description=Telegram Media Catalog Bot
   After=network.target

   [Service]
   Type=simple
   User=your_username
   WorkingDirectory=/path/to/media-catalog-bot
   ExecStart=/usr/bin/python3 main.py
   Restart=always
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```
   
   Then:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable telegram-bot
   sudo systemctl start telegram-bot
   sudo systemctl status telegram-bot
   ```

### Docker

Create `Dockerfile`:

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "main.py"]
```

Create `docker-compose.yml`:

```yaml
version: '3.8'

services:
  bot:
    build: .
    restart: unless-stopped
    env_file:
      - .env
    volumes:
      - ./catalog.db:/app/catalog.db
```

Run:
```bash
docker-compose up -d
```

## Project Structure

```
tombrady420/
├── main.py                     # Main entry point
├── config.py                   # Configuration management
├── database.py                 # Database models and operations
├── webhook_server.py           # Webhook server for production
├── scan_channel.py             # Channel scanning utility
├── handlers/                   # Command and message handlers
│   ├── start.py               # /start command
│   ├── menu.py                # /menu and catalog browsing
│   ├── search.py              # Search functionality
│   ├── product_view.py        # Product viewing
│   └── admin.py               # Admin commands
├── utils/                      # Utility functions
│   ├── notifications.py       # Notification system
│   ├── pagination.py          # Pagination logic
│   ├── fuzzy_search.py        # Fuzzy search implementation
│   ├── categories.py          # Category definitions
│   └── helpers.py             # Helper functions
├── tests/                      # Test files
├── requirements.txt            # Python dependencies
├── .env.example               # Environment variables template
├── README.md                  # Main documentation
├── CHANGELOG.md               # Version history and changes
├── ADMIN_GUIDE.md             # Admin instructions
├── DATABASE_PERSISTENCE.md    # Database setup guide
└── WEBHOOK_DEPLOYMENT.md      # Webhook deployment guide
```

## Documentation

- **[README.md](README.md)** - Main documentation (this file)
- **[CHANGELOG.md](CHANGELOG.md)** - Version history and recent changes
- **[ADMIN_GUIDE.md](ADMIN_GUIDE.md)** - Detailed admin instructions
- **[DATABASE_PERSISTENCE.md](DATABASE_PERSISTENCE.md)** - Database setup and migration
- **[WEBHOOK_DEPLOYMENT.md](WEBHOOK_DEPLOYMENT.md)** - Production deployment guide

## Database

The bot uses SQLite by default (stored as `catalog.db`). The database contains:

- **products**: All saved products with file_id, caption, message metadata
- **pagination_state**: User pagination states (auto-cleaned after 10 minutes)
- **ignored_messages**: Messages that should be skipped during scanning
- **media_groups**: Tracks media groups (photo albums)
- **pending_categorization**: Products awaiting admin categorization
- **bot_users**: Tracks users who interact with the bot

### Database Persistence

⚠️ **Important**: By default, the database is stored locally and may be lost on redeploys.

For production deployments, you should:
1. **Use persistent disk storage** (Render, Docker volumes)
2. **Use PostgreSQL** for better scalability
3. **Set up regular backups**

📚 See [DATABASE_PERSISTENCE.md](DATABASE_PERSISTENCE.md) for detailed instructions on:
- Setting up persistent storage for different platforms
- Importing existing channel messages after database loss
- Migration from SQLite to PostgreSQL
- Backup strategies

To use PostgreSQL instead, modify `database.py` to use `asyncpg` or `psycopg2`.

## Troubleshooting

### Bot doesn't detect channel posts

- Verify the bot is an admin in the channel
- Check that `CHANNEL_ID` or `CHANNEL_USERNAME` is correct
- Ensure the channel is public or the bot has access
- Check bot logs for errors

### Search not working

- Make sure `rapidfuzz` is installed: `pip install rapidfuzz`
- The bot falls back to `difflib` if `rapidfuzz` is not available

### Permission errors

- Ensure the bot has "Can post messages" rights
- Check that the bot hasn't been removed from the channel

### Database errors

- Check file permissions for `catalog.db`
- Ensure the directory is writable

## License

This project is provided as-is for educational and commercial use.

## Support

For issues or questions, check the logs in the console output. The bot includes comprehensive error handling and logging.

