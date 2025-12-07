# webhook_server.py
import os
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
