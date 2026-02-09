"""
Bot key utilities for multi-bot support.

This module provides helpers to identify which bot environment variable
the current process is using. This allows multiple bot processes to
coordinate broadcasts and notifications without hardcoding bot usernames.
"""
import os
import re
import logging
from typing import Optional
import threading

logger = logging.getLogger(__name__)

# Global variable to store the bot username for this process
# Set during application initialization via set_bot_username()
# Thread-safe access via a lock
_BOT_USERNAME: Optional[str] = None
_BOT_USERNAME_LOCK = threading.Lock()


def get_bot_env_key() -> str:
    """
    Get the environment variable name used for the bot token in this process.
    
    Searches for environment variables matching the pattern BOT_TOKEN or BOT_TOKEN_N
    where N is a digit. Prefers BOT_TOKEN if present, otherwise returns the first
    matching BOT_TOKEN_N key.
    
    This provides a stable identifier for the bot process that can be stored in
    the database instead of bot usernames, making the code portable across deployments.
    
    Returns:
        str: The environment variable name (e.g., 'BOT_TOKEN', 'BOT_TOKEN_1', etc.)
             Returns 'BOT_TOKEN' as fallback if no matching env var is found.
    
    Examples:
        >>> os.environ['BOT_TOKEN'] = 'token1'
        >>> get_bot_env_key()
        'BOT_TOKEN'
        
        >>> del os.environ['BOT_TOKEN']
        >>> os.environ['BOT_TOKEN_1'] = 'token1'
        >>> get_bot_env_key()
        'BOT_TOKEN_1'
    """
    # Pattern matches: BOT_TOKEN, BOT_TOKEN_1, BOT_TOKEN_2, etc.
    pattern = re.compile(r'^BOT_TOKEN(?:_\d+)?$')
    
    # First, check if BOT_TOKEN exists (this is the primary bot)
    if 'BOT_TOKEN' in os.environ:
        logger.debug("[BotKey] Found BOT_TOKEN in environment (primary bot)")
        return 'BOT_TOKEN'
    
    # Look for BOT_TOKEN_N keys
    matching_keys = [key for key in os.environ.keys() if pattern.match(key)]
    
    if matching_keys:
        # Sort to get the first numbered key (BOT_TOKEN_1, BOT_TOKEN_2, etc.)
        matching_keys.sort()
        selected_key = matching_keys[0]
        logger.debug(f"[BotKey] Found {len(matching_keys)} BOT_TOKEN keys, selected: {selected_key}")
        return selected_key
    
    # Fallback if no matching env var found
    logger.warning("[BotKey] No BOT_TOKEN or BOT_TOKEN_N environment variable found, using 'BOT_TOKEN' as fallback")
    return 'BOT_TOKEN'


def is_primary_bot_process() -> bool:
    """
    Check if the current process is the primary bot.
    
    The primary bot is identified by having the 'BOT_TOKEN' environment variable
    (without a numeric suffix). The primary bot is responsible for:
    - Categorizing new products
    - Queuing notifications for all users
    
    Secondary bots (BOT_TOKEN_1, BOT_TOKEN_2, etc.) are responsible for:
    - Processing their assigned users' messages from queues
    - Handling user interactions
    
    Returns:
        bool: True if this is the primary bot process, False otherwise
    
    Examples:
        >>> os.environ['BOT_TOKEN'] = 'token1'
        >>> is_primary_bot_process()
        True
        
        >>> del os.environ['BOT_TOKEN']
        >>> os.environ['BOT_TOKEN_1'] = 'token1'
        >>> is_primary_bot_process()
        False
    """
    bot_key = get_bot_env_key()
    is_primary = (bot_key == 'BOT_TOKEN')
    
    if is_primary:
        logger.debug("[BotKey] Current process is PRIMARY bot (has BOT_TOKEN)")
    else:
        logger.debug(f"[BotKey] Current process is SECONDARY bot (using {bot_key})")
    
    return is_primary


def get_bot_key_display(bot_key: str, bot_username: Optional[str] = None) -> str:
    """
    Get a human-readable display string for a bot key.
    
    Args:
        bot_key: The environment variable name (e.g., 'BOT_TOKEN', 'BOT_TOKEN_1')
        bot_username: Optional bot username to include in display
    
    Returns:
        str: Display string like "BOT_TOKEN (@mybot)" or "BOT_TOKEN_1"
    
    Examples:
        >>> get_bot_key_display('BOT_TOKEN', 'mybot')
        'BOT_TOKEN (@mybot)'
        
        >>> get_bot_key_display('BOT_TOKEN_1')
        'BOT_TOKEN_1'
    """
    if bot_username:
        return f"{bot_key} (@{bot_username})"
    return bot_key


def set_bot_username(username: str):
    """
    Set the bot username for this process (thread-safe).
    
    This should be called during application initialization with the bot's
    actual Telegram username. It allows other modules to access the bot_username
    without needing a Telegram context.
    
    Note: This should only be called once during initialization. Subsequent calls
    will overwrite the value.
    
    Args:
        username: The bot's Telegram username (without @)
    """
    global _BOT_USERNAME
    with _BOT_USERNAME_LOCK:
        _BOT_USERNAME = username
    logger.info(f"[BotKey] Set bot_username to: @{username}")


def get_bot_username() -> Optional[str]:
    """
    Get the bot username for this process (thread-safe).
    
    Returns the username that was set via set_bot_username() during initialization.
    Returns None if not yet initialized.
    
    Returns:
        str: The bot username (without @), or None if not set
    """
    with _BOT_USERNAME_LOCK:
        return _BOT_USERNAME

