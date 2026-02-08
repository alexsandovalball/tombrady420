"""
Broadcast forwarder using Redis pub/sub to distribute broadcasts across bot instances.

This module provides:
1. publish_broadcast() - Publishes broadcast events to Redis
2. start_listener_in_background() - Starts a Redis listener in each bot process to enqueue local broadcasts
3. MinimalContext - Helper class for creating context objects for listeners
"""
import logging
import asyncio
import json
from typing import Optional, Callable
from telegram.ext import ContextTypes

logger = logging.getLogger(__name__)

# Redis channel name for broadcast events
BROADCAST_CHANNEL = "bot:broadcast"

# Global listener task reference
_listener_task: Optional[asyncio.Task] = None


class MinimalContext:
    """
    Minimal context object for broadcast listener.
    Used when starting listeners without a full Telegram update context.
    """
    def __init__(self, bot, application):
        self.bot = bot
        self.application = application


async def publish_broadcast(message_text: str) -> bool:
    """
    Publish a broadcast event to Redis pub/sub channel.
    All bot instances subscribed to the channel will receive and process the event.
    
    Args:
        message_text: The text message to broadcast
        
    Returns:
        bool: True if published successfully, False otherwise
    """
    try:
        from utils.redis_client import get_redis_client
        import time
        
        redis_client = get_redis_client()
        
        # Prepare broadcast payload
        payload = {
            "message_text": message_text,
            "timestamp": time.time()
        }
        
        # Publish to Redis channel
        # Returns number of subscribers that received the message
        subscriber_count = redis_client.publish(BROADCAST_CHANNEL, json.dumps(payload))
        
        logger.info(
            f"[BroadcastForwarder] Published broadcast to {subscriber_count} subscribers: "
            f"message_length={len(message_text)}"
        )
        
        return True
        
    except Exception as e:
        logger.error(f"[BroadcastForwarder] Failed to publish broadcast: {e}", exc_info=True)
        return False


async def _redis_listener_loop(
    context: ContextTypes.DEFAULT_TYPE,
    enqueue_callback: Callable
):
    """
    Internal Redis listener loop that subscribes to broadcast channel.
    When a message is received, it calls the enqueue_callback to process the broadcast locally.
    
    Note: This uses the synchronous redis pubsub interface with a timeout. While not fully async,
    this is acceptable because:
    1. The blocking is minimal (1 second timeout)
    2. It runs in a dedicated background task
    3. The redis library's async support (redis.asyncio) requires Python 3.7+ and additional setup
    
    Args:
        context: Telegram context for bot operations
        enqueue_callback: Async callback function to enqueue local broadcast
    """
    from utils.redis_client import get_redis_client
    from utils.bot_key import get_bot_env_key, get_bot_key_display
    
    redis_client = None
    pubsub = None
    
    try:
        redis_client = get_redis_client()
        pubsub = redis_client.pubsub()
        
        # Subscribe to broadcast channel
        pubsub.subscribe(BROADCAST_CHANNEL)
        
        bot_key = get_bot_env_key()
        bot_username = context.bot.username if hasattr(context.bot, 'username') else 'unknown'
        bot_display = get_bot_key_display(bot_key, bot_username)
        
        logger.info(
            f"[BroadcastForwarder] Listener started for {bot_display}, "
            f"subscribed to channel: {BROADCAST_CHANNEL}"
        )
        
        # Listen for messages
        while True:
            try:
                # Get message with timeout to allow graceful shutdown
                # Note: This is a synchronous call, but with a 1-second timeout
                # it's acceptable for this background task
                message = pubsub.get_message(timeout=1.0)
                
                if message and message['type'] == 'message':
                    try:
                        # Parse the broadcast payload
                        payload = json.loads(message['data'])
                        message_text = payload.get('message_text', '')
                        
                        logger.info(
                            f"[BroadcastForwarder] Received broadcast on {bot_display}: "
                            f"message_length={len(message_text)}"
                        )
                        
                        # Enqueue local broadcast for this bot's users
                        await enqueue_callback(context, message_text)
                        
                    except json.JSONDecodeError as e:
                        logger.error(f"[BroadcastForwarder] Invalid JSON in broadcast message: {e}")
                    except Exception as e:
                        logger.error(
                            f"[BroadcastForwarder] Error processing broadcast message: {e}",
                            exc_info=True
                        )
                
                # Small delay to prevent tight loop
                await asyncio.sleep(0.1)
                
            except asyncio.CancelledError:
                logger.info(f"[BroadcastForwarder] Listener cancelled for {bot_display}")
                raise
            except Exception as e:
                logger.error(
                    f"[BroadcastForwarder] Error in listener loop: {e}",
                    exc_info=True
                )
                # Wait before retrying
                await asyncio.sleep(5)
                
    except asyncio.CancelledError:
        logger.info("[BroadcastForwarder] Listener shutting down")
        raise
    except Exception as e:
        logger.error(f"[BroadcastForwarder] Fatal error in listener: {e}", exc_info=True)
        raise
    finally:
        # Cleanup
        if pubsub:
            try:
                pubsub.unsubscribe(BROADCAST_CHANNEL)
                pubsub.close()
                logger.info("[BroadcastForwarder] Unsubscribed and closed pubsub")
            except Exception as e:
                logger.warning(f"[BroadcastForwarder] Error closing pubsub: {e}")


def start_listener_in_background(
    context: ContextTypes.DEFAULT_TYPE,
    enqueue_callback: Callable
) -> Optional[asyncio.Task]:
    """
    Start the Redis broadcast listener in the background.
    Should be called once at bot startup.
    
    Args:
        context: Telegram context for bot operations
        enqueue_callback: Async callback function(context, message_text) to enqueue local broadcast
        
    Returns:
        asyncio.Task or None: The listener task if started successfully, None if Redis is not configured
    """
    global _listener_task
    
    try:
        from configs.config import Config
        
        # Check if Redis is configured
        if not Config.REDIS_URL:
            logger.info(
                "[BroadcastForwarder] Redis not configured (REDIS_URL not set). "
                "Broadcasts will only work on local bot instance."
            )
            return None
        
        # Check if listener is already running
        if _listener_task and not _listener_task.done():
            logger.warning("[BroadcastForwarder] Listener already running")
            return _listener_task
        
        # Create and start the listener task
        _listener_task = asyncio.create_task(_redis_listener_loop(context, enqueue_callback))
        
        logger.info("[BroadcastForwarder] Listener task created successfully")
        
        return _listener_task
        
    except Exception as e:
        logger.error(f"[BroadcastForwarder] Failed to start listener: {e}", exc_info=True)
        return None


def stop_listener():
    """
    Stop the Redis broadcast listener.
    Useful for cleanup or testing.
    """
    global _listener_task
    
    if _listener_task and not _listener_task.done():
        _listener_task.cancel()
        logger.info("[BroadcastForwarder] Listener task cancelled")
        _listener_task = None
