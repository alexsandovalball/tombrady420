# Telegram Media Catalog Bot

A production-ready Telegram bot for managing and browsing a media product catalog with multi-language support and web interface.

## Quick Start

### Prerequisites
- Python 3.8+
- Telegram Bot Token from [@BotFather](https://t.me/botfather)
- Telegram channel (bot must be admin)

### Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp configs/.env.example .env
# Edit .env with your BOT_TOKEN, CHANNEL_ID, ADMIN_IDS

# Run
python main.py
```

## Key Features

- 🌐 Multi-language support (6 languages)
- 🔍 Fuzzy search with typo tolerance
- 📂 Category/subcategory organization
- 🔔 Smart notifications
- 📱 Web app interface
- 🤖 Multi-bot support

## Documentation

- **[Admin Guide](docs/ADMIN_GUIDE.md)** - Admin features and commands
- **[User Guide](docs/USER_GUIDE.md)** - User instructions
- **[Configuration](configs/.env.example)** - All environment variables

## Deployment

### Render.com (Recommended)
1. Create new Web Service
2. Connect repository
3. Set environment variables
4. Deploy with webhook mode

See `configs/.env.example` for all configuration options.

## Project Structure

```
├── main.py              # Bot entry point
├── webhook_server.py    # FastAPI webhook server
├── database.py          # Database operations
├── handlers/            # Command handlers
├── utils/               # Helper utilities
├── translations/        # Multi-language support
├── webapp/              # Web interface
└── configs/             # Configuration files
```

## License

MIT License - See LICENSE file for details
