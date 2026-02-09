"""
Database-backed rate limiter for shared pause state across bot workers.

This module provides a centralized way to track Telegram RetryAfter windows
so that when one worker receives a 429, all workers pause for that bot.

Uses SQLite for storage (consistent with broadcast_forwarder approach).
"""
import logging
import time
from typing import Optional
from contextlib import asynccontextmanager
import aiosqlite

from configs.config import Config

logger = logging.getLogger(__name__)


async def _get_db_path() -> str:
    """Get the database path from config."""
    return Config.DB_PATH


async def get_paused_until(bot_key: str) -> Optional[float]:
    """
    Get the timestamp (UTC epoch seconds) until which sending is paused for a bot.
    
    Args:
        bot_key: Identifier for the bot (e.g., bot_env_key)
    
    Returns:
        The UTC epoch timestamp until which sending is paused, or None if not paused
    """
    try:
        db_path = await _get_db_path()
        async with aiosqlite.connect(db_path) as db:
            async with db.execute("""
                SELECT paused_until FROM bot_rate_pause_state 
                WHERE bot_key = ?
            """, (bot_key,)) as cursor:
                row = await cursor.fetchone()
                
            if row is None:
                return None
            
            paused_until = float(row[0])
            current_time = time.time()
            
            # If pause has expired, delete the row and return None
            if paused_until <= current_time:
                await db.execute("DELETE FROM bot_rate_pause_state WHERE bot_key = ?", (bot_key,))
                await db.commit()
                return None
            
            return paused_until
    except Exception as e:
        logger.error(f"Error getting pause state for bot {bot_key}: {e}")
        return None


async def set_paused_until(bot_key: str, until_timestamp: float):
    """
    Set the timestamp until which sending should be paused for a bot.
    
    Args:
        bot_key: Identifier for the bot
        until_timestamp: UTC epoch timestamp until which to pause
    """
    try:
        current_time = time.time()
        
        if until_timestamp <= current_time:
            logger.warning(f"Pause timestamp {until_timestamp} is in the past; not setting pause state")
            return
        
        db_path = await _get_db_path()
        async with aiosqlite.connect(db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO bot_rate_pause_state (bot_key, paused_until, updated_at)
                VALUES (?, ?, ?)
            """, (bot_key, until_timestamp, current_time))
            await db.commit()
            
        logger.info(f"Set pause state for bot {bot_key} until {until_timestamp} ({until_timestamp - current_time:.1f}s)")
    except Exception as e:
        logger.error(f"Error setting pause state for bot {bot_key}: {e}")


async def clear_pause(bot_key: str):
    """
    Clear the pause state for a bot (e.g., if manually overriding).
    
    Args:
        bot_key: Identifier for the bot
    """
    try:
        db_path = await _get_db_path()
        async with aiosqlite.connect(db_path) as db:
            await db.execute("DELETE FROM bot_rate_pause_state WHERE bot_key = ?", (bot_key,))
            await db.commit()
        logger.info(f"Cleared pause state for bot {bot_key}")
    except Exception as e:
        logger.error(f"Error clearing pause state for bot {bot_key}: {e}")


async def get_remaining_pause_seconds(bot_key: str) -> float:
    """
    Get the remaining pause duration in seconds for a bot.
    
    Args:
        bot_key: Identifier for the bot
    
    Returns:
        Remaining seconds to wait, or 0 if not paused
    """
    paused_until = await get_paused_until(bot_key)
    if paused_until is None:
        return 0.0
    
    current_time = time.time()
    remaining = paused_until - current_time
    return max(0.0, remaining)


@asynccontextmanager
async def check_and_wait_paused(bot_key: str):
    """
    Context manager that checks if bot is paused and waits if necessary.
    
    Usage:
        async with check_and_wait_paused("bot123"):
            # Send message
            await bot.send_message(...)
    
    Args:
        bot_key: Identifier for the bot
    """
    import asyncio
    
    remaining = await get_remaining_pause_seconds(bot_key)
    if remaining > 0:
        logger.info(f"Bot {bot_key} is paused for {remaining:.1f}s; waiting...")
        await asyncio.sleep(remaining)
    
    try:
        yield
    finally:
        pass  # No cleanup needed
