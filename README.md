# Telegram Media Catalog Bot

A complete, production-ready Telegram bot that acts as a media product catalog. The bot automatically monitors a public channel for media posts, saves them as products, and allows users to browse and search the catalog.

## Features

- ✅ **Automatic Product Detection**: Monitors a public channel and automatically saves media posts as products
- ✅ **Full Catalog Browsing**: View all products with pagination
- ✅ **Fuzzy Search**: Natural language search with typo tolerance
- ✅ **Product Viewing**: View individual products with original media and captions
- ✅ **Admin Features**: Delete products, view statistics
- ✅ **Rate Limiting**: Prevents spam and abuse
- ✅ **Pagination**: Smart pagination for catalog and search results
- ✅ **Error Handling**: Robust error handling and logging

## Requirements

- Python 3.8+
- Telegram Bot Token (from [@BotFather](https://t.me/botfather))
- A public Telegram channel where the bot is an admin with "Can post messages" and "Can delete messages" rights

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

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

Edit `.env`:

```env
BOT_TOKEN=your_bot_token_here
CHANNEL_ID=-1001234567890
ADMIN_IDS=123456789,987654321
```

**How to get these values:**

- **BOT_TOKEN**: Create a bot with [@BotFather](https://t.me/botfather) and copy the token
- **CHANNEL_ID**: 
  - Forward a message from your channel to [@userinfobot](https://t.me/userinfobot)
  - Or use [@getidsbot](https://t.me/getidsbot)
  - Channel IDs are negative numbers (e.g., `-1001234567890`)
- **ADMIN_IDS**: 
  - Message [@userinfobot](https://t.me/userinfobot) to get your user ID
  - Add multiple IDs separated by commas (no spaces)

### 4. Set Up the Bot in Your Channel

1. Add the bot to your public channel as an **Administrator**
2. Grant the bot these permissions:
   - ✅ Can post messages
   - ✅ Can delete messages
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
- Natural language search - Just type keywords like "blue tshirt", "red shoes", etc.

### Admin Commands

- `/stats` - View catalog statistics (total products, added today)

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
   - Delete products (removes from both database and channel)
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
media-catalog-bot/
├── main.py                 # Main entry point
├── database.py             # Database models and operations
├── handlers/               # Command and message handlers
│   ├── start.py           # /start command
│   ├── menu.py            # /menu and catalog browsing
│   ├── search.py          # Search functionality
│   ├── product_view.py    # Product viewing
│   └── admin.py           # Admin commands
├── utils/                  # Utility functions
│   ├── pagination.py      # Pagination logic
│   ├── fuzzy_search.py    # Fuzzy search implementation
│   └── helpers.py         # Helper functions
├── requirements.txt        # Python dependencies
├── .env.example           # Environment variables template
└── README.md              # This file
```

## Database

The bot uses SQLite by default (stored as `catalog.db`). The database contains:

- **products**: All saved products with file_id, caption, message metadata
- **pagination_state**: User pagination states (auto-cleaned after 10 minutes)

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

- Ensure the bot has "Can post messages" and "Can delete messages" rights
- Check that the bot hasn't been removed from the channel

### Database errors

- Check file permissions for `catalog.db`
- Ensure the directory is writable

## License

This project is provided as-is for educational and commercial use.

## Support

For issues or questions, check the logs in the console output. The bot includes comprehensive error handling and logging.

