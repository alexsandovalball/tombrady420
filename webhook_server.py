"""
ASGI webhook server for receiving Telegram updates.
Uses FastAPI for async request handling.
"""

import logging
import asyncio
from fastapi import FastAPI, Request, Response
from http import HTTPStatus
from telegram import Update
from telegram.ext import Application
from config.config import BOT_TOKEN, WEBHOOK_URL, WEBHOOK_PORT, WEBHOOK_LISTEN
from main import startup_checks

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()
application: Application = None

@app.on_event("startup")
async def on_startup():
    global application
    await startup_checks()
    application = Application.builder().token(BOT_TOKEN).updater(None).build()
    # Register handlers as in main.py
    # ...
    await application.bot.set_webhook(url=WEBHOOK_URL)
    await application.start()
    logger.info("Webhook server started.")

@app.on_event("shutdown")
async def on_shutdown():
    if application:
        await application.stop()
        logger.info("Webhook server stopped.")

@app.post("/telegram")
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, application.bot)
    await application.process_update(update)
    return Response(status_code=HTTPStatus.OK)

@app.get("/health")
async def health_check():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("webhook_server:app", host=WEBHOOK_LISTEN, port=WEBHOOK_PORT, reload=True)