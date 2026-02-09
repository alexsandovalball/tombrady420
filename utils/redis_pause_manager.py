"""
Redis-backed shared pause state manager for rate-limit handling.

This module provides a centralized, shared pause mechanism that:
- Uses Redis for cross-worker pause coordination
- Stores pause state per bot: notifications:pause:{bot_username}
- Implements atomic pause set/extend operations
- Parses Retry-After and X-RateLimit-Reset headers
- Adds configurable buffer to pause duration
- Provides telemetry and metrics for pause events
"""
import logging
import json
import time
from typing import Optional, Dict, Any
import redis.asyncio as redis
from configs.config import Config

logger = logging.getLogger(__name__)

# Configuration defaults
DEFAULT_PAUSE_BUFFER_SECONDS = 5
DEFAULT_REDIS_HOST = 'localhost'
DEFAULT_REDIS_PORT = 6379
DEFAULT_REDIS_DB = 0

# Metrics counters (in-memory for simplicity, could be exported to Prometheus/StatsD)
_metrics = {
    "pause_set_count": 0,
    "pause_skipped_count": 0
}


def get_redis_client() -> redis.Redis:
    """
    Get a Redis client instance.
    
    Returns:
        redis.Redis: Configured Redis client
    """
    redis_host = getattr(Config, 'REDIS_HOST', DEFAULT_REDIS_HOST)
    redis_port = getattr(Config, 'REDIS_PORT', DEFAULT_REDIS_PORT)
    redis_db = getattr(Config, 'REDIS_DB', DEFAULT_REDIS_DB)
    redis_password = getattr(Config, 'REDIS_PASSWORD', None)
    
    return redis.Redis(
        host=redis_host,
        port=redis_port,
        db=redis_db,
        password=redis_password,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5
    )


def get_pause_key(bot_username: str) -> str:
    """
    Get the Redis key for a bot's pause state.
    
    Args:
        bot_username: Bot username (e.g., "my_bot")
    
    Returns:
        str: Redis key (e.g., "notifications:pause:my_bot")
    """
    return f"notifications:pause:{bot_username}"


async def get_pause_state(bot_username: str) -> Optional[Dict[str, Any]]:
    """
    Get the current pause state for a bot.
    
    Args:
        bot_username: Bot username
    
    Returns:
        dict or None: Pause state with {paused_until, reason, set_by, retry_after_header}
                     or None if not paused or pause expired
    """
    try:
        client = get_redis_client()
        key = get_pause_key(bot_username)
        
        data = await client.get(key)
        if not data:
            return None
        
        pause_state = json.loads(data)
        paused_until = pause_state.get("paused_until", 0)
        
        # Check if pause has expired
        current_time = time.time()
        if paused_until <= current_time:
            # Clean up expired pause
            await client.delete(key)
            return None
        
        return pause_state
    except Exception as e:
        logger.error(f"Error getting pause state for bot {bot_username}: {e}")
        return None
    finally:
        await client.aclose()


async def get_remaining_pause_seconds(bot_username: str) -> float:
    """
    Get the remaining pause duration in seconds.
    
    Args:
        bot_username: Bot username
    
    Returns:
        float: Remaining seconds to wait, or 0 if not paused
    """
    pause_state = await get_pause_state(bot_username)
    if not pause_state:
        return 0.0
    
    paused_until = pause_state.get("paused_until", 0)
    current_time = time.time()
    remaining = paused_until - current_time
    return max(0.0, remaining)


async def is_paused(bot_username: str) -> bool:
    """
    Check if a bot is currently paused.
    
    Args:
        bot_username: Bot username
    
    Returns:
        bool: True if paused, False otherwise
    """
    remaining = await get_remaining_pause_seconds(bot_username)
    return remaining > 0


