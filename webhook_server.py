# webhook_server.py
import json
import logging
from fastapi import FastAPI, Request, Response

from telegram import Update
from main import application  # imports the built Application object

# Logging setup
logger = logging.getLogger("webhook")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)

app = FastAPI()

@app.on_event("startup")
async def startup_event():
    """
    Ensure the telegram Application is initialized and started when FastAPI starts.
    This lets the Application consume application.update_queue and run handlers.
    """
    try:
        logger.info("Starting telegram Application...")
        await application.initialize()
        await application.start()
        logger.info("Telegram Application started")
    except Exception:
        logger.exception("Failed to start telegram Application")
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
    try:
        # Read raw body and log it
        body_bytes = await request.body()
        body_text = body_bytes.decode("utf-8")
        logger.info("WEBHOOK RECEIVED: %s", body_text)

        # Parse JSON and convert to Update
        data = json.loads(body_text)
        update = Update.de_json(data, application.bot)

        # Put update into the application's queue
        await application.update_queue.put(update)
        logger.info("Update queued: update_id=%s", data.get("update_id"))

        return Response(status_code=200)
    except Exception:
        logger.exception("WEBHOOK HANDLER ERROR")
        raise
