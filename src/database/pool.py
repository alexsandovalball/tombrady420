"""
Database connection pooling for improved performance.
Provides reusable connections and query caching.
"""
import asyncio
import aiosqlite
import logging
from typing import Optional, Dict, Any, List
from datetime import datetime, timedelta
import hashlib
import json

logger = logging.getLogger(__name__)


class DatabasePool:
    """
    Connection pool manager for SQLite database.
    Provides connection reuse and query result caching.
    """
    
    def __init__(self, db_path: str, pool_size: int = 10, cache_ttl: int = 300):
        """
        Initialize the database pool.
        
        Args:
            db_path: Path to SQLite database file
            pool_size: Maximum number of connections in pool
            cache_ttl: Cache time-to-live in seconds (default 5 minutes)
        """
        self.db_path = db_path
        self.pool_size = pool_size
        self.cache_ttl = cache_ttl
        self._pool: List[aiosqlite.Connection] = []
        self._in_use: List[aiosqlite.Connection] = []
        self._lock = asyncio.Lock()
        self._cache: Dict[str, tuple] = {}  # {query_hash: (result, timestamp)}
        self._stats = {
            'total_queries': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'connections_created': 0
        }
    
    async def _create_connection(self) -> aiosqlite.Connection:
        """Create a new database connection with WAL mode enabled."""
        conn = await aiosqlite.connect(self.db_path)
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA synchronous=NORMAL")
        await conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
        self._stats['connections_created'] += 1
        return conn
    
    async def get_connection(self) -> aiosqlite.Connection:
        """Get a connection from the pool or create a new one."""
        async with self._lock:
            if self._pool:
                conn = self._pool.pop()
                self._in_use.append(conn)
                return conn
            elif len(self._in_use) < self.pool_size:
                conn = await self._create_connection()
                self._in_use.append(conn)
                return conn
            else:
                # Wait for a connection to become available
                pass
        
        # If pool is exhausted, wait and retry
        await asyncio.sleep(0.1)
        return await self.get_connection()
    
    async def release_connection(self, conn: aiosqlite.Connection):
        """Return a connection to the pool."""
        async with self._lock:
            if conn in self._in_use:
                self._in_use.remove(conn)
                self._pool.append(conn)
    
    async def close_all(self):
        """Close all connections in the pool."""
        async with self._lock:
            for conn in self._pool + self._in_use:
                await conn.close()
            self._pool.clear()
            self._in_use.clear()
    
    def _get_cache_key(self, query: str, params: tuple = None) -> str:
        """Generate a cache key for a query."""
        key_data = f"{query}:{params}"
        return hashlib.md5(key_data.encode()).hexdigest()
    
    def _is_cache_valid(self, timestamp: datetime) -> bool:
        """Check if cached result is still valid."""
        return (datetime.now() - timestamp).seconds < self.cache_ttl
    
    async def execute_cached(self, query: str, params: tuple = None, cacheable: bool = True):
        """
        Execute a SELECT query with optional caching.
        
        Args:
            query: SQL query string
            params: Query parameters
            cacheable: Whether to cache this query result
            
        Returns:
            List of rows
        """
        self._stats['total_queries'] += 1
        
        # Check cache for SELECT queries
        if cacheable and query.strip().upper().startswith('SELECT'):
            cache_key = self._get_cache_key(query, params)
            
            if cache_key in self._cache:
                result, timestamp = self._cache[cache_key]
                if self._is_cache_valid(timestamp):
                    self._stats['cache_hits'] += 1
                    logger.debug(f"Cache hit for query: {query[:50]}...")
                    return result
                else:
                    # Remove expired cache entry
                    del self._cache[cache_key]
        
        self._stats['cache_misses'] += 1
        
        # Execute query
        conn = await self.get_connection()
        try:
            cursor = await conn.execute(query, params or ())
            result = await cursor.fetchall()
            
            # Cache the result if applicable
            if cacheable and query.strip().upper().startswith('SELECT'):
                cache_key = self._get_cache_key(query, params)
                self._cache[cache_key] = (result, datetime.now())
            
            return result
        finally:
            await self.release_connection(conn)
    
    async def execute(self, query: str, params: tuple = None):
        """Execute a non-SELECT query (INSERT, UPDATE, DELETE)."""
        self._stats['total_queries'] += 1
        
        conn = await self.get_connection()
        try:
            await conn.execute(query, params or ())
            await conn.commit()
        finally:
            await self.release_connection(conn)
    
    async def executemany(self, query: str, params_list: List[tuple]):
        """Execute a batch of queries efficiently."""
        self._stats['total_queries'] += len(params_list)
        
        conn = await self.get_connection()
        try:
            await conn.executemany(query, params_list)
            await conn.commit()
        finally:
            await self.release_connection(conn)
    
    def clear_cache(self):
        """Clear all cached query results."""
        self._cache.clear()
        logger.info("Query cache cleared")
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get pool statistics."""
        cache_hit_rate = 0
        if self._stats['total_queries'] > 0:
            cache_hit_rate = self._stats['cache_hits'] / self._stats['total_queries']
        
        return {
            'active_connections': len(self._in_use),
            'available_connections': len(self._pool),
            'total_connections': len(self._pool) + len(self._in_use),
            'total_queries': self._stats['total_queries'],
            'cache_hits': self._stats['cache_hits'],
            'cache_misses': self._stats['cache_misses'],
            'cache_hit_rate': cache_hit_rate,
            'cache_size': len(self._cache),
            'connections_created': self._stats['connections_created']
        }
