"""
Webhook server for Telegram bot using FastAPI and uvicorn.
This allows the bot to receive updates via webhooks instead of polling,
which is more suitable for serverless deployments like Render.com.
"""
import logging
import os
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, status
from telegram import Update
from telegram.ext import Application

from configs.config import Config, ConfigError
# Import the bot setup function
from main import setup_bot_application

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


async def setup_applications():
    """Initialize and configure all bot applications for webhook mode."""
    # Validate configuration on startup
    try:
        Config.validate()
    except ConfigError as e:
        logger.error(str(e))
        raise
    
    # Get all bot tokens from config
    bot_tokens = Config.BOT_TOKENS
    
    if not bot_tokens:
        raise ConfigError("No bot tokens configured. Please set BOT_TOKEN or BOT_TOKEN_1.")
    
    # Get webhook URL from config
    webhook_url = Config.WEBHOOK_URL
    if not webhook_url:
        logger.warning("WEBHOOK_URL not set. Webhook may not work properly.")
    
    # Create applications for all tokens
    bot_apps = []
    for i, bot_token in enumerate(bot_tokens, 1):
        logger.info(f"Setting up bot instance {i}...")
        
        # Add staggered delay for secondary bots to avoid race conditions
        if i > 1:
            delay = 2.5 * (i - 1)  # 2.5s, 5.0s, 7.5s, etc.
            logger.info(f"Bot instance {i}: Adding {delay}s delay before initialization to avoid conflicts")
            await asyncio.sleep(delay)
        
        # Use the setup_bot_application function from main.py
        bot_app = setup_bot_application(bot_token, bot_instance=i)
        
        # Initialize the application
        await bot_app.initialize()
        await bot_app.start()
        
        # Set webhook for this bot instance
        if webhook_url:
            webhook_endpoint = f"{webhook_url}/webhook/{i}"
            await bot_app.bot.set_webhook(
                url=webhook_endpoint,
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True
            )
            logger.info(f"Bot instance {i} webhook: {webhook_endpoint}")
        
        bot_apps.append(bot_app)
    
    logger.info(f"All {len(bot_apps)} bot instances initialized successfully")
    return bot_apps


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan - startup and shutdown."""
    # Startup
    logger.info("Starting webhook server...")
    app.state.bot_apps = await setup_applications()
    yield
    # Shutdown
    logger.info("Shutting down bot applications...")
    for i, bot_app in enumerate(app.state.bot_apps, 1):
        try:
            await bot_app.stop()
            await bot_app.shutdown()
            logger.info(f"Bot instance {i} shut down successfully")
        except Exception as e:
            logger.error(f"Error shutting down bot instance {i}: {e}")
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
    return {
        "status": "running",
        "bot": "Telegram Media Catalog Bot",
        "mode": "webhook"
    }


@app.post("/webhook/{bot_instance}")
async def webhook(request: Request, bot_instance: int):
    """Handle incoming webhook updates from Telegram for a specific bot instance."""
    logger.info(f"Received webhook request for bot instance {bot_instance}")
    
    # Check if bots are initialized
    if not hasattr(request.app.state, "bot_apps") or request.app.state.bot_apps is None:
        logger.error("Bot applications not initialized")
        return Response(status_code=status.HTTP_503_SERVICE_UNAVAILABLE)
    
    # Validate bot instance number
    if bot_instance < 1 or bot_instance > len(request.app.state.bot_apps):
        logger.error(f"Invalid bot instance: {bot_instance}")
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    
    # Get the correct bot application (convert to 0-based index)
    bot_app = request.app.state.bot_apps[bot_instance - 1]
    
    try:
        # Parse the update
        json_data = await request.json()
        update = Update.de_json(json_data, bot_app.bot)
        
        # Log update details
        if update.message:
            logger.info(f"Bot {bot_instance}: Processing message update from user {update.message.from_user.id}")
        elif update.callback_query:
            logger.info(f"Bot {bot_instance}: Processing callback query from user {update.callback_query.from_user.id}: {update.callback_query.data}")
        elif update.channel_post:
            logger.info(f"Bot {bot_instance}: Processing channel post from chat {update.channel_post.chat.id}")
        else:
            logger.info(f"Bot {bot_instance}: Processing update type: {update.update_id}")
        
        # Process the update
        await bot_app.process_update(update)
        
        logger.info(f"Bot {bot_instance}: Webhook request processed successfully")
        return Response(status_code=status.HTTP_200_OK)
    except Exception as e:
        logger.error(f"Bot {bot_instance}: Error processing webhook update: {e}", exc_info=True)
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
