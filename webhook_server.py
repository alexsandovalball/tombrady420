"""
Webhook server for Telegram bot using FastAPI and uvicorn.
This allows the bot to receive updates via webhooks instead of polling,
which is more suitable for serverless deployments like Render.com.
"""
import os
import logging
from fastapi import FastAPI, Request, Response, status
from telegram import Update
from telegram.ext import Application
from dotenv import load_dotenv

# Import the bot setup functions
from main import (
    post_init,
    start_command,
    menu_command,
    stats_command,
    nuke_command,
    channel_post_handler,
    callback_query_handler,
    message_handler,
    error_handler
)
from telegram.ext import (
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters
)
from utils.helpers import get_channel_id, get_channel_username

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Create FastAPI app
app = FastAPI(title="Telegram Bot Webhook Server")

# Global variable to hold the bot application
bot_app: Application = None


async def setup_application():
    """Initialize and configure the bot application."""
    global bot_app
    
    # Get bot token
    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise ValueError("BOT_TOKEN not found in environment variables!")
    
    # Create application
    bot_app = Application.builder().token(bot_token).build()
    
    # Initialize database
    await post_init(bot_app)
    
    # Register handlers
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(CommandHandler("menu", menu_command))
    bot_app.add_handler(CommandHandler("stats", stats_command))
    bot_app.add_handler(CommandHandler("nuke", nuke_command))
    
    # Channel post handler (for monitoring channel)
    channel_id = get_channel_id()
    channel_username = get_channel_username()
    
    if channel_id:
        # Use channel ID filter
        channel_filter = filters.Chat(chat_id=channel_id)
    elif channel_username:
        # Use channel username filter
        channel_filter = filters.Chat(username=channel_username.lstrip("@"))
    else:
        logger.warning("No CHANNEL_ID or CHANNEL_USERNAME set. Channel monitoring disabled.")
        channel_filter = None
    
    if channel_filter:
        bot_app.add_handler(
            MessageHandler(
                channel_filter & filters.ChatType.CHANNEL,
                channel_post_handler
            )
        )
        logger.info(f"Channel monitoring enabled for: {channel_id or channel_username}")
    
    # Callback query handler (for inline buttons)
    bot_app.add_handler(CallbackQueryHandler(callback_query_handler))
    
    # Message handler (for search queries)
    bot_app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.ChatType.PRIVATE,
            message_handler
        )
    )
    
    # Error handler
    bot_app.add_error_handler(error_handler)
    
    # Initialize the application
    await bot_app.initialize()
    await bot_app.start()
    
    # Set webhook
    webhook_url = os.getenv("WEBHOOK_URL")
    if webhook_url:
        await bot_app.bot.set_webhook(
            url=f"{webhook_url}/webhook",
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        logger.info(f"Webhook set to: {webhook_url}/webhook")
    else:
        logger.warning("WEBHOOK_URL not set. Webhook may not work properly.")
    
    logger.info("Bot application initialized successfully")
    return bot_app


@app.on_event("startup")
async def startup_event():
    """Initialize bot on server startup."""
    logger.info("Starting webhook server...")
    await setup_application()


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup on server shutdown."""
    global bot_app
    if bot_app:
        logger.info("Shutting down bot application...")
        await bot_app.stop()
        await bot_app.shutdown()
        logger.info("Bot application shut down successfully")


@app.get("/")
async def root():
    """Health check endpoint."""
    return {
        "status": "running",
        "bot": "Telegram Media Catalog Bot",
        "mode": "webhook"
    }


@app.post("/webhook")
async def webhook(request: Request):
    """Handle incoming webhook updates from Telegram."""
    try:
        # Parse the update
        json_data = await request.json()
        update = Update.de_json(json_data, bot_app.bot)
        
        # Process the update
        await bot_app.process_update(update)
        
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Error processing webhook update: {e}", exc_info=True)
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@app.get("/health")
async def health():
    """Health check endpoint for deployment platforms."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    
    # Get port from environment or default to 8000
    port = int(os.getenv("PORT", "8000"))
    
    # Run the server
    uvicorn.run(
        "webhook_server:app",
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
