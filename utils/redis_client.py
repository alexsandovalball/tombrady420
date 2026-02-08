"""
Redis client initialization and configuration.
Provides a singleton Redis client for pub/sub and caching operations.
"""
import logging
import redis
from typing import Optional
from configs.config import Config

logger = logging.getLogger(__name__)

_redis_client: Optional[redis.Redis] = None


def get_redis_client() -> redis.Redis:
    """
    Get or create a singleton Redis client instance.
    
    Note: Uses synchronous Redis client. The initial ping() call during initialization
    is synchronous but only happens once. For production use with async/await throughout,
    consider using redis.asyncio.Redis.
    
    Returns:
        redis.Redis: Configured Redis client
        
    Raises:
        ValueError: If REDIS_URL is not configured
    """
    global _redis_client
    
    if _redis_client is None:
        # Get Redis URL from config
        redis_url = getattr(Config, 'REDIS_URL', None)
        
        if not redis_url:
            raise ValueError(
                "REDIS_URL not configured. Please set REDIS_URL environment variable "
                "(e.g., redis://localhost:6379/0)"
            )
        
        try:
            _redis_client = redis.from_url(
                redis_url,
                decode_responses=True,  # Automatically decode responses to strings
                socket_connect_timeout=5,
                socket_timeout=5,
                retry_on_timeout=True,
                health_check_interval=30
            )
            
            # Test the connection (synchronous - only runs once during initialization)
            _redis_client.ping()
            logger.info(f"Redis client initialized successfully: {redis_url}")
            
        except redis.RedisError as e:
            logger.error(f"Failed to initialize Redis client: {e}")
            raise
    
    return _redis_client


def reset_redis_client():
    """
    Reset the Redis client (useful for testing).
    Closes existing connection and clears the singleton.
    """
    global _redis_client
    if _redis_client is not None:
        try:
            _redis_client.close()
        except Exception as e:
            logger.warning(f"Error closing Redis client: {e}")
        finally:
            _redis_client = None
