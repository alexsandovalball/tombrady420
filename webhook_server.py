# webhook_server.py
"""
ASGI entrypoint for hosting the Telegram bot application under FastAPI.
Initializes the database and starts/stops the telegram Application during FastAPI lifecycle events.
"""

import json
import logging
from fastapi import FastAPI, Request, Response

from main import application  # imports the built Application object built by main.build_application()
from database import init_db  # async convenience wrapper that delegates to db.init_db()

# Logging setup for this module
logger = logging.getLogger("webhook")
logger.setLevel(logging.INFO)
logger.propagate = False
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

# Reduce noisy library logs
logging.getLogger("httpx").setLevel(logging.WARNING)

app = FastAPI()


@app.on_event("startup")
async def startup_event():
    """
    Initialize the database schema, then initialize and start the telegram Application.
    This runs inside FastAPI's event loop.
    """
    try:
        logger.info("Initializing database...")
        await init_db()
        logger.info("Database initialized")

        logger.info("Initializing telegram Application...")
        # initialize() prepares internal resources; start() begins background tasks
        await application.initialize()
        await application.start()
        logger.info("Telegram Application started")
    except Exception:
        logger.exception("Failed to initialize DB or start telegram Application")
        raise


@app.on_event("shutdown")
async def shutdown_event():
    """
    Stop and shutdown the telegram Application cleanly when FastAPI shuts down.
    """
    try:
        logger.info("Stopping telegram Application...")
        await application.stop()
        await application.shutdown()
        logger.info("Telegram Application stopped")
    except Exception:
        logger.exception("Failed to stop telegram Application")
        raise


@app.get("/")
async def root():
    return {"ok": True}


@app.post("/webhook")
async def webhook(request: Request):
    """
    Accept Telegram webhook POSTs, convert JSON to Update, and enqueue into the Application.
    """
    try:
        body_bytes = await request.body()
        body_text = body_bytes.decode("utf-8")
        logger.info("WEBHOOK RECEIVED: %s", body_text)

        data = json.loads(body_text)
        # Convert to Update using the application's bot instance
        update = application.bot.update_factory(data) if hasattr(application.bot, "update_factory") else None
        if update is None:
            # Fallback to telegram.Update.de_json for older versions
            from telegram import Update as _Update  # local import to avoid import-time side effects
            update = _Update.de_json(data, application.bot)

        await application.update_queue.put(update)
        logger.info("Update queued: update_id=%s", data.get("update_id"))
        return Response(status_code=200)
    except Exception:
        logger.exception("WEBHOOK HANDLER ERROR")
        raise