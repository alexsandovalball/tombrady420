# Quick Start Guide

## 1. Install Dependencies

```bash
pip install -r requirements.txt
```

## 2. Configure Environment

Create a `.env` file in the project root:

```env
BOT_TOKEN=your_bot_token_from_botfather
CHANNEL_ID=-1001234567890
ADMIN_IDS=123456789,987654321
```

**How to get these values:**

- **BOT_TOKEN**: Message [@BotFather](https://t.me/botfather) on Telegram, create a new bot, copy the token
- **CHANNEL_ID**: Forward a message from your channel to [@userinfobot](https://t.me/userinfobot) to get the channel ID (negative number)
- **ADMIN_IDS**: Message [@userinfobot](https://t.me/userinfobot) to get your user ID, add multiple IDs separated by commas

## 3. Set Up Bot in Channel

1. Add your bot to your public channel as an **Administrator**
2. Grant permissions:
   - ✅ Can post messages
3. Make sure the channel is public or the bot has access

## 4. Run the Bot

```bash
python main.py
```

The bot will:
- Create the database automatically
- Start monitoring your channel
- Begin accepting user commands

## 5. Test It

1. Post a photo/video with a caption in your channel
2. The bot should automatically save it
3. Message the bot privately: `/start`
4. Try `/menu` to see all products
5. Try searching: type "blue" or any keyword

## Troubleshooting

- **Bot doesn't detect posts**: Check that bot is admin and CHANNEL_ID is correct
- **Import errors**: Make sure all dependencies are installed: `pip install -r requirements.txt`
- **Database errors**: Check file permissions, ensure the directory is writable

