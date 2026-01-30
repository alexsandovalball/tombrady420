"""
Webhook server for Telegram bot using FastAPI and uvicorn.
This allows the bot to receive updates via webhooks instead of polling,
which is more suitable for serverless deployments like Render.com.
"""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, status
from telegram import Update
from telegram.ext import Application

from config import Config, ConfigError
# Import the bot setup functions
from main import (
    post_init,
    start_command,
    menu_command,
    stats_command,
    nuke_command,
    recategorize_command,
    users_command,
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

# Configure logging with structured format for better visibility on Render
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - [%(funcName)s:%(lineno)d] - %(message)s',
    level=logging.INFO,
    force=True  # Ensure this configuration takes precedence
)
logger = logging.getLogger(__name__)

# Ensure all loggers use INFO level
logging.getLogger('telegram').setLevel(logging.INFO)
logging.getLogger('httpx').setLevel(logging.WARNING)  # Reduce noise from HTTP library
logging.getLogger('uvicorn.access').setLevel(logging.INFO)  # Keep access logs visible


async def setup_application():
    """Initialize and configure the bot application."""
    # Validate configuration on startup
    try:
        Config.validate()
    except ConfigError as e:
        logger.error(str(e))
        raise
    
    # Get bot token from config
    bot_token = Config.BOT_TOKEN
    
    # Create application
    bot_app = Application.builder().token(bot_token).build()
    
    # Initialize database
    await post_init(bot_app)
    
    # Register handlers
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(CommandHandler("menu", menu_command))
    bot_app.add_handler(CommandHandler("stats", stats_command))
    bot_app.add_handler(CommandHandler("nuke", nuke_command))
    bot_app.add_handler(CommandHandler("recategorize", recategorize_command))
    bot_app.add_handler(CommandHandler("users", users_command))
    
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
    webhook_url = Config.WEBHOOK_URL
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan - startup and shutdown."""
    # Startup
    logger.info("Starting webhook server...")
    app.state.bot_app = await setup_application()
    yield
    # Shutdown
    logger.info("Shutting down bot application...")
    await app.state.bot_app.stop()
    await app.state.bot_app.shutdown()
    logger.info("Bot application shut down successfully")


# Create FastAPI app with lifespan
app = FastAPI(
    title="Telegram Bot Webhook Server",
    lifespan=lifespan
)


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
    logger.info("Received webhook request")
    
    # Check if bot is initialized
    if not hasattr(request.app.state, "bot_app") or request.app.state.bot_app is None:
        logger.error("Bot application not initialized")
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    
    try:
        # Parse the update
        json_data = await request.json()
        update = Update.de_json(json_data, request.app.state.bot_app.bot)
        
        # Log update details
        if update.message:
            logger.info(f"Processing message update from user {update.message.from_user.id}")
        elif update.callback_query:
            logger.info(f"Processing callback query from user {update.callback_query.from_user.id}: {update.callback_query.data}")
        elif update.channel_post:
            logger.info(f"Processing channel post from chat {update.channel_post.chat.id}")
        else:
            logger.info(f"Processing update type: {update.update_id}")
        
        # Process the update
        await request.app.state.bot_app.process_update(update)
        
        logger.info("Webhook request processed successfully")
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
