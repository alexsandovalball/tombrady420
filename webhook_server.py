# webhook_server.py
import os
from fastapi import FastAPI, Request, Response
from telegram import Update
from main import application  # imports the built Application object

app = FastAPI()

@app.get("/")
async def root():
    return {"ok": True}

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    # Convert JSON to Update and push into the application's queue
    update = Update.de_json(data, application.bot)
    await application.update_queue.put(update)
    return Response(status_code=200)
