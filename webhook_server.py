"""
ASGI entrypoint for hosting the Telegram bot application under FastAPI.
Initializes the database and starts/stops the telegram Application during FastAPI lifecycle events.
"""

import json
import logging
import asyncio
from fastapi import FastAPI, Request, Response

from main import application  # imports the built Application object built by main.build_application()
from database import init_db, DB_PATH  # async convenience wrapper that delegates to db.init_db()

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

        # Non-blocking startup DB count diagnostic (safe)
        async def _log_startup_db_count():
            try:
                import aiosqlite
                async with aiosqlite.connect(DB_PATH) as _conn:
                    _conn.row_factory = aiosqlite.Row
                    async with _conn.execute("SELECT COUNT(*) AS cnt FROM products") as _cur:
                        _r = await _cur.fetchone()
                        try:
                            cnt = int(_r["cnt"]) if _r and "cnt" in _r.keys() else "unknown"
                        except Exception:
                            cnt = "unknown"
                        logger.info("Startup DB product count: %s", cnt)
            except Exception:
                logger.exception("Failed to read product count at startup")

        try:
            asyncio.create_task(_log_startup_db_count())
        except Exception:
            logger.exception("Failed to schedule startup DB count task")

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
    Returns 200 on success, 400 for bad payload, 500 for internal errors.
    """
    try:
        body_bytes = await request.body()
        try:
            body_text = body_bytes.decode("utf-8")
        except Exception:
            # Fallback: try direct json load from bytes
            body_text = None

        if body_text:
            logger.info("WEBHOOK RECEIVED: %s", body_text)
            try:
                data = json.loads(body_text)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON payload received on /webhook")
                return Response(status_code=400, content="Invalid JSON")
        else:
            # If decode failed, attempt to parse bytes directly
            try:
                data = json.loads(body_bytes)
            except Exception:
                logger.warning("Invalid JSON payload received on /webhook (bytes parse failed)")
                return Response(status_code=400, content="Invalid JSON")

        # Convert to Update using the application's bot instance if possible
        update = None
        try:
            bot = getattr(application, "bot", None)
            if bot and hasattr(bot, "update_factory"):
                update = bot.update_factory(data)
        except Exception:
            logger.exception("update_factory conversion failed; will try fallback")

        if update is None:
            # Fallback to telegram.Update.de_json for older versions
            try:
                from telegram import Update as _Update  # local import to avoid import-time side effects
                update = _Update.de_json(data, getattr(application, "bot", None))
            except Exception:
                logger.exception("Failed to construct Update from payload")
                return Response(status_code=500, content="Failed to construct Update")

        # Ensure application has an update_queue to accept updates
        if not hasattr(application, "update_queue") or application.update_queue is None:
            logger.error("Application has no update_queue; cannot enqueue update")
            return Response(status_code=500, content="Server misconfiguration")

        await application.update_queue.put(update)
        logger.info("Update queued: update_id=%s", data.get("update_id"))
        return Response(status_code=200)
    except Exception:
        logger.exception("WEBHOOK HANDLER ERROR")
        # Return 500 rather than re-raising to avoid crashing the ASGI worker on single bad update
        return Response(status_code=500, content="Internal server error")