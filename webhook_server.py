"""
Webhook server for Telegram bot using FastAPI and uvicorn.
This allows the bot to receive updates via webhooks instead of polling,
which is more suitable for serverless deployments like Render.com.
"""
import logging
import os
from contextlib import asynccontextmanager
from typing import List
from fastapi import FastAPI, Request, Response, status
from telegram import Update
from telegram.ext import Application

from configs.config import Config, ConfigError
# Import the bot setup functions
from main import (
    post_init,
    setup_bot_commands,
    start_command,
    menu_command,
    language_command,
    subscribe_command,
    unsubscribe_command,
    nuke_command,
    recategorize_command,
    users_command,
    block_command,
    unblock_command,
    send_command,
    broadcast_command,
    setcontact_command,
    clearcache_command,
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
from utils.helpers import get_channel_id, get_channel_username, get_file_id_cache_size

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

# Global registry for bot applications
# This allows the notification service to access all bot instances
_bot_applications = []

def get_bot_applications() -> List[Application]:
    """Get all bot application instances."""
    return _bot_applications

def get_bot_usernames() -> List[str]:
    """Get list of bot usernames for all registered bots."""
    return [bot.bot.username for bot in _bot_applications if hasattr(bot, 'bot') and hasattr(bot.bot, 'username')]


async def setup_application(bot_token: str, bot_index: int = 0):
    """Initialize and configure a bot application.
    
    Args:
        bot_token: The Telegram bot token
        bot_index: Index of the bot (0 for primary, 1+ for additional bots)
    """
    logger.info(f"Setting up bot application {bot_index}...")
    
    # Create application
    bot_app = Application.builder().token(bot_token).build()
    
    # Initialize database (only once for the primary bot)
    if bot_index == 0:
        await post_init(bot_app)
    else:
        # For secondary bots, set commands
        await setup_bot_commands(bot_app.bot)
    
    # Register handlers
    bot_app.add_handler(CommandHandler("start", start_command))
    bot_app.add_handler(CommandHandler("menu", menu_command))
    bot_app.add_handler(CommandHandler("language", language_command))
    bot_app.add_handler(CommandHandler("nuke", nuke_command))
    bot_app.add_handler(CommandHandler("recategorize", recategorize_command))
    bot_app.add_handler(CommandHandler("users", users_command))
    bot_app.add_handler(CommandHandler("subscribe", subscribe_command))
    bot_app.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
    bot_app.add_handler(CommandHandler("block", block_command))
    bot_app.add_handler(CommandHandler("unblock", unblock_command))
    bot_app.add_handler(CommandHandler("send", send_command))
    bot_app.add_handler(CommandHandler("broadcast", broadcast_command))
    bot_app.add_handler(CommandHandler("setcontact", setcontact_command))
    bot_app.add_handler(CommandHandler("clearcache", clearcache_command))
    
    # Channel post handler (for monitoring channel)
    # Only the primary bot (bot_index == 0) should monitor the channel and send categorization requests
    if bot_index == 0:
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
            logger.info(f"Channel monitoring enabled for primary bot: {channel_id or channel_username}")
    else:
        logger.info(f"Channel monitoring DISABLED for secondary bot {bot_index} (only primary bot monitors channel)")
    
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
        # Primary bot uses /webhook, additional bots use /webhook/1, /webhook/2, etc.
        webhook_path = "/webhook" if bot_index == 0 else f"/webhook/{bot_index}"
        await bot_app.bot.set_webhook(
            url=f"{webhook_url}{webhook_path}",
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True
        )
        logger.info(f"Webhook set for bot {bot_index}: {webhook_url}{webhook_path}")
    else:
        logger.warning("WEBHOOK_URL not set. Webhook may not work properly.")
    
    logger.info(f"Bot application {bot_index} initialized successfully")
    return bot_app


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan - startup and shutdown."""
    global _bot_applications
    
    # Startup
    logger.info("Starting webhook server...")
    
    # Validate configuration on startup
    try:
        Config.validate()
    except ConfigError as e:
        logger.error(str(e))
        raise
    
    # Initialize all bot applications
    bot_apps = []
    bot_tokens = Config.BOT_TOKENS
    
    if not bot_tokens:
        logger.error("No bot tokens configured!")
        raise ConfigError("At least one bot token is required")
    
    logger.info(f"Initializing {len(bot_tokens)} bot instance(s)...")
    
    for idx, token in enumerate(bot_tokens):
        try:
            bot_app = await setup_application(token, idx)
            bot_apps.append(bot_app)
            logger.info(f"Bot {idx} initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize bot {idx}: {e}", exc_info=True)
            # Clean up any previously initialized bots
            for app in bot_apps:
                try:
                    await app.stop()
                    await app.shutdown()
                except Exception:
                    pass
            raise
    
    # Store in global registry for notification service access
    _bot_applications = bot_apps
    
    app.state.bot_apps = bot_apps
    logger.info(f"All {len(bot_apps)} bot(s) initialized successfully")
    
    # Log bot usernames for debugging
    for idx, bot_app in enumerate(bot_apps):
        if hasattr(bot_app, 'bot') and hasattr(bot_app.bot, 'username'):
            logger.info(f"  Bot {idx}: @{bot_app.bot.username}")
    
    # Log cache status for multi-bot setups
    if len(bot_apps) > 1:
        cache_size = await get_file_id_cache_size()
        if cache_size == 0:
            logger.warning(
                f"File ID cache is empty. For secondary bots (bot 1-{len(bot_apps)-1}) to display media correctly, "
                f"admins must start a conversation with each bot by sending /start. "
                f"This allows the bots to cache bot-specific file IDs."
            )
        else:
            logger.info(f"File ID cache contains {cache_size} entries")
    
    yield
    
    # Shutdown
    logger.info("Shutting down bot applications...")
    for idx, bot_app in enumerate(app.state.bot_apps):
        try:
            await bot_app.stop()
            await bot_app.shutdown()
            logger.info(f"Bot {idx} shut down successfully")
        except Exception as e:
            logger.error(f"Error shutting down bot {idx}: {e}")
    
    # Clear global registry
    _bot_applications.clear()
    logger.info("All bot applications shut down successfully")


# Create FastAPI app with lifespan
app = FastAPI(
    title="Telegram Bot Webhook Server",
    lifespan=lifespan
)


@app.get("/")
@app.head("/")
async def root():
    """Health check endpoint."""
    bot_count = len(getattr(app.state, "bot_apps", []))
    return {
        "status": "running",
        "bot": "Telegram Media Catalog Bot",
        "mode": "webhook",
        "bot_instances": bot_count
    }


@app.post("/webhook")
async def webhook(request: Request):
    """Handle incoming webhook updates from Telegram for the primary bot (bot 0)."""
    logger.info("Received webhook request for primary bot")
    
    # Check if bots are initialized
    if not hasattr(request.app.state, "bot_apps") or not request.app.state.bot_apps:
        logger.error("Bot applications not initialized")
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    
    try:
        # Parse the update
        json_data = await request.json()
        # Use the first bot (primary bot at index 0)
        bot_app = request.app.state.bot_apps[0]
        update = Update.de_json(json_data, bot_app.bot)
        
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
        await bot_app.process_update(update)
        
        logger.info("Webhook request processed successfully for primary bot")
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Error processing webhook update for primary bot: {e}", exc_info=True)
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@app.post("/webhook/{bot_index}")
async def webhook_indexed(request: Request, bot_index: int):
    """Handle incoming webhook updates from Telegram for additional bot instances.
    
    Note: The primary bot (bot 0) is accessed via /webhook, not /webhook/0.
    This endpoint handles bots 1, 2, 3, etc.
    """
    logger.info(f"Received webhook request for bot {bot_index}")
    
    # Check if bots are initialized
    if not hasattr(request.app.state, "bot_apps") or not request.app.state.bot_apps:
        logger.error("Bot applications not initialized")
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    
    # Validate bot index (must be >= 1 for this endpoint)
    if bot_index >= len(request.app.state.bot_apps):
        logger.error(f"Invalid bot index: {bot_index}. Available bots: 0-{len(request.app.state.bot_apps) - 1}")
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    
    if bot_index == 0:
        logger.warning("Bot 0 should use /webhook endpoint, not /webhook/0. Redirecting...")
        # Still process it for convenience, but log the warning
    
    try:
        # Parse the update
        json_data = await request.json()
        bot_app = request.app.state.bot_apps[bot_index]
        update = Update.de_json(json_data, bot_app.bot)
        
        # Log update details
        if update.message:
            logger.info(f"Bot {bot_index}: Processing message update from user {update.message.from_user.id}")
        elif update.callback_query:
            logger.info(f"Bot {bot_index}: Processing callback query from user {update.callback_query.from_user.id}: {update.callback_query.data}")
        elif update.channel_post:
            logger.info(f"Bot {bot_index}: Processing channel post from chat {update.channel_post.chat.id}")
        else:
            logger.info(f"Bot {bot_index}: Processing update type: {update.update_id}")
        
        # Process the update
        await bot_app.process_update(update)
        
        logger.info(f"Webhook request processed successfully for bot {bot_index}")
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Error processing webhook update for bot {bot_index}: {e}", exc_info=True)
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


@app.get("/health")
@app.head("/health")
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
