# webhook_server.py
"""
ASGI entrypoint for hosting the bot behind a webhook using FastAPI + uvicorn.
This file exposes `app` (FastAPI) so you can run `uvicorn webhook_server:app`.
It starts the telegram Application on startup and stops it on shutdown.
"""

from __future__ import annotations

import os
import logging
import asyncio
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import PlainTextResponse
from telegram import Update
from telegram.ext import Application

from main import build_application, startup_tasks, shutdown_tasks
from config import USE_WEBHOOK, WEBHOOK_URL, LOG_LEVEL

logger = logging.getLogger(__name__)
logging.basicConfig(level=LOG_LEVEL)

app = FastAPI()
telegram_app: Application | None = None


@app.on_event("startup")
async def _startup():
    global telegram_app
    logger.info("ASGI startup: building Telegram Application")
    telegram_app = build_application()
    # initialize (creates DB etc)
    await telegram_app.initialize()
    await startup_tasks(telegram_app)
    # start the application (this starts internal tasks but does not block)
    await telegram_app.start()
    # If using webhook, set webhook and rely on FastAPI endpoint to receive updates
    if USE_WEBHOOK:
        if not WEBHOOK_URL:
            raise RuntimeError("USE_WEBHOOK is true but WEBHOOK_URL is not set")
        logger.info("Setting webhook to %s", WEBHOOK_URL)
        await telegram_app.bot.set_webhook(WEBHOOK_URL)
    logger.info("Telegram Application started")


@app.on_event("shutdown")
async def _shutdown():
    global telegram_app
    if telegram_app:
        logger.info("Stopping Telegram Application")
        if USE_WEBHOOK:
            try:
                await telegram_app.bot.delete_webhook()
            except Exception:
                logger.exception("Failed to delete webhook")
        await telegram_app.stop()
        await shutdown_tasks(telegram_app)
        await telegram_app.shutdown()
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
        raise HTTPException(status_code=503, detail="Bot not initialized")
    # Basic token check
    if token != telegram_app.bot.token:
        raise HTTPException(status_code=403, detail="Invalid token")
    body = await request.body()
    try:
        update = Update.de_json(await request.json(), telegram_app.bot)
    except Exception as e:
        logger.exception("Failed to parse update: %s", e)
        raise HTTPException(status_code=400, detail="Invalid update payload")
    # Process update asynchronously
    await telegram_app.update_queue.put(update)
    return PlainTextResponse("OK")


@app.get("/health")
async def health():
    return {"status": "ok"}