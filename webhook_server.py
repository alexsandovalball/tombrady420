"""
ASGI entrypoint for hosting the Telegram bot application under FastAPI.
Initializes the database and starts/stops the telegram Application during FastAPI lifecycle events.
"""

import os
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

# Diagnostic toggle: set INCOMING_DIAG=1 to enable, 0 to disable
_INCOMING_DIAG_ENABLED = os.getenv("INCOMING_DIAG", "1").strip() not in ("0", "false", "False", "")

if _INCOMING_DIAG_ENABLED:
    _diag_logger = logging.getLogger("webhook_incoming_diag")
    _diag_logger.setLevel(logging.INFO)
    if not _diag_logger.handlers:
        _h = logging.StreamHandler()
        _h.setFormatter(logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s"))
        _diag_logger.addHandler(_h)
else:
    _diag_logger = None


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


# Base webhook handler: converts payload to Update and enqueues it
async def _base_webhook(request: Request) -> Response:
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
            logger.debug("WEBHOOK RECEIVED (text payload)")
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


# Optional diagnostic wrapper that logs top-level keys and finds media_group_id/caption
async def _diagnostic_wrapper(request: Request) -> Response:
    try:
        body_bytes = await request.body()
        try:
            body_text = body_bytes.decode("utf-8")
        except Exception:
            body_text = None

        if body_text:
            try:
                _data = json.loads(body_text)
            except Exception:
                _data = None
        else:
            try:
                _data = json.loads(body_bytes)
            except Exception:
                _data = None

        # Log top-level keys and any media_group_id or caption present
        try:
            if isinstance(_data, dict) and _diag_logger:
                keys = list(_data.keys())
                _diag_logger.info("INCOMING UPDATE keys=%s", keys)

                def _find(d, path=""):
                    if isinstance(d, dict):
                        for k, v in d.items():
                            if k == "media_group_id":
                                _diag_logger.info("FOUND media_group_id=%s at %s", v, path + "/" + k)
                            if k == "caption":
                                _diag_logger.info(
                                    "FOUND caption (len=%s) at %s",
                                    len(str(v)) if v is not None else 0,
                                    path + "/" + k,
                                )
                            _find(v, path + "/" + k)
                    elif isinstance(d, list):
                        for i, item in enumerate(d):
                            _find(item, path + f"[{i}]")

                _find(_data)
        except Exception:
            if _diag_logger:
                _diag_logger.exception("Diagnostic parse failed")
    except Exception:
        # Diagnostic must never block the main handler
        if _diag_logger:
            _diag_logger.exception("Diagnostic wrapper failed to parse request")
    # Call base handler to perform normal processing
    return await _base_webhook(request)


# Expose the webhook endpoint: use diagnostic wrapper if enabled
if _INCOMING_DIAG_ENABLED:
    webhook = _diagnostic_wrapper
else:
    webhook = _base_webhook

# Register the webhook endpoint with FastAPI
app.post("/webhook")(webhook)