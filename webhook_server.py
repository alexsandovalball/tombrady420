# webhook_server.py
"""
ASGI entrypoint for hosting the bot behind a webhook using FastAPI + uvicorn.
Exposes `app` (FastAPI) so you can run `uvicorn webhook_server:app`.
Starts the telegram Application on startup and stops it on shutdown.
"""

from __future__ import annotations

# Ensure stdout logging is configured early and consistently for Render
import logging as _logging
import sys as _sys

_root_logger = _logging.getLogger()
if not _root_logger.handlers:
    _handler = _logging.StreamHandler(stream=_sys.stdout)
    _formatter = _logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    _handler.setFormatter(_formatter)
    _root_logger.addHandler(_handler)
_root_logger.setLevel(_logging.INFO)
for _name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
    _lg = _logging.getLogger(_name)
    _lg.propagate = True
    if _lg.level == _logging.NOTSET:
        _lg.setLevel(_logging.INFO)

import json
import logging
import os
from typing import Optional

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import PlainTextResponse
from telegram import Update
from telegram.ext import Application

from main import build_application, startup_tasks, shutdown_tasks
from config import USE_WEBHOOK, WEBHOOK_URL, LOG_LEVEL

# Optional env var to allow permissive token checking while debugging.
# Set ALLOW_INSECURE_WEBHOOK=true only temporarily for debugging.
_ALLOW_INSECURE = os.getenv("ALLOW_INSECURE_WEBHOOK", "false").lower() in ("1", "true", "yes")

# Configure module logger (root handler already configured above)
logger = logging.getLogger(__name__)

app = FastAPI()
telegram_app: Optional[Application] = None


def _mask_token(token: Optional[str]) -> str:
    """Return a masked representation of a token for safe logging."""
    if not token or len(token) < 8:
        return "REDACTED"
    return token[:4] + "…" + token[-4:]


@app.on_event("startup")
async def _startup():
    global telegram_app
    logger.info("ASGI startup: building Telegram Application")
    telegram_app = build_application()

    # Initialize application (creates DB etc)
    try:
        await telegram_app.initialize()
        await startup_tasks(telegram_app)
        await telegram_app.start()
    except Exception:
        logger.exception("Failed to initialize/start Telegram Application")
        # If initialization fails, ensure we don't leave a partially started app
        try:
            # attempt graceful shutdown if partially started
            if telegram_app:
                await telegram_app.shutdown()
        except Exception:
            logger.exception("Failed to shutdown partially initialized Telegram Application")
        # attempt to close DB connection if present
        try:
            from database import db as database_instance  # local import to avoid circulars
            await database_instance.close()
        except Exception:
            logger.debug("No DB connection to close or failed to close DB during startup failure")
        telegram_app = None
        raise

    # If using webhook, set webhook and rely on FastAPI endpoint to receive updates
    if USE_WEBHOOK:
        if not WEBHOOK_URL:
            raise RuntimeError("USE_WEBHOOK is true but WEBHOOK_URL is not set")
        # Ensure webhook URL includes the token path so Telegram posts to /webhook/{token}
        try:
            bot_token = telegram_app.bot.token
            if bot_token is None:
                raise RuntimeError("Bot token is not available on telegram_app.bot")
            # If WEBHOOK_URL already contains the token path, use it as-is; otherwise append
            if f"/webhook/{bot_token}" in WEBHOOK_URL:
                webhook_target = WEBHOOK_URL
            else:
                webhook_target = WEBHOOK_URL.rstrip("/") + f"/webhook/{bot_token}"
            logger.info("Setting webhook to %s", webhook_target)
            await telegram_app.bot.set_webhook(webhook_target)
        except Exception:
            logger.exception("Failed to set webhook")
            # Do not raise here; allow the app to run so you can inspect logs and retry
    logger.info("Telegram Application started")


@app.on_event("shutdown")
async def _shutdown():
    global telegram_app
    if not telegram_app:
        logger.info("Telegram Application not initialized; shutdown complete")
        # still attempt to close DB if present
        try:
            from database import db as database_instance
            await database_instance.close()
        except Exception:
            logger.debug("No DB connection to close on shutdown")
        return

    logger.info("Stopping Telegram Application")
    if USE_WEBHOOK:
        try:
            await telegram_app.bot.delete_webhook()
            logger.info("Webhook deleted")
        except Exception:
            logger.exception("Failed to delete webhook")

    try:
        await telegram_app.stop()
    except Exception:
        logger.exception("Error while stopping telegram_app")

    try:
        await shutdown_tasks(telegram_app)
    except Exception:
        logger.exception("shutdown_tasks failed")

    try:
        await telegram_app.shutdown()
    except Exception:
        logger.exception("Error while shutting down telegram_app")

    # Ensure DB connection closed
    try:
        from database import db as database_instance
        await database_instance.close()
        logger.info("Database connection closed")
    except Exception:
        logger.exception("Failed to close DB connection on shutdown")

    telegram_app = None
    logger.info("ASGI shutdown complete")


