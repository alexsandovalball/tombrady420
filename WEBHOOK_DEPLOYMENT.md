# Webhook Deployment Guide

This bot now supports both **polling mode** (for local development) and **webhook mode** (for production deployment on platforms like Render.com).

## Webhook Mode (Production)

### Environment Variables

Add these environment variables to your deployment platform:

```env
BOT_TOKEN=your_bot_token_here
CHANNEL_ID=-your channel ID here
ADMIN_IDS=Admin IDs here
USE_WEBHOOK=true
WEBHOOK_URL=https://your-app.onrender.com
PORT=8000
```

### Deployment on Render.com

1. Create a new Web Service on Render.com
2. Connect your GitHub repository
3. Configure the service:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn webhook_server:app --host 0.0.0.0 --port $PORT`
4. Add environment variables (see above)
5. Deploy!

The bot will automatically set up the webhook URL when the server starts.

### Running Locally (Webhook Mode)

```bash
# Set environment variables
export BOT_TOKEN="your_token"
export WEBHOOK_URL="https://your-ngrok-url.ngrok.io"
export USE_WEBHOOK="true"

# Run the webhook server
python webhook_server.py
# Or use uvicorn directly:
uvicorn webhook_server:app --host 0.0.0.0 --port 8000 --reload
```

For local testing with webhooks, you'll need a tool like [ngrok](https://ngrok.com/) to expose your local server to the internet.

## Polling Mode (Local Development)

For local development, polling mode is simpler as it doesn't require webhook setup:

```bash
# Don't set USE_WEBHOOK or set it to false
export USE_WEBHOOK="false"
export BOT_TOKEN="your_token"

# Run in polling mode
python main.py
```

## API Endpoints

When running in webhook mode, these endpoints are available:

- `GET /` - Health check and bot status
- `POST /webhook` - Telegram webhook endpoint (automatically configured)
- `GET /health` - Health check for deployment platforms

## Troubleshooting

### Webhook not receiving updates

1. Check that `WEBHOOK_URL` is set correctly (must be HTTPS)
2. Verify the webhook is set: check Telegram API with `getWebhookInfo`
3. Check server logs for errors

### Port binding issues

Make sure the `PORT` environment variable is set. Render.com provides this automatically.

### Import errors

Ensure all dependencies are installed:
```bash
pip install -r requirements.txt
```

## Switching Between Modes

Simply change the `USE_WEBHOOK` environment variable:
- `USE_WEBHOOK=true` → Webhook mode (use `webhook_server.py`)
- `USE_WEBHOOK=false` or unset → Polling mode (use `main.py`)

The bot will automatically use the appropriate mode.
