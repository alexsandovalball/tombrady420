"""
Broadcast forwarder using database-backed pub/sub.

This module provides a DB-backed broadcast forwarding system that replaces Redis pub/sub.
It allows multiple bot instances to coordinate broadcast messages by storing events in
the SQLite database and having each bot poll for unprocessed events.
"""
import asyncio
import json
import logging
from typing import Callable, Awaitable, Optional, Dict, Any

from database import Database
from configs.config import Config
from utils.bot_key import get_bot_env_key

logger = logging.getLogger(__name__)

# Polling configuration
POLL_INTERVAL_SECONDS = 3  # How often to check for new broadcast events


async def publish_broadcast(db: Database, message_text: str, payload: Optional[dict] = None) -> int:
    """
    Publish a broadcast event to the database.
    
    This inserts a new broadcast event that will be picked up by all bot instances
    running the listener loop.
    
    Args:
        db: Database instance
        message_text: The text message to broadcast
        payload: Optional dictionary payload with additional data
        
    Returns:
        int: The ID of the created broadcast event
        
    Raises:
        Exception: If the database insert fails
    """
    try:
        # Convert payload to JSON if provided
        payload_json = json.dumps(payload) if payload else None
        
        # Insert the broadcast event
        event_id = await db.insert_broadcast_event(message_text, payload_json)
        
        logger.info(f"[BroadcastForwarder] Published broadcast event {event_id}: {message_text[:50]}...")
        return event_id
        
    except Exception as e:
        logger.error(f"[BroadcastForwarder] Failed to publish broadcast: {e}", exc_info=True)
        raise


def start_listener_in_background(
    db: Database, 
    enqueue_callback: Callable[[dict, str], Awaitable[None]]
) -> asyncio.Task:
    """
    Start a background task that polls for unprocessed broadcast events.
    
    The listener will continuously poll the database for broadcast events that
    haven't been processed by this bot instance yet. When found, it calls the
    enqueue_callback with the event context and message text.
    
    Args:
        db: Database instance
        enqueue_callback: Async function to call for each unprocessed event.
                         Should accept (context: dict, message_text: str).
                         
    Returns:
        asyncio.Task: The background task handle
    """
    task = asyncio.create_task(_listener_loop(db, enqueue_callback))
    logger.info("[BroadcastForwarder] Started broadcast listener in background")
    return task


async def _listener_loop(
    db: Database,
    enqueue_callback: Callable[[dict, str], Awaitable[None]]
) -> None:
    """
    Internal polling loop that checks for unprocessed broadcast events.
    
    This loop runs continuously, checking the database every POLL_INTERVAL_SECONDS
    for new broadcast events. When found, it:
    1. Calls the enqueue_callback to deliver the message
    2. Marks the event as processed (even if callback fails, to avoid infinite retries)
    
    Args:
        db: Database instance
        enqueue_callback: Async function to call for each event
    """
    bot_key = get_bot_env_key()
    logger.info(f"[BroadcastForwarder] Listener loop starting for bot_key={bot_key}")
    
    while True:
        try:
            # Get unprocessed events for this bot
            events = await db.get_unprocessed_broadcasts_for_bot(bot_key)
            
            if events:
                logger.info(f"[BroadcastForwarder] Found {len(events)} unprocessed broadcast(s)")
            
            for event in events:
                event_id = event['id']
                message_text = event['message_text']
                payload_json = event.get('payload_json')
                
                # Parse payload if present
                context = {}
                if payload_json:
                    try:
                        context = json.loads(payload_json)
                    except json.JSONDecodeError as e:
                        logger.error(f"[BroadcastForwarder] Failed to parse payload for event {event_id}: {e}")
                
                logger.info(f"[BroadcastForwarder] Processing broadcast event {event_id}")
                
                # Call the enqueue callback
                # Important: We mark as processed even if callback fails to avoid infinite retries
                try:
                    await enqueue_callback(context, message_text)
                    logger.info(f"[BroadcastForwarder] Successfully enqueued broadcast event {event_id}")
                except Exception as e:
                    logger.error(
                        f"[BroadcastForwarder] Error calling enqueue_callback for event {event_id}: {e}",
                        exc_info=True
                    )
                
                # Mark as processed (INSERT OR IGNORE prevents duplicates)
                try:
                    await db.mark_broadcast_processed(event_id, bot_key)
                    logger.debug(f"[BroadcastForwarder] Marked event {event_id} as processed by {bot_key}")
                except Exception as e:
                    logger.error(
                        f"[BroadcastForwarder] Failed to mark event {event_id} as processed: {e}",
                        exc_info=True
                    )
            
            # Wait before next poll
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            
        except asyncio.CancelledError:
            logger.info("[BroadcastForwarder] Listener loop cancelled")
            break
        except Exception as e:
            logger.error(f"[BroadcastForwarder] Error in listener loop: {e}", exc_info=True)
            # Continue despite errors - wait a bit longer before retrying
            await asyncio.sleep(POLL_INTERVAL_SECONDS * 2)
    
    logger.info("[BroadcastForwarder] Listener loop stopped")