@app.post("/webhook/{token}")
async def telegram_webhook(token: str, request: Request) -> Response:
    """
    Endpoint to receive Telegram updates when using webhook mode.
    The path includes the bot token for a minimal auth check.
    """
    global telegram_app
    if not telegram_app:
        logger.warning("Received webhook but telegram_app is not initialized")
        raise HTTPException(status_code=503, detail="Bot not initialized")

    # Basic token check (can be temporarily bypassed with ALLOW_INSECURE_WEBHOOK)
    bot_token = getattr(telegram_app.bot, "token", None)
    if not isinstance(bot_token, str):
        logger.error("telegram_app.bot.token is not a string (value=%r)", bot_token)
        raise HTTPException(status_code=500, detail="Server misconfiguration")

    # Read request body robustly: prefer JSON parsing but fall back to raw bytes
    try:
        # Try fast path: if content-type is JSON, use request.json()
        content_type = request.headers.get("content-type", "")
        if "application/json" in content_type.lower():
            payload = await request.json()
            body_text = json.dumps(payload)
        else:
            body_bytes = await request.body()
            body_text = body_bytes.decode("utf-8", errors="replace")
            try:
                payload = json.loads(body_text)
            except Exception:
                payload = None
    except Exception as e:
        logger.exception("Failed to read request body: %s", e)
        raise HTTPException(status_code=400, detail="Failed to read request body")

    # Log a masked token and a short preview of the body for debugging
    try:
        preview = (body_text[:2000] + "...") if len(body_text) > 2000 else body_text
        logger.info("WEBHOOK RAW BODY token_path=%s bot=%s body_preview=%s", _mask_token(token), _mask_token(bot_token), preview)
    except Exception:
        logger.debug("Failed to log webhook raw body preview")

    # Token enforcement
    if token != bot_token:
        if _ALLOW_INSECURE:
            logger.warning(
                "Webhook token mismatch (path=%s bot=%s) but ALLOW_INSECURE_WEBHOOK is enabled; continuing for debug",
                _mask_token(token),
                _mask_token(bot_token),
            )
        else:
            logger.warning("Invalid token on webhook path: %s", _mask_token(token))
            raise HTTPException(status_code=403, detail="Invalid token")

    # Parse update JSON once (use payload if available, otherwise try to parse body_text)
    if payload is None:
        try:
            payload = json.loads(body_text)
        except Exception as e:
            logger.exception("Failed to parse JSON body: %s", e)
            raise HTTPException(status_code=400, detail="Invalid JSON payload")

    try:
        update = Update.de_json(payload, telegram_app.bot)
    except Exception as e:
        logger.exception("Failed to parse update into Update object: %s", e)
        raise HTTPException(status_code=400, detail="Invalid update payload")

    # Log a compact representation for debugging
    try:
        if getattr(update, "channel_post", None) is not None:
            summary = f"channel_post id={getattr(update.channel_post, 'message_id', None)} chat={getattr(update.channel_post, 'chat', None)}"
        else:
            summary = f"message id={getattr(update.message, 'message_id', None)} chat={getattr(update.message, 'chat', None)}"
        logger.debug("WEBHOOK RECEIVED: update_id=%s summary=%s", getattr(update, "update_id", None), summary)
    except Exception:
        logger.debug("WEBHOOK RECEIVED: could not extract summary")

    # Enqueue update for the Application to process
    try:
        if getattr(telegram_app, "update_queue", None) is None:
            logger.error("telegram_app.update_queue is not available; ensure application.start() was called")
            # Return 200 to avoid Telegram retry storms but log the misconfiguration
            return PlainTextResponse("OK", status_code=200)

        await telegram_app.update_queue.put(update)
        logger.debug("Enqueued update_id=%s", getattr(update, "update_id", None))
    except Exception:
        logger.exception("Failed to enqueue update")
        # Return 200 to avoid Telegram retry storms, but log the failure
        return PlainTextResponse("OK", status_code=200)

    return PlainTextResponse("OK", status_code=200)


@app.get("/log-test")
async def log_test():
    """
    Lightweight endpoint to verify that stdout logging is captured by the host.
    Call this endpoint and check your platform logs for the two messages below.
    """
    test_logger = logging.getLogger("log_test")
    test_logger.info("log-test endpoint invoked")
    test_logger.debug("log-test debug detail")
    return PlainTextResponse("log test ok", status_code=200)


@app.get("/health")
async def health():
    return {"status": "ok"}