async def set_pause_state(
    bot_username: str,
    retry_after_seconds: float,
    reason: str = "rate_limit_429",
    set_by: str = "system",
    retry_after_header: Optional[str] = None,
    buffer_seconds: Optional[float] = None
) -> bool:
    """
    Set pause state for a bot with atomic extend semantics.
    
    Only updates the pause if the new paused_until timestamp is greater than
    the existing one, preventing races where multiple workers try to set pause.
    
    Args:
        bot_username: Bot username
        retry_after_seconds: Seconds to pause (from Retry-After or default)
        reason: Reason for pause (default: "rate_limit_429")
        set_by: Who set the pause (default: "system")
        retry_after_header: Original Retry-After header value
        buffer_seconds: Extra buffer to add (default: DEFAULT_PAUSE_BUFFER_SECONDS)
    
    Returns:
        bool: True if pause was set/extended, False otherwise
    """
    if buffer_seconds is None:
        buffer_seconds = getattr(Config, 'PAUSE_BUFFER_SECONDS', DEFAULT_PAUSE_BUFFER_SECONDS)
    
    try:
        client = get_redis_client()
        key = get_pause_key(bot_username)
        
        current_time = time.time()
        new_paused_until = current_time + retry_after_seconds + buffer_seconds
        
        # Use Lua script for atomic compare-and-set
        # Only set if new paused_until > existing paused_until
        lua_script = """
        local key = KEYS[1]
        local new_data = ARGV[1]
        local new_paused_until = tonumber(ARGV[2])
        local ttl = tonumber(ARGV[3])
        
        local existing = redis.call('GET', key)
        if existing then
            local existing_data = cjson.decode(existing)
            local existing_paused_until = tonumber(existing_data.paused_until)
            
            -- Only update if new pause is longer
            if new_paused_until > existing_paused_until then
                redis.call('SETEX', key, ttl, new_data)
                return 1
            else
                return 0
            end
        else
            -- No existing pause, set it
            redis.call('SETEX', key, ttl, new_data)
            return 1
        end
        """
        
        pause_data = {
            "paused_until": new_paused_until,
            "reason": reason,
            "set_by": set_by,
            "retry_after_header": retry_after_header,
            "set_at": current_time
        }
        
        pause_json = json.dumps(pause_data)
        # Set TTL slightly longer than pause duration to avoid premature expiry
        ttl = int(retry_after_seconds + buffer_seconds + 60)
        
        result = await client.eval(lua_script, 1, key, pause_json, new_paused_until, ttl)
        
        if result == 1:
            # Increment metrics
            _metrics["pause_set_count"] += 1
            
            logger.info(
                f"[PauseManager] Set pause for bot @{bot_username}: "
                f"paused_until={new_paused_until:.2f} ({retry_after_seconds + buffer_seconds:.1f}s), "
                f"reason={reason}, set_by={set_by}, retry_after_header={retry_after_header}"
            )
            return True
        else:
            logger.debug(
                f"[PauseManager] Pause not extended for bot @{bot_username}: "
                f"existing pause is longer"
            )
            return False
        
    except Exception as e:
        logger.error(f"Error setting pause state for bot {bot_username}: {e}")
        return False
    finally:
        await client.aclose()


async def clear_pause_state(bot_username: str):
    """
    Clear the pause state for a bot (manual override).
    
    Args:
        bot_username: Bot username
    """
    try:
        client = get_redis_client()
        key = get_pause_key(bot_username)
        await client.delete(key)
        logger.info(f"[PauseManager] Cleared pause state for bot @{bot_username}")
    except Exception as e:
        logger.error(f"Error clearing pause state for bot {bot_username}: {e}")
    finally:
        await client.aclose()


async def increment_pause_skipped_metric(bot_username: str):
    """
    Increment the counter for skipped processing due to pause.
    
    Args:
        bot_username: Bot username
    """
    _metrics["pause_skipped_count"] += 1
    logger.debug(f"[PauseManager] Incremented pause_skipped metric for bot @{bot_username}")


def get_metrics() -> Dict[str, int]:
    """
    Get current metrics counters.
    
    Returns:
        dict: Metrics with pause_set_count and pause_skipped_count
    """
    return _metrics.copy()


def parse_retry_after(retry_after_value: Any) -> Optional[float]:
    """
    Parse Retry-After header value to seconds.
    
    Supports:
    - Integer seconds: "30"
    - HTTP-date format (converts to seconds from now)
    
    Args:
        retry_after_value: Header value (int, str, or None)
    
    Returns:
        float or None: Seconds to wait, or None if cannot parse
    """
    if retry_after_value is None:
        return None
    
    try:
        # Try parsing as integer seconds
        return float(retry_after_value)
    except (ValueError, TypeError):
        pass
    
    try:
        # Try parsing as HTTP-date
        from email.utils import parsedate_to_datetime
        retry_date = parsedate_to_datetime(str(retry_after_value))
        current_time = time.time()
        retry_timestamp = retry_date.timestamp()
        seconds = retry_timestamp - current_time
        return max(0.0, seconds)
    except Exception:
        logger.warning(f"Failed to parse Retry-After value: {retry_after_value}")
        return None


def parse_ratelimit_reset(reset_value: Any) -> Optional[float]:
    """
    Parse X-RateLimit-Reset header value to seconds.
    
    Typically a Unix timestamp.
    
    Args:
        reset_value: Header value (int, str, or None)
    
    Returns:
        float or None: Seconds to wait, or None if cannot parse
    """
    if reset_value is None:
        return None
    
    try:
        reset_timestamp = float(reset_value)
        current_time = time.time()
        seconds = reset_timestamp - current_time
        return max(0.0, seconds)
    except (ValueError, TypeError):
        logger.warning(f"Failed to parse X-RateLimit-Reset value: {reset_value}")
        return None
