"""
Optimized webhook server for Telegram bot.
Uses FastAPI to receive webhook updates and process them through the telegram Application.
"""
import json
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from telegram import Update

from main import application

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# Reduce noisy library logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Manage application lifespan: startup and shutdown.
    Initialize and start the telegram Application on startup.
    Stop and shutdown the telegram Application on shutdown.
    """
    # Startup
    try:
        logger.info("Initializing telegram Application...")
        await application.initialize()
        await application.start()
        logger.info("Telegram Application started successfully")
    except Exception as e:
        logger.exception("Failed to start telegram Application: %s", e)
        raise
    
    yield
    
    # Shutdown
    try:
        logger.info("Shutting down telegram Application...")
        await application.stop()
        await application.shutdown()
        logger.info("Telegram Application stopped successfully")
    except Exception as e:
        logger.exception("Failed to stop telegram Application: %s", e)


app = FastAPI(title="Telegram Bot Webhook", lifespan=lifespan)


@app.get("/")
async def root():
    """Health check endpoint."""
    return {"status": "ok", "message": "Telegram bot webhook server is running"}


@app.post("/webhook")
async def webhook(request: Request):
    """
    Process incoming webhook updates from Telegram.
    Optimized for fast processing and proper error handling.
    """
    try:
        # Read and parse request body
        body = await request.body()
        if not body:
            logger.warning("Received empty webhook request")
            raise HTTPException(status_code=400, detail="Empty request body")
        
        # Parse JSON
        try:
            data = json.loads(body)
        except json.JSONDecodeError as e:
            logger.error("Invalid JSON in webhook request: %s", e)
            raise HTTPException(status_code=400, detail="Invalid JSON")
        
        # Log update info
        update_id = data.get("update_id", "unknown")
        logger.info("Processing webhook update_id=%s", update_id)
        
        # Convert to Telegram Update object
        try:
            update = Update.de_json(data, application.bot)
        except Exception as e:
            logger.error("Failed to parse Telegram update: %s", e)
            raise HTTPException(status_code=400, detail="Invalid update format")
        
        if not update:
            logger.warning("Parsed update is None for update_id=%s", update_id)
            raise HTTPException(status_code=400, detail="Failed to parse update")
        
        # Queue the update for processing
        await application.update_queue.put(update)
        logger.debug("Update %s queued successfully", update_id)
        
        # Return 200 OK immediately
        return {"ok": True}
        
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Unexpected error processing webhook: %s", e)
        raise HTTPException(status_code=500, detail="Internal server error")
