"""
Database-backed broadcast forwarding (no Redis).

This module provides cross-instance broadcast support by storing broadcast events
in the SQLite database and having each bot instance poll for unprocessed events.

Key features:
- No external dependencies (Redis-free)
- Works with shared SQLite database
- Each bot instance polls for its own unprocessed broadcasts
- Automatic deduplication via broadcast_processed table
"""
import asyncio
import inspect
import logging
import json
from typing import Callable, Awaitable, Optional, Any, Dict

from database import Database
from configs.config import Config
from utils.bot_key import get_bot_env_key

logger = logging.getLogger(__name__)

# Configurable polling interval in seconds
# Trade-offs:
# - Lower values (1-2s): More responsive broadcasts, higher DB load
# - Higher values (5-10s): Lower DB load, delayed broadcast delivery
# - Default (3s): Balanced approach for most use cases
POLL_INTERVAL_SECONDS = 3


async def publish_broadcast(db: Database, message_text: str, payload: Optional[Dict[str, Any]] = None) -> int:
    """
    Publish a broadcast event to the database.
    
    This inserts a new broadcast event that will be picked up by all bot instances
    (including this one) via their polling listeners.
    
    Args:
        db: Database instance
        message_text: The broadcast message text
        payload: Optional dictionary with additional data (will be JSON-serialized)
    
    Returns:
        The ID of the newly created broadcast event
    
    Example:
        >>> broadcast_id = await publish_broadcast(db, "System maintenance in 1 hour", 
        ...                                        {"priority": "high"})
    """
    payload_json = json.dumps(payload) if payload else None
    
    try:
        broadcast_id = await db.insert_broadcast_event(message_text, payload_json)
        logger.info(f"[BroadcastForwarder] Published broadcast event {broadcast_id}: {message_text[:50]}...")
        return broadcast_id
    except Exception as e:
        logger.error(f"[BroadcastForwarder] Failed to publish broadcast: {e}", exc_info=True)
        raise


def start_listener_in_background(db: Database, enqueue_callback: Callable[[dict, str], Awaitable[None]]) -> asyncio.Task:
    """
    Start a background task that polls for unprocessed broadcast events.
    
    The listener will continuously poll the database for broadcasts that this bot
    instance has not yet processed, and call the enqueue_callback for each.
    
    Args:
        db: Database instance
        enqueue_callback: Async function to call for each broadcast.
                         Signature: async def callback(context: dict, message_text: str)
                         where context contains the deserialized payload (or empty dict)
    
    Returns:
        The asyncio.Task running the listener loop
    
    Example:
        >>> async def handle_broadcast(context, message_text):
        ...     print(f"Received: {message_text}")
        >>> task = start_listener_in_background(db, handle_broadcast)
    """
    bot_key = get_bot_env_key()
    logger.info(f"[BroadcastForwarder] Starting broadcast listener for bot_key={bot_key} with poll_interval={POLL_INTERVAL_SECONDS}s")
    
    # Normalize enqueue_callback to (context, message_text) signature when callers passed a function expecting (db, context, message_text)
    try:
        sig = inspect.signature(enqueue_callback)
        params = [p for p in sig.parameters.values() if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        if len(params) == 3:
            # enqueue_callback(db, context, message_text) -> adapt to (context, message_text)
            original = enqueue_callback
            enqueue_callback = (lambda context, message_text, _f=original, _db=db: _f(_db, context, message_text))
            logger.info("[BroadcastForwarder] Wrapped enqueue_callback to bind db for 3-arg callback")
    except Exception:
        # If inspect fails for some callables, just proceed — runtime error will be handled in the loop
        logger.debug("[BroadcastForwarder] Could not inspect enqueue_callback signature; proceeding without normalization")
    
    task = asyncio.create_task(_listener_loop(db, enqueue_callback, bot_key))
    return task


async def _listener_loop(db: Database, enqueue_callback: Callable[[dict, str], Awaitable[None]], bot_key: str):
    """
    Internal polling loop that processes unprocessed broadcast events.
    
    This runs continuously, checking for new broadcasts every POLL_INTERVAL_SECONDS.
    For each unprocessed broadcast:
    1. Calls enqueue_callback with the broadcast data
    2. Marks the broadcast as processed (even if callback fails, to avoid infinite retries)
    
    Args:
        db: Database instance
        enqueue_callback: Async callback to invoke for each broadcast
        bot_key: The bot environment key for this instance
    """
    logger.info(f"[BroadcastForwarder] Listener loop started for {bot_key}")
    
    while True:
        try:
            # Poll for unprocessed broadcasts
            events = await db.get_unprocessed_broadcasts_for_bot(bot_key)
            
            if events:
                logger.info(f"[BroadcastForwarder] Found {len(events)} unprocessed broadcast(s) for {bot_key}")
            
            for event in events:
                broadcast_id = event['id']
                message_text = event['message_text']
                payload_json = event.get('payload_json')
                
                # Deserialize payload if present
                context = {}
                if payload_json:
                    try:
                        context = json.loads(payload_json)
                    except json.JSONDecodeError as e:
                        logger.warning(f"[BroadcastForwarder] Failed to parse payload for broadcast {broadcast_id}: {e}")
                
                # Call the enqueue callback
                try:
                    logger.info(f"[BroadcastForwarder] Processing broadcast {broadcast_id} for {bot_key}: {message_text[:50]}...")
                    await enqueue_callback(context, message_text)
                    logger.info(f"[BroadcastForwarder] Successfully processed broadcast {broadcast_id}")
                except Exception as e:
                    logger.error(f"[BroadcastForwarder] Error processing broadcast {broadcast_id}: {e}", exc_info=True)
                    # Continue anyway - we'll mark it processed to avoid infinite retries
                
                # Mark as processed (INSERT OR IGNORE to handle race conditions)
                try:
                    await db.mark_broadcast_processed(broadcast_id, bot_key)
                except Exception as e:
                    logger.error(f"[BroadcastForwarder] Failed to mark broadcast {broadcast_id} as processed: {e}", exc_info=True)
            
            # Wait before next poll
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            
        except Exception as e:
            logger.error(f"[BroadcastForwarder] Error in listener loop: {e}", exc_info=True)
            # Wait a bit before retrying to avoid tight error loops
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
