"""
Database models and operations for the media catalog bot.
Uses SQLite with aiosqlite for async operations.
"""
import aiosqlite
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any, Tuple
from collections import defaultdict
import logging
import asyncio
import uuid
import re
import json

from configs.config import Config
from utils.categories import EXCLUDED_FROM_ALL_PRODUCTS
from utils.countries import country_code, country_display_name

logger = logging.getLogger(__name__)

# Pre-computed SQL placeholder string for EXCLUDED_FROM_ALL_PRODUCTS.
# Only '?' characters are interpolated into SQL — never user data.
_EXCLUDED_PLACEHOLDERS = ','.join('?' * len(EXCLUDED_FROM_ALL_PRODUCTS))

# Default values (empty — operators set these via /setcontact and /setlink)
DEFAULT_ORDER_CONTACT = ''
DEFAULT_ORDER_LINK = ''

# In-memory caches for frequently accessed data
_user_language_cache: Dict[int, Tuple[str, datetime]] = {}
_order_contact_cache: Optional[Tuple[str, datetime]] = None
_order_link_cache: Optional[Tuple[str, datetime]] = None
_order_links_cache: Optional[Tuple[List[Dict[str, str]], datetime]] = None
_cache_lock = asyncio.Lock()
CACHE_TTL_SECONDS = 300  # 5 minutes


class Database:
    """Database manager for product catalog."""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or Config.DB_PATH
    
    async def _get_optimized_connection(self):
        """
        Internal helper to create an optimized database connection.
        Applies busy_timeout and cache_size pragmas for better concurrent performance.

        Returns a connection object that should be used with async with.
        """
        conn = await aiosqlite.connect(self.db_path)
        # Set busy timeout to 30 seconds to handle concurrent access gracefully
        await conn.execute("PRAGMA busy_timeout = 30000")
        # Increase cache size for better performance (10MB)
        await conn.execute("PRAGMA cache_size = -10000")
        await conn.execute("PRAGMA synchronous = NORMAL")
        await conn.execute("PRAGMA temp_store = MEMORY")
        await conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def get_connection(self):
        """
        Return an async context manager that yields an optimized database connection.
        Wraps _get_optimized_connection() for backward-compatible public access.

        Usage::

            async with db.get_connection() as conn:
                async with conn.execute("SELECT ...") as cursor:
                    ...
        """
        return self._OptimizedConnectionContext(self)

    class _OptimizedConnectionContext:
        """Async context manager returned by get_connection()."""

        def __init__(self, db: "Database"):
            self._db = db
            self._conn = None

        async def __aenter__(self):
            self._conn = await self._db._get_optimized_connection()
            return self._conn

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            if self._conn is not None:
                await self._conn.close()
            return False

    async def checkpoint_wal(self) -> None:
        """
        Checkpoint the WAL file to prevent it from growing indefinitely.
        Should be called periodically (e.g., after processing large batches).
        Uses TRUNCATE mode to reset the WAL file after checkpointing.
        """
        try:
            async with self.get_connection() as db:
                await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                logger.debug("WAL checkpoint completed successfully")
        except Exception as e:
            logger.warning(f"Failed to checkpoint WAL: {e}")
    
    @staticmethod
    def normalize_bot_username(bot_username: Optional[str]) -> Optional[str]:
        """Normalize bot username to lowercase for consistent database lookups."""
        return bot_username.lower() if bot_username else None
    
    async def init_db(self):
        """Initialize database with schema and apply performance optimizations."""
        async with self.get_connection() as db:
            # Enable WAL mode for better concurrent read/write performance
            await db.execute("PRAGMA journal_mode=WAL")
            logger.info("Enabled WAL mode for database")

            # Set busy timeout to prevent immediate SQLITE_BUSY errors under concurrent load
            await db.execute("PRAGMA busy_timeout = 30000")

            # Performance optimizations
            await db.execute("PRAGMA cache_size = -10000")  # 10MB cache
            await db.execute("PRAGMA synchronous = NORMAL")  # Faster writes, still safe with WAL
            await db.execute("PRAGMA temp_store = MEMORY")  # Keep temp tables in memory

            logger.info("Applied SQLite performance optimizations (busy_timeout=30s, cache_size=10MB, synchronous=NORMAL)")
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    caption TEXT,
                    message_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    media_group_id TEXT,
                    additional_file_ids TEXT,
                    additional_message_ids TEXT,
                    category TEXT,
                    subcategory TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(message_id, chat_id)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS deleted_product_redirects (
                    product_id INTEGER PRIMARY KEY,
                    caption TEXT,
                    category TEXT,
                    subcategory TEXT,
                    deleted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS pagination_state (
                    user_id INTEGER NOT NULL,
                    state_type TEXT NOT NULL,
                    query TEXT,
                    page INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, state_type, query)
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_last_search (
                    user_id INTEGER PRIMARY KEY,
                    query TEXT NOT NULL,
                    page INTEGER NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS ignored_messages (
                    message_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (message_id, chat_id)
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS media_groups (
                    media_group_id TEXT PRIMARY KEY,
                    chat_id INTEGER NOT NULL,
                    product_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS pending_categorization (
                    product_id INTEGER PRIMARY KEY,
                    notified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS bot_users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    interaction_count INTEGER DEFAULT 1,
                    notifications_enabled INTEGER DEFAULT 1,
                    is_blocked INTEGER DEFAULT 0,
                    is_subscribed INTEGER DEFAULT 1,
                    language TEXT DEFAULT 'en'
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS notification_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    product_id INTEGER NOT NULL,
                    sent_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS custom_message_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    message_text TEXT NOT NULL,
                    sent_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS translation_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_text TEXT NOT NULL,
                    source_lang TEXT NOT NULL DEFAULT 'en',
                    target_lang TEXT NOT NULL,
                    translated_text TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    used_count INTEGER DEFAULT 1,
                    last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(source_text, source_lang, target_lang)
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS bot_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS bot_file_id_cache (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_chat_id INTEGER NOT NULL,
                    source_message_id INTEGER NOT NULL,
                    file_index INTEGER DEFAULT 0,
                    file_type TEXT NOT NULL,
                    bot_username TEXT NOT NULL,
                    file_id TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_used TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    use_count INTEGER DEFAULT 1,
                    UNIQUE(source_chat_id, source_message_id, file_index, bot_username)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS bot_instances (
                    bot_username TEXT PRIMARY KEY,
                    bot_id INTEGER NOT NULL,
                    token_hash TEXT NOT NULL,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            await db.execute("""
                CREATE TABLE IF NOT EXISTS inline_queries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    query TEXT NOT NULL DEFAULT '',
                    bot_username TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS inline_chosen_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    result_id TEXT NOT NULL,
                    query TEXT NOT NULL DEFAULT '',
                    bot_username TEXT NOT NULL DEFAULT '',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS webapp_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT,
                    user_id TEXT,
                    username TEXT,
                    event_type TEXT NOT NULL DEFAULT 'unknown',
                    action TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT '',
                    error TEXT NOT NULL DEFAULT '',
                    details_json TEXT,
                    client_timestamp TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS visitor_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    visitor_hash TEXT NOT NULL,
                    telegram_user_id TEXT,
                    username TEXT,
                    source TEXT NOT NULL DEFAULT 'website',
                    page_type TEXT NOT NULL DEFAULT '',
                    path TEXT NOT NULL DEFAULT '',
                    product_id INTEGER,
                    category TEXT,
                    subcategory TEXT,
                    country TEXT NOT NULL DEFAULT 'Unknown',
                    region TEXT NOT NULL DEFAULT 'Unknown',
                    city TEXT NOT NULL DEFAULT 'Unknown',
                    language TEXT NOT NULL DEFAULT '',
                    device_type TEXT NOT NULL DEFAULT 'unknown',
                    user_agent_hash TEXT NOT NULL DEFAULT '',
                    referrer TEXT NOT NULL DEFAULT '',
                    is_authenticated INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS search_query_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    normalized_query TEXT NOT NULL,
                    expanded_query TEXT,
                    matched_count INTEGER NOT NULL DEFAULT 0,
                    source TEXT NOT NULL DEFAULT 'webapp',
                    language TEXT NOT NULL DEFAULT '',
                    user_id TEXT,
                    ip_address TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS seo_query_mappings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    term TEXT NOT NULL,
                    normalized_term TEXT NOT NULL UNIQUE,
                    category TEXT,
                    subcategory TEXT,
                    source TEXT NOT NULL DEFAULT 'admin',
                    language TEXT NOT NULL DEFAULT '',
                    approved INTEGER NOT NULL DEFAULT 1,
                    hits INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS search_console_query_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    query TEXT NOT NULL,
                    normalized_query TEXT NOT NULL,
                    page TEXT NOT NULL DEFAULT '',
                    country TEXT NOT NULL DEFAULT '',
                    device TEXT NOT NULL DEFAULT '',
                    clicks INTEGER NOT NULL DEFAULT 0,
                    impressions INTEGER NOT NULL DEFAULT 0,
                    ctr REAL NOT NULL DEFAULT 0,
                    position REAL NOT NULL DEFAULT 0,
                    source_date TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(normalized_query, page, country, device, source_date)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS external_seo_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    term TEXT NOT NULL,
                    normalized_term TEXT NOT NULL,
                    term_type TEXT NOT NULL DEFAULT 'query',
                    provider TEXT NOT NULL DEFAULT 'internal',
                    source TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT '',
                    subcategory TEXT NOT NULL DEFAULT '',
                    confidence INTEGER NOT NULL DEFAULT 0,
                    approved INTEGER NOT NULL DEFAULT 0,
                    evidence_count INTEGER NOT NULL DEFAULT 1,
                    notes_json TEXT,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(provider, normalized_term, category, subcategory)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS indexnow_submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL UNIQUE,
                    reason TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    http_status INTEGER,
                    last_error TEXT,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    submitted_at TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS broadcast_chats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_username TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    chat_type TEXT NOT NULL,
                    title TEXT,
                    enabled INTEGER NOT NULL DEFAULT 0,
                    delivery_failure_count INTEGER NOT NULL DEFAULT 0,
                    last_delivery_failure TIMESTAMP,
                    last_delivery_error TEXT,
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    added_by_user_id INTEGER,
                    UNIQUE(bot_username, chat_id)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS user_ips (
                    user_id INTEGER NOT NULL,
                    ip_address TEXT NOT NULL,
                    first_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, ip_address)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS blocked_ips (
                    ip_address TEXT PRIMARY KEY,
                    blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    reason TEXT
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS bot_registry (
                    bot_id TEXT PRIMARY KEY,
                    telegram_bot_id INTEGER NOT NULL UNIQUE,
                    bot_username TEXT,
                    token_hash TEXT NOT NULL,
                    webhook_secret TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS bot_slot_locks (
                    slot_id INTEGER PRIMARY KEY,
                    owner TEXT NOT NULL,
                    lock_until TIMESTAMP NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS canonical_media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    product_id TEXT NOT NULL,
                    media_type TEXT NOT NULL,
                    file_unique_id TEXT NOT NULL UNIQUE,
                    origin_chat_id INTEGER,
                    origin_message_id INTEGER,
                    original_file_id TEXT,
                    mime_type TEXT,
                    file_size INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS media_objects (
                    media_id TEXT PRIMARY KEY,
                    media_owner_id TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    file_unique_id TEXT,
                    product_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    ttl_expiry TIMESTAMP,
                    deleted_at TIMESTAMP,
                    FOREIGN KEY (media_owner_id) REFERENCES bot_registry(bot_id)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS bot_media_file_ids (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bot_identifier TEXT NOT NULL,
                    file_unique_id TEXT NOT NULL,
                    resolved_file_id TEXT NOT NULL,
                    last_verified_at TIMESTAMP,
                    is_valid INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(bot_identifier, file_unique_id)
                )
            """)

            await db.execute("""
                CREATE TABLE IF NOT EXISTS blocked_entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    phone TEXT,
                    ip_address TEXT,
                    device_fingerprint TEXT,
                    reason TEXT,
                    blocked_by_admin INTEGER,
                    blocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    is_permanent INTEGER DEFAULT 1
                )
            """)

            # Set default order contact if not exists
            await db.execute("""
                INSERT OR IGNORE INTO bot_settings (key, value)
                VALUES ('order_contact', ?)
            """, (DEFAULT_ORDER_CONTACT,))
            
            # Add columns if they don't exist (for existing databases)
            # Schema migrations defined as (table, column, definition, log_on_add)
            migrations = [
                ("products", "media_group_id", "TEXT", False),
                ("products", "additional_file_ids", "TEXT", False),
                ("products", "category", "TEXT", False),
                ("products", "subcategory", "TEXT", False),
                ("products", "bot_username", "TEXT", False),
                ("products", "additional_message_ids", "TEXT", True),
                ("products", "compressed_file_id", "TEXT", True),
                ("bot_users", "notifications_enabled", "INTEGER DEFAULT 1", False),
                ("bot_users", "bot_username", "TEXT", False),
                ("bot_users", "is_blocked", "INTEGER DEFAULT 0", False),
                ("bot_users", "language", "TEXT DEFAULT 'en'", True),
                ("bot_users", "is_subscribed", "INTEGER DEFAULT 1", True),
                ("notification_queue", "claimed_at", "TIMESTAMP", True),
                ("notification_queue", "claim_token", "TEXT", True),
                ("notification_queue", "claimed_by", "TEXT", True),
                ("inline_queries", "latency_ms", "INTEGER", True),
                ("inline_queries", "results_count", "INTEGER", True),
                ("inline_queries", "is_empty_query", "INTEGER DEFAULT 0", True),
                ("inline_queries", "query_length", "INTEGER", True),
                ("bot_file_id_cache", "slot_id", "INTEGER", True),
                ("bot_file_id_cache", "identity_hash", "TEXT", True),
                ("bot_media_file_ids", "slot_id", "INTEGER", True),
                ("bot_media_file_ids", "identity_hash", "TEXT", True),
                ("bot_instances", "slot_id", "INTEGER", True),
                ("bot_instances", "slot_name", "TEXT", True),
                ("bot_instances", "identity_version", "INTEGER DEFAULT 1", True),
                ("bot_instances", "identity_hash", "TEXT", True),
                ("bot_instances", "webhook_secret", "TEXT", True),
                ("bot_instances", "telegram_username", "TEXT", True),
                ("broadcast_chats", "delivery_failure_count", "INTEGER NOT NULL DEFAULT 0", True),
                ("broadcast_chats", "last_delivery_failure", "TIMESTAMP", True),
                ("broadcast_chats", "last_delivery_error", "TEXT", True),
                ("search_query_events", "language", "TEXT NOT NULL DEFAULT ''", True),
                ("seo_query_mappings", "language", "TEXT NOT NULL DEFAULT ''", True),
            ]

            for table, column, definition, log_on_add in migrations:
                try:
                    await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                    if log_on_add:
                        logger.info(f"Added '{column}' column to {table} table")
                except aiosqlite.OperationalError as e:
                    if "duplicate column" in str(e).lower():
                        if log_on_add:
                            logger.debug(f"Column '{column}' already exists in {table} table")
                    else:
                        logger.warning(f"Could not add '{column}' column to {table}: {e}")

            await self._init_product_search_index(db)
            
            # Migrate existing media groups to populate additional_message_ids
            # For media groups created before this fix, infer message IDs from sequential ordering
            logger.info("Checking for media groups needing message ID migration...")
            await self._migrate_existing_media_groups(db)
            
            # Check for products without bot_username
            logger.info("Checking for products needing bot_username migration...")
            await self._migrate_bot_username_for_existing_products(db)
            
            # Clean up duplicates before adding unique indexes
            logger.info("Removing duplicate notification queue entries...")
            dedupe_success = await self._dedupe_notification_queue(db)
            
            # Create performance-critical indexes
            logger.info("Creating database indexes...")
            
            # Products table indexes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_products_category ON products(category)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_products_category_subcategory ON products(category, subcategory)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_products_created_at ON products(created_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_products_media_group_id ON products(media_group_id)")
            # Composite covering indexes for the most frequent sorted query patterns
            await db.execute("CREATE INDEX IF NOT EXISTS idx_products_category_created ON products(category, created_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_products_cat_subcat_created ON products(category, subcategory, created_at DESC)")
            
            # Bot users indexes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_bot_users_notifications ON bot_users(notifications_enabled) WHERE notifications_enabled = 1")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_bot_users_last_seen ON bot_users(last_seen DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_bot_users_bot_username ON bot_users(bot_username)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_bot_users_user_id_bot ON bot_users(user_id, bot_username)")
            
            # Notification queue indexes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_notification_queue_user_sent ON notification_queue(user_id, sent_at)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_notification_queue_created ON notification_queue(created_at)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_notification_queue_claimed ON notification_queue(claimed_at, sent_at)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_notification_queue_claim_token ON notification_queue(claim_token)")

            # Critical: Create unique index for duplicate prevention
            # If this fails, the notification system cannot guarantee deduplication
            try:
                await db.execute("""
                    CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_queue_user_product_unsent 
                    ON notification_queue(user_id, product_id) 
                    WHERE sent_at IS NULL
                """)
                logger.info("Successfully created unique index for notification deduplication")
            except Exception as e:
                error_msg = f"CRITICAL: Failed to create unique index for notification_queue (dedupe_success={dedupe_success}): {e}"
                logger.error(error_msg)
                # If deduplication was successful but index creation failed,
                # this indicates a database issue that needs immediate attention
                if dedupe_success:
                    logger.error("Database may be corrupted or incompatible - unique index creation failed after successful deduplication")
                raise RuntimeError(error_msg)
            
            # Pagination state indexes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_pagination_state_user ON pagination_state(user_id, created_at DESC)")
            
            # Custom message queue indexes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_custom_message_queue_user_sent ON custom_message_queue(user_id, sent_at)")
            
            # Translation cache indexes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_translation_cache_lookup ON translation_cache(source_text, source_lang, target_lang)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_translation_cache_last_used ON translation_cache(last_used DESC)")

            # Bot file ID cache indexes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_bot_file_id_cache_bot_username ON bot_file_id_cache(bot_username)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_bot_file_id_cache_slot ON bot_file_id_cache(slot_id, identity_hash)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_canonical_media_product ON canonical_media(product_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_canonical_media_origin ON canonical_media(origin_chat_id, origin_message_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_bot_media_file_ids_bot ON bot_media_file_ids(bot_identifier)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_bot_media_file_ids_slot ON bot_media_file_ids(slot_id, identity_hash)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_bot_media_file_ids_valid ON bot_media_file_ids(bot_identifier, is_valid)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_media_objects_owner ON media_objects(media_owner_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_media_objects_ttl ON media_objects(ttl_expiry, deleted_at)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_blocked_entities_user ON blocked_entities(telegram_user_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_blocked_entities_ip ON blocked_entities(ip_address)")
            await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_blocked_entities_user_perm ON blocked_entities(telegram_user_id) WHERE telegram_user_id IS NOT NULL AND is_permanent = 1")
            await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_blocked_entities_ip_perm ON blocked_entities(ip_address) WHERE ip_address IS NOT NULL AND is_permanent = 1")

            # Bot users language index
            await db.execute("CREATE INDEX IF NOT EXISTS idx_bot_users_language_code ON bot_users(language)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_webapp_events_created ON webapp_events(created_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_webapp_events_user ON webapp_events(user_id, created_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_webapp_events_type_action ON webapp_events(event_type, action)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_visitor_events_created ON visitor_events(created_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_visitor_events_visitor ON visitor_events(visitor_hash, created_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_visitor_events_source ON visitor_events(source, page_type, created_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_visitor_events_location ON visitor_events(country, region, city, created_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_search_query_events_created ON search_query_events(created_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_search_query_events_matched ON search_query_events(matched_count, created_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_search_query_events_autolearn ON search_query_events(matched_count, normalized_query, created_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_seo_query_mappings_approved ON seo_query_mappings(approved, normalized_term)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_seo_query_mappings_taxonomy ON seo_query_mappings(category, subcategory)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_seo_query_mappings_language ON seo_query_mappings(language, category, subcategory, approved)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_search_query_events_language ON search_query_events(language, created_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_search_console_query_events_date ON search_console_query_events(source_date DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_search_console_query_events_query ON search_console_query_events(normalized_query, source_date DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_external_seo_signals_approved ON external_seo_signals(approved, confidence DESC, last_seen DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_external_seo_signals_taxonomy ON external_seo_signals(category, subcategory, approved)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_indexnow_submissions_status ON indexnow_submissions(status, updated_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_indexnow_submissions_submitted ON indexnow_submissions(submitted_at DESC)")

            # Broadcast chats indexes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_chats_bot_username ON broadcast_chats(bot_username)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_broadcast_chats_enabled ON broadcast_chats(bot_username, enabled) WHERE enabled = 1")

            logger.info("Database indexes created successfully")
            
            await db.commit()
            logger.info("Database initialized successfully")

    async def _init_product_search_index(self, db):
        """Create and backfill the optional FTS5 product search index."""
        try:
            await db.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS product_search USING fts5(
                    caption,
                    category,
                    subcategory,
                    content='products',
                    content_rowid='id',
                    tokenize='unicode61'
                )
            """)
            await db.execute("""
                CREATE TRIGGER IF NOT EXISTS products_ai_search AFTER INSERT ON products BEGIN
                    INSERT INTO product_search(rowid, caption, category, subcategory)
                    VALUES (new.id, coalesce(new.caption, ''), coalesce(new.category, ''), coalesce(new.subcategory, ''));
                END
            """)
            await db.execute("""
                CREATE TRIGGER IF NOT EXISTS products_ad_search AFTER DELETE ON products BEGIN
                    INSERT INTO product_search(product_search, rowid, caption, category, subcategory)
                    VALUES ('delete', old.id, coalesce(old.caption, ''), coalesce(old.category, ''), coalesce(old.subcategory, ''));
                END
            """)
            await db.execute("""
                CREATE TRIGGER IF NOT EXISTS products_au_search AFTER UPDATE ON products BEGIN
                    INSERT INTO product_search(product_search, rowid, caption, category, subcategory)
                    VALUES ('delete', old.id, coalesce(old.caption, ''), coalesce(old.category, ''), coalesce(old.subcategory, ''));
                    INSERT INTO product_search(rowid, caption, category, subcategory)
                    VALUES (new.id, coalesce(new.caption, ''), coalesce(new.category, ''), coalesce(new.subcategory, ''));
                END
            """)
            await db.execute("""
                INSERT INTO product_search(rowid, caption, category, subcategory)
                SELECT id, coalesce(caption, ''), coalesce(category, ''), coalesce(subcategory, '')
                FROM products
                WHERE id NOT IN (SELECT rowid FROM product_search)
            """)
            logger.info("Product FTS search index initialized")
        except aiosqlite.OperationalError as e:
            logger.warning("SQLite FTS5 unavailable; falling back to LIKE search: %s", e)
            try:
                await db.execute("DROP TRIGGER IF EXISTS products_ai_search")
                await db.execute("DROP TRIGGER IF EXISTS products_ad_search")
                await db.execute("DROP TRIGGER IF EXISTS products_au_search")
                await db.execute("DROP TABLE IF EXISTS product_search")
            except aiosqlite.OperationalError:
                pass

    @staticmethod
    def _build_fts_query(query: str) -> Optional[str]:
        """Build a conservative FTS5 prefix query from user input."""
        terms = re.findall(r"[A-Za-z0-9_]+", (query or "").lower())
        if not terms:
            return None
        return " ".join(f"{term}*" for term in terms[:8])

    async def _has_product_search_index(self, db) -> bool:
        """Return True when the optional FTS5 table exists."""
        async with db.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'product_search'"
        ) as cursor:
            return await cursor.fetchone() is not None
    
    async def _dedupe_notification_queue(self, db):
        """
        Remove duplicate notifications, keeping the earliest entry for each
        (user_id, product_id, sent_at IS NULL) combination.
        """
        try:
            await db.execute("""
                DELETE FROM notification_queue
                WHERE sent_at IS NULL
                  AND id NOT IN (
                      SELECT MIN(id) FROM notification_queue
                      WHERE sent_at IS NULL
                      GROUP BY user_id, product_id
                  )
            """)
            return True
        except Exception as e:
            logger.warning(f"Could not dedupe notification_queue: {e}")
            return False
    
    async def _migrate_existing_media_groups(self, db):
        """
        Migrate existing media groups to populate additional_message_ids.
        For media groups created before this fix, we infer message IDs based on
        the assumption that media group messages are sent sequentially.
        """
        try:
            # Find all products with additional_file_ids but no additional_message_ids
            cursor = await db.execute("""
                SELECT id, message_id, additional_file_ids
                FROM products
                WHERE additional_file_ids IS NOT NULL 
                  AND additional_file_ids != ''
                  AND (additional_message_ids IS NULL OR additional_message_ids = '')
            """)
            products_to_migrate = await cursor.fetchall()
            
            if not products_to_migrate:
                logger.info("No media groups need message ID migration")
                return
            
            logger.info(f"Found {len(products_to_migrate)} media groups needing message ID migration")
            
            migrated_count = 0
            for product_id, first_message_id, additional_file_ids_json in products_to_migrate:
                try:
                    # Parse the additional file IDs to get the count
                    import json
                    file_data = json.loads(additional_file_ids_json)
                    num_additional_files = len(file_data)
                    
                    # Infer sequential message IDs starting from first_message_id + 1
                    # This assumes Telegram sends media group messages sequentially
                    additional_msg_ids = [first_message_id + i + 1 for i in range(num_additional_files)]
                    additional_msg_ids_json = json.dumps(additional_msg_ids)
                    
                    # Update the product
                    await db.execute("""
                        UPDATE products 
                        SET additional_message_ids = ?
                        WHERE id = ?
                    """, (additional_msg_ids_json, product_id))
                    
                    migrated_count += 1
                    logger.debug(f"Migrated product {product_id}: inferred {num_additional_files} message IDs")
                    
                except Exception as e:
                    logger.warning(f"Failed to migrate product {product_id}: {e}")
                    continue
            
            await db.commit()
            logger.info(f"Successfully migrated {migrated_count} media groups with inferred message IDs")
            
        except Exception as e:
            logger.error(f"Error during media group migration: {e}")
    
    async def _migrate_bot_username_for_existing_products(self, db):
        """
        Migrate existing products to populate bot_username.
        For products created before this fix, we set bot_username to None
        which will trigger bot-specific file ID resolution on first access.
        """
        try:
            # Count products without bot_username
            cursor = await db.execute("""
                SELECT COUNT(*) FROM products
                WHERE bot_username IS NULL
            """)
            row = await cursor.fetchone()
            count = row[0] if row else 0
            
            if count == 0:
                logger.info("All products already have bot_username populated")
                return
            
            logger.info(f"Found {count} products without bot_username - they will use fallback file ID resolution")
            # Note: We don't need to do anything here - products with NULL bot_username
            # will automatically trigger bot-specific file ID resolution when accessed
            
        except Exception as e:
            logger.error(f"Error checking bot_username migration: {e}")
    
    async def add_product(
        self,
        file_id: str,
        file_type: str,
        caption: Optional[str],
        message_id: int,
        chat_id: int,
        media_group_id: Optional[str] = None,
        additional_file_ids: Optional[str] = None,
        additional_message_ids: Optional[str] = None,
        category: Optional[str] = None,
        subcategory: Optional[str] = None,
        bot_username: Optional[str] = None
    ) -> int:
        """Add a new product and let SQLite allocate a stable product ID."""
        async with self.get_connection() as db:
            try:
                cursor = await db.execute("""
                    INSERT INTO products (file_id, file_type, caption, message_id, chat_id,
                                        media_group_id, additional_file_ids, additional_message_ids, category, subcategory, bot_username, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (file_id, file_type, caption or "", message_id, chat_id,
                      media_group_id, additional_file_ids, additional_message_ids, category, subcategory,
                      self.normalize_bot_username(bot_username), datetime.now()))
                await db.commit()
                product_id = int(cursor.lastrowid)
                logger.info(f"Product added: ID={product_id}, message_id={message_id}, category={category}, bot={bot_username}")
                return product_id
            except aiosqlite.IntegrityError:
                logger.warning(f"Product already exists: message_id={message_id}, chat_id={chat_id}")
                return -1
    
    async def get_product(self, product_id: int) -> Optional[Dict[str, Any]]:
        """Get a product by ID."""
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM products WHERE id = ?
            """, (product_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
                return None

    async def get_deleted_product_redirect(self, product_id: int) -> Optional[Dict[str, Any]]:
        """Get redirect metadata for a product that has been deleted."""
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM deleted_product_redirects WHERE product_id = ?
            """, (product_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
    
    async def get_all_products(self, limit: int = None, offset: int = 0) -> List[Dict[str, Any]]:
        """Get all products with pagination."""
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM products ORDER BY created_at DESC"
            if limit:
                query += f" LIMIT {limit} OFFSET {offset}"
            async with db.execute(query) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    
    async def count_products(self) -> int:
        """Get total number of products."""
        async with self.get_connection() as db:
            async with db.execute("SELECT COUNT(*) FROM products") as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
    
    async def count_products_excluding_categories(self, excluded_categories: List[str]) -> int:
        """
        Count products excluding those in specified categories.
        Products with no category assigned (NULL or empty string) are included.
        
        Args:
            excluded_categories: List of category names to exclude
        
        Returns:
            Count of products excluding specified categories
        """
        async with self.get_connection() as db:
            if not excluded_categories:
                # If no categories to exclude, count all products
                async with db.execute("SELECT COUNT(*) FROM products") as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else 0
            
            # Build placeholders for SQL query
            placeholders = ','.join('?' * len(excluded_categories))
            query = f"""
                SELECT COUNT(*) FROM products 
                WHERE (category IS NULL OR category = '') OR category NOT IN ({placeholders})
            """
            
            async with db.execute(query, excluded_categories) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
    
    async def search_products(self, query: str, limit: int = None, offset: int = 0) -> List[Dict[str, Any]]:
        """Search products by caption (for fuzzy search filtering).
        Excludes products from Dated Proofs, Client Touchdowns, and Announcements categories.
        """
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            sql_query = f"""
                SELECT * FROM products 
                WHERE caption LIKE ?
                  AND (category IS NULL OR category = '' OR category NOT IN ({_EXCLUDED_PLACEHOLDERS}))
                ORDER BY created_at DESC
            """
            if limit:
                sql_query += f" LIMIT {limit} OFFSET {offset}"
            async with db.execute(sql_query, (f"%{query}%", *EXCLUDED_FROM_ALL_PRODUCTS)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def search_products_filtered(
        self,
        query: str,
        excluded_categories: List[str],
        limit: int = 24,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Search caption, category, and subcategory while excluding certain categories.

        Uses the optimized connection (busy_timeout + cache_size) to avoid
        repeating PRAGMA setup at every call site.
        """
        pattern = f"%{query.lower()}%"
        placeholders = ",".join("?" * len(excluded_categories))
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            fts_query = self._build_fts_query(query)
            if fts_query and await self._has_product_search_index(db):
                try:
                    sql = f"""
                        SELECT p.*
                        FROM product_search
                        JOIN products p ON p.id = product_search.rowid
                        WHERE product_search MATCH ?
                          AND (p.category IS NULL OR p.category = '' OR p.category NOT IN ({placeholders}))
                        ORDER BY p.created_at DESC
                        LIMIT ? OFFSET ?
                    """
                    async with db.execute(
                        sql, (fts_query, *excluded_categories, limit, offset)
                    ) as cursor:
                        rows = await cursor.fetchall()
                        return [dict(row) for row in rows]
                except aiosqlite.OperationalError as e:
                    logger.warning("FTS product search failed; falling back to LIKE search: %s", e)

            sql = f"""
                SELECT * FROM products
                WHERE (LOWER(caption) LIKE ? OR LOWER(category) LIKE ? OR LOWER(subcategory) LIKE ?)
                  AND (category IS NULL OR category = '' OR category NOT IN ({placeholders}))
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """
            async with db.execute(
                sql, (pattern, pattern, pattern, *excluded_categories, limit, offset)
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def count_products_filtered(
        self,
        query: str,
        excluded_categories: List[str],
    ) -> int:
        """Count products matching search_products_filtered criteria."""
        pattern = f"%{query.lower()}%"
        placeholders = ",".join("?" * len(excluded_categories))
        async with self.get_connection() as db:
            fts_query = self._build_fts_query(query)
            if fts_query and await self._has_product_search_index(db):
                try:
                    sql = f"""
                        SELECT COUNT(*)
                        FROM product_search
                        JOIN products p ON p.id = product_search.rowid
                        WHERE product_search MATCH ?
                          AND (p.category IS NULL OR p.category = '' OR p.category NOT IN ({placeholders}))
                    """
                    async with db.execute(sql, (fts_query, *excluded_categories)) as cursor:
                        row = await cursor.fetchone()
                        return row[0] if row else 0
                except aiosqlite.OperationalError as e:
                    logger.warning("FTS product count failed; falling back to LIKE search: %s", e)

            sql = f"""
                SELECT COUNT(*) FROM products
                WHERE (LOWER(caption) LIKE ? OR LOWER(category) LIKE ? OR LOWER(subcategory) LIKE ?)
                  AND (category IS NULL OR category = '' OR category NOT IN ({placeholders}))
            """
            async with db.execute(sql, (pattern, pattern, pattern, *excluded_categories)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def get_user_subscription(self, user_id: int) -> Optional[bool]:
        """Return the is_subscribed flag for user_id, or None if user not found."""
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT is_subscribed FROM bot_users WHERE user_id = ?", (user_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return bool(row["is_subscribed"]) if row else None

    async def update_user_subscription(self, user_id: int, is_subscribed: bool) -> None:
        """Upsert the is_subscribed flag for user_id (atomic INSERT OR REPLACE)."""
        value = 1 if is_subscribed else 0
        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO bot_users (user_id, is_subscribed) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET is_subscribed = excluded.is_subscribed
                """,
                (user_id, value),
            )
            await db.commit()
    
    async def get_all_products_for_search(self) -> List[Dict[str, Any]]:
        """
        Get all products for fuzzy search (no pagination).
        Excludes products from Dated Proofs, Client Touchdowns, and Announcements categories.
        Only fetches columns needed for search and inline result building.
        """
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row

            query = f"""
                SELECT id, caption, category, subcategory, file_id, file_type,
                       bot_username, chat_id, message_id, media_group_id,
                       additional_file_ids, additional_message_ids, created_at
                FROM products
                WHERE (category IS NULL OR category = '' OR category NOT IN ({_EXCLUDED_PLACEHOLDERS}))
                ORDER BY created_at DESC
            """

            async with db.execute(query, EXCLUDED_FROM_ALL_PRODUCTS) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    
    async def get_all_products_excluding_categories(self, excluded_categories: List[str], limit: int = None, offset: int = 0) -> List[Dict[str, Any]]:
        """
        Get all products excluding those in specified categories.
        Products with no category assigned (NULL or empty string) are included.
        
        Args:
            excluded_categories: List of category names to exclude (e.g., ['DATEDPROOFS', 'CLIENTTOUCHDOWNS'])
            limit: Maximum number of products to return (optional)
            offset: Number of products to skip (for pagination, default 0)
        
        Returns:
            List of product dictionaries excluding products from specified categories
        """
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            
            if not excluded_categories:
                # If no categories to exclude, return all products
                query = "SELECT * FROM products ORDER BY created_at DESC"
                if limit is not None:
                    query += f" LIMIT {limit} OFFSET {offset}"
                async with db.execute(query) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
            
            # Build placeholders for SQL query
            placeholders = ','.join('?' * len(excluded_categories))
            query = f"""
                SELECT * FROM products 
                WHERE (category IS NULL OR category = '') OR category NOT IN ({placeholders})
                ORDER BY created_at DESC
            """
            if limit is not None:
                query += f" LIMIT {limit} OFFSET {offset}"
            
            async with db.execute(query, excluded_categories) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    
    async def delete_product(self, product_id: int) -> bool:
        """Delete a product by ID.

        Space reclamation (VACUUM) is intentionally omitted here.  Running
        VACUUM after every deletion is expensive, blocks all writers, and is
        harmful to latency under load.  SQLite in WAL mode recycles free pages
        automatically, so no explicit VACUUM is needed for normal operation.
        If periodic maintenance is required, call ``run_maintenance()`` from a
        scheduled job instead.
        """
        try:
            async with self.get_connection() as db:
                await db.execute("BEGIN")
                await db.execute("""
                    INSERT OR REPLACE INTO deleted_product_redirects (
                        product_id, caption, category, subcategory, deleted_at
                    )
                    SELECT id, caption, category, subcategory, CURRENT_TIMESTAMP
                    FROM products
                    WHERE id = ?
                """, (product_id,))
                pending_cursor = await db.execute(
                    "DELETE FROM pending_categorization WHERE product_id = ?",
                    (product_id,),
                )
                queue_cursor = await db.execute(
                    "DELETE FROM notification_queue WHERE product_id = ?",
                    (product_id,),
                )
                media_cursor = await db.execute(
                    "DELETE FROM media_groups WHERE product_id = ?",
                    (product_id,),
                )
                cursor = await db.execute("DELETE FROM products WHERE id = ?", (product_id,))
                await db.commit()
                deleted = cursor.rowcount > 0
                if deleted:
                    logger.info(
                        "Product deleted: ID=%s pending_categorizations=%s notifications=%s media_groups=%s",
                        product_id,
                        pending_cursor.rowcount,
                        queue_cursor.rowcount,
                        media_cursor.rowcount,
                    )
                return deleted
        except aiosqlite.DatabaseError as exc:
            if "malformed" in str(exc).lower() or "corrupt" in str(exc).lower():
                logger.critical(
                    "SQLite corruption detected while deleting product_id=%s. "
                    "Run scripts/sqlite_doctor.py against an offline copy of the database before retrying deletes.",
                    product_id,
                    exc_info=True,
                )
            raise
    
    async def get_product_by_message(self, message_id: int, chat_id: int) -> Optional[Dict[str, Any]]:
        """Get product by message_id and chat_id."""
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM products WHERE message_id = ? AND chat_id = ?
            """, (message_id, chat_id)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
                return None
    
    async def save_pagination_state(
        self,
        user_id: int,
        state_type: str,
        query: Optional[str],
        page: int
    ):
        """Save pagination state for a user."""
        async with self.get_connection() as db:
            await db.execute("""
                INSERT OR REPLACE INTO pagination_state (user_id, state_type, query, page, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, state_type, query or "", page, datetime.now()))
            await db.commit()
    
    async def get_pagination_state(
        self,
        user_id: int,
        state_type: str,
        query: Optional[str]
    ) -> Optional[int]:
        """Get pagination state for a user."""
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT page FROM pagination_state 
                WHERE user_id = ? AND state_type = ? AND query = ?
            """, (user_id, state_type, query or "")) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None
    
    async def get_latest_pagination_state(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get the most recent pagination state for a user.
        
        Returns:
            A dict with state_type, query, and page, or None if no state exists.
        """
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT state_type, query, page FROM pagination_state
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT 1
            """, (user_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {
                        "state_type": row["state_type"],
                        "query": row["query"],
                        "page": row["page"]
                    }
                return None
    
    async def cleanup_old_pagination_states(self, minutes: int = 10):
        """Clean up pagination states older than specified minutes."""
        async with self.get_connection() as db:
            await db.execute("""
                DELETE FROM pagination_state 
                WHERE datetime(created_at) < datetime('now', '-' || ? || ' minutes')
            """, (minutes,))
            await db.commit()
    
    async def save_last_search(self, user_id: int, query: str, page: int):
        """Save user's last search query and page."""
        async with self.get_connection() as db:
            await db.execute("""
                INSERT OR REPLACE INTO user_last_search (user_id, query, page, updated_at)
                VALUES (?, ?, ?, ?)
            """, (user_id, query, page, datetime.now()))
            await db.commit()
    
    async def get_last_search(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user's last search query and page."""
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT query, page FROM user_last_search WHERE user_id = ?
            """, (user_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return {"query": row["query"], "page": row["page"]}
                return None
    
    async def add_ignored_message(self, message_id: int, chat_id: int):
        """Add a message to the ignore list (for deleted products)."""
        async with self.get_connection() as db:
            try:
                await db.execute("""
                    INSERT OR IGNORE INTO ignored_messages (message_id, chat_id, created_at)
                    VALUES (?, ?, ?)
                """, (message_id, chat_id, datetime.now()))
                await db.commit()
                logger.info(f"Added message {message_id} from chat {chat_id} to ignore list")
            except Exception as e:
                logger.error(f"Error adding to ignore list: {e}")
    
    async def is_message_ignored(self, message_id: int, chat_id: int) -> bool:
        """Check if a message is in the ignore list."""
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT 1 FROM ignored_messages 
                WHERE message_id = ? AND chat_id = ?
            """, (message_id, chat_id)) as cursor:
                row = await cursor.fetchone()
                return row is not None
    
    async def get_or_create_media_group_product(self, media_group_id: str, chat_id: int) -> Optional[int]:
        """Get existing product_id for a media group or return None if it doesn't exist."""
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT product_id FROM media_groups 
                WHERE media_group_id = ? AND chat_id = ?
            """, (media_group_id, chat_id)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None
    
    async def register_media_group(self, media_group_id: str, chat_id: int, product_id: int):
        """Register a media group with its product."""
        async with self.get_connection() as db:
            await db.execute("""
                INSERT OR REPLACE INTO media_groups (media_group_id, chat_id, product_id, created_at)
                VALUES (?, ?, ?, ?)
            """, (media_group_id, chat_id, product_id, datetime.now()))
            await db.commit()
    
    async def update_product_media(self, product_id: int, additional_file_ids: str, additional_message_ids: str = None):
        """Update a product's additional file IDs and message IDs for media groups."""
        async with self.get_connection() as db:
            await db.execute("""
                UPDATE products SET additional_file_ids = ?, additional_message_ids = ? WHERE id = ?
            """, (additional_file_ids, additional_message_ids, product_id))
            await db.commit()
    
    async def update_compressed_file_id(self, product_id: int, compressed_file_id: str):
        """
        Store compressed video file ID for a product.
        
        Args:
            product_id: Product ID
            compressed_file_id: Telegram file_id of the compressed video
        """
        async with self.get_connection() as db:
            await db.execute("""
                UPDATE products SET compressed_file_id = ? WHERE id = ?
            """, (compressed_file_id, product_id))
            await db.commit()
            logger.info(f"Updated compressed_file_id for product {product_id}")
    
    async def get_compressed_file_id(self, product_id: int) -> Optional[str]:
        """
        Get compressed video file ID for a product.
        
        Args:
            product_id: Product ID
            
        Returns:
            Compressed file_id or None if not available
        """
        try:
            async with self.get_connection() as db:
                async with db.execute("""
                    SELECT compressed_file_id FROM products WHERE id = ?
                """, (product_id,)) as cursor:
                    row = await cursor.fetchone()
                    if row and row[0]:
                        return row[0]
                    return None
        except Exception as e:
            logger.exception(f"Error getting compressed file_id for product {product_id}: {e}")
            return None
    
    async def get_products_by_category(self, category: str, limit: int = None, offset: int = 0) -> List[Dict[str, Any]]:
        """Get products filtered by category."""
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM products WHERE category = ? ORDER BY created_at DESC"
            if limit:
                query += f" LIMIT {limit} OFFSET {offset}"
            async with db.execute(query, (category,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_related_products(
        self,
        product_id: int,
        category: str,
        subcategory: Optional[str] = None,
        limit: int = 6,
    ) -> List[Dict[str, Any]]:
        """Get other products in the same category for public product pages."""
        if not category:
            return []

        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            params: list[Any] = [category, product_id]
            same_subcategory_order = "1"
            if subcategory:
                same_subcategory_order = "CASE WHEN LOWER(TRIM(COALESCE(subcategory, ''))) = LOWER(TRIM(?)) THEN 0 ELSE 1 END"
                params.append(subcategory)

            params.append(limit)
            async with db.execute(f"""
                SELECT *
                FROM products
                WHERE LOWER(TRIM(COALESCE(category, ''))) = LOWER(TRIM(?))
                  AND id != ?
                ORDER BY {same_subcategory_order}, created_at DESC, id DESC
                LIMIT ?
            """, tuple(params)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    
    async def count_products_by_category(self, category: str) -> int:
        """Count products in a category."""
        async with self.get_connection() as db:
            async with db.execute("SELECT COUNT(*) FROM products WHERE category = ?", (category,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def get_subcategory_summary(self, category: str) -> tuple[List[Dict[str, Any]], int]:
        """Return subcategory counts and total category count using one connection."""
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT subcategory, COUNT(*) as count
                FROM products
                WHERE category = ? AND subcategory IS NOT NULL
                GROUP BY subcategory
                ORDER BY subcategory
            """, (category,)) as cursor:
                rows = await cursor.fetchall()
                subcategory_counts = [
                    {"subcategory": row[0], "count": row[1]}
                    for row in rows
                ]

            async with db.execute(
                "SELECT COUNT(*) FROM products WHERE category = ?",
                (category,),
            ) as cursor:
                row = await cursor.fetchone()
                total = row[0] if row else 0

            return subcategory_counts, total
    
    async def get_all_category_counts(self) -> Dict[str, int]:
        """Get product counts for all categories in a single query (optimized)."""
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT category, COUNT(*) as count 
                FROM products 
                WHERE category IS NOT NULL 
                GROUP BY category
            """) as cursor:
                rows = await cursor.fetchall()
                return {row[0]: row[1] for row in rows}
    
    async def get_all_categories(self) -> List[str]:
        """Get list of all unique categories."""
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT DISTINCT category FROM products 
                WHERE category IS NOT NULL 
                ORDER BY category
            """) as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]
    
    async def get_products_by_category_and_subcategory(
        self, category: str, subcategory: str, limit: int = None, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Get products filtered by category and subcategory."""
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM products WHERE category = ? AND subcategory = ? ORDER BY created_at DESC"
            if limit:
                query += f" LIMIT {limit} OFFSET {offset}"
            async with db.execute(query, (category, subcategory)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    
    async def count_products_by_category_and_subcategory(self, category: str, subcategory: str) -> int:
        """Count products in a category and subcategory."""
        async with self.get_connection() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM products WHERE category = ? AND subcategory = ?",
                (category, subcategory)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
    
    async def get_subcategories_with_counts(self, category: str) -> List[Dict[str, Any]]:
        """Get subcategories for a category with product counts."""
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT subcategory, COUNT(*) as count
                FROM products
                WHERE category = ? AND subcategory IS NOT NULL
                GROUP BY subcategory
                ORDER BY subcategory
            """, (category,)) as cursor:
                rows = await cursor.fetchall()
                return [{"subcategory": row[0], "count": row[1]} for row in rows]
    
    async def get_all_subcategories_with_counts(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get all subcategories for all categories with product counts in a single query."""
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT category, subcategory, COUNT(*) as count
                FROM products
                WHERE subcategory IS NOT NULL
                GROUP BY category, subcategory
                ORDER BY category, subcategory
            """) as cursor:
                rows = await cursor.fetchall()
                # Group by category
                result = {}
                for row in rows:
                    category = row[0]
                    if category not in result:
                        result[category] = []
                    result[category].append({"subcategory": row[1], "count": row[2]})
                return result

    async def get_category_summary(self, excluded_categories: List[str]) -> tuple[Dict[str, int], Dict[str, List[Dict[str, Any]]], int]:
        """Return category counts, subcategory counts, and storefront total in one connection."""
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT category, COUNT(*) as count
                FROM products
                WHERE category IS NOT NULL
                GROUP BY category
            """) as cursor:
                rows = await cursor.fetchall()
                category_counts = {row[0]: row[1] for row in rows}

            async with db.execute("""
                SELECT category, subcategory, COUNT(*) as count
                FROM products
                WHERE subcategory IS NOT NULL
                GROUP BY category, subcategory
                ORDER BY category, subcategory
            """) as cursor:
                rows = await cursor.fetchall()
                subcategory_counts: Dict[str, List[Dict[str, Any]]] = {}
                for row in rows:
                    subcategory_counts.setdefault(row[0], []).append({
                        "subcategory": row[1],
                        "count": row[2],
                    })

            if excluded_categories:
                placeholders = ",".join("?" * len(excluded_categories))
                total_sql = f"""
                    SELECT COUNT(*) FROM products
                    WHERE category IS NULL OR category = '' OR category NOT IN ({placeholders})
                """
                total_params = excluded_categories
            else:
                total_sql = "SELECT COUNT(*) FROM products"
                total_params = []

            async with db.execute(total_sql, total_params) as cursor:
                row = await cursor.fetchone()
                all_products_count = row[0] if row else 0

            return category_counts, subcategory_counts, all_products_count
    
    async def update_product_category(self, product_id: int, category: str, subcategory: Optional[str] = None):
        """Update a product's category, subcategory, and timestamp.

        Updates created_at to current time so recategorized products move up in lists,
        indicating fresh or restocked inventory.
        """
        async with self.get_connection() as db:
            await db.execute("""
                UPDATE products SET category = ?, subcategory = ?, created_at = ? WHERE id = ?
            """, (category, subcategory, datetime.now(), product_id))
            await db.commit()

            # Remove from pending categorization if it exists
            await db.execute("""
                DELETE FROM pending_categorization WHERE product_id = ?
            """, (product_id,))
            await db.commit()
    
    async def add_pending_categorization(self, product_id: int):
        """Mark a product as needing categorization."""
        async with self.get_connection() as db:
            await db.execute("""
                INSERT OR IGNORE INTO pending_categorization (product_id, notified_at)
                VALUES (?, ?)
            """, (product_id, datetime.now()))
            await db.commit()
    
    async def remove_pending_categorization(self, product_id: int):
        """Remove a product from pending categorization."""
        async with self.get_connection() as db:
            await db.execute("""
                DELETE FROM pending_categorization WHERE product_id = ?
            """, (product_id,))
            await db.commit()
    
    async def get_pending_categorizations(self) -> List[int]:
        """Get list of product IDs that need categorization."""
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT product_id FROM pending_categorization ORDER BY notified_at
            """) as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]
    
    async def track_user(self, user_id: int, username: Optional[str] = None, 
                        first_name: Optional[str] = None, last_name: Optional[str] = None,
                        bot_username: Optional[str] = None):
        """
        Track or update a bot user.
        
        Note: bot_username is only set/updated if the database value is NULL.
        - On first interaction: bot_username is recorded
        - On subsequent interactions: bot_username is preserved (not overwritten)
        - This ensures analytics show which bot the user first interacted with
        """
        if await self.is_entity_blocked(telegram_user_id=user_id, username=username):
            logger.info("[block] denied_registration user_id=%s username=%s", user_id, username or "")
            return
        async with self.get_connection() as db:
            # Check if user exists
            async with db.execute("SELECT user_id FROM bot_users WHERE user_id = ?", (user_id,)) as cursor:
                exists = await cursor.fetchone()
            
            if exists:
                # Update existing user
                await db.execute("""
                    UPDATE bot_users 
                    SET username = ?, first_name = ?, last_name = ?, 
                        last_seen = ?, interaction_count = interaction_count + 1,
                        bot_username = COALESCE(?, bot_username)
                    WHERE user_id = ?
                """, (username, first_name, last_name, datetime.now(), bot_username, user_id))
            else:
                # Insert new user
                await db.execute("""
                    INSERT INTO bot_users (user_id, username, first_name, last_name, first_seen, last_seen, bot_username)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (user_id, username, first_name, last_name, datetime.now(), datetime.now(), bot_username))
            
            await db.commit()
    
    async def get_all_users(self) -> List[Dict[str, Any]]:
        """Get all bot users."""
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM bot_users ORDER BY last_seen DESC
            """) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    
    async def get_users_paginated(self, limit: int = 10, offset: int = 0) -> tuple[int, List[Dict[str, Any]]]:
        """
        Get paginated list of bot users with total count.
        
        Args:
            limit: Number of users per page
            offset: Number of users to skip
        
        Returns:
            Tuple of (total_count, users_list)
        """
        async with self.get_connection() as db:
            # Get total count
            async with db.execute("SELECT COUNT(*) FROM bot_users") as cursor:
                total = (await cursor.fetchone())[0]
            
            # Get paginated results
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM bot_users ORDER BY last_seen DESC LIMIT ? OFFSET ?
            """, (limit, offset)) as cursor:
                rows = await cursor.fetchall()
                return total, [dict(row) for row in rows]
    
    async def count_users(self) -> int:
        """Get total number of users."""
        async with self.get_connection() as db:
            async with db.execute("SELECT COUNT(*) FROM bot_users") as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
    
    async def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user details by user ID."""
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM bot_users WHERE user_id = ?
            """, (user_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
    
    async def get_bot_username_for_user(self, user_id: int) -> Optional[str]:
        """
        Get the bot_username for a specific user.
        Returns None if user not found or bot_username is NULL.
        """
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT bot_username FROM bot_users WHERE user_id = ?
            """, (user_id,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def get_bot_usernames_for_users(self, user_ids: List[int]) -> Dict[int, Optional[str]]:
        """
        Bulk fetch bot usernames for multiple users in a single query.
        Returns a dict mapping user_id -> bot_username (or None if not set).
        """
        if not user_ids:
            return {}
        placeholders = ','.join('?' * len(user_ids))
        async with self.get_connection() as db:
            async with db.execute(f"""
                SELECT user_id, LOWER(bot_username) FROM bot_users WHERE user_id IN ({placeholders})
            """, tuple(user_ids)) as cursor:
                rows = await cursor.fetchall()
        result = {row[0]: row[1] for row in rows}
        for uid in user_ids:
            result.setdefault(uid, None)
        return result
    
    async def user_exists(self, user_id: int) -> bool:
        """Check if a user exists in the database."""
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT user_id FROM bot_users WHERE user_id = ?
            """, (user_id,)) as cursor:
                row = await cursor.fetchone()
                return row is not None
    
    async def set_user_notifications(self, user_id: int, enabled: bool):
        """Enable or disable notifications for a user."""
        async with self.get_connection() as db:
            await db.execute("""
                UPDATE bot_users SET notifications_enabled = ? WHERE user_id = ?
            """, (1 if enabled else 0, user_id))
            await db.commit()
    
    async def is_user_subscribed(self, user_id: int) -> bool:
        """Check if user has notifications enabled."""
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT notifications_enabled FROM bot_users WHERE user_id = ?
            """, (user_id,)) as cursor:
                row = await cursor.fetchone()
                # Default to True if user not found
                return row[0] == 1 if row else True
    
    async def get_subscribed_users(self) -> List[int]:
        """Get list of user IDs with notifications enabled (excluding admins)."""
        from utils.helpers import get_admin_ids
        admin_ids = get_admin_ids()
        
        if not admin_ids:
            # No admins to filter, simple query
            async with self.get_connection() as db:
                async with db.execute("""
                    SELECT user_id FROM bot_users WHERE notifications_enabled = 1
                """) as cursor:
                    rows = await cursor.fetchall()
                    return [row[0] for row in rows]
        
        # Filter admins in SQL for better performance
        # Build placeholders safely - length is controlled by admin_ids count
        num_admins = len(admin_ids)
        placeholders = ','.join(['?'] * num_admins)
        query = f"""
            SELECT user_id FROM bot_users 
            WHERE notifications_enabled = 1 
            AND user_id NOT IN ({placeholders})
        """
        async with self.get_connection() as db:
            async with db.execute(query, admin_ids) as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]
    
    async def get_subscribed_users_by_bot(self) -> Dict[str, List[int]]:
        """
        Get list of user IDs with notifications enabled, grouped by bot_username.
        Returns a dict mapping bot_username to list of user_ids.
        Users without a bot_username are included under None key.
        Excludes admins.
        """
        from utils.helpers import get_admin_ids
        admin_ids = get_admin_ids()
        
        result = defaultdict(list)
        
        if not admin_ids:
            # No admins to filter, simple query
            async with self.get_connection() as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("""
                    SELECT user_id, bot_username FROM bot_users 
                    WHERE notifications_enabled = 1
                """) as cursor:
                    rows = await cursor.fetchall()
                    for row in rows:
                        result[row['bot_username']].append(row['user_id'])
                    return dict(result)
        
        # Filter admins in SQL for better performance
        num_admins = len(admin_ids)
        placeholders = ','.join(['?'] * num_admins)
        query = f"""
            SELECT user_id, bot_username FROM bot_users 
            WHERE notifications_enabled = 1 
            AND user_id NOT IN ({placeholders})
        """
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, admin_ids) as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    result[row['bot_username']].append(row['user_id'])
                return dict(result)
    
    async def get_all_users_by_bot(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get all users grouped by bot_username.
        Returns a dict mapping bot_username to list of user dicts.
        Users without a bot_username are included under None key.
        """
        result = defaultdict(list)
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM bot_users ORDER BY last_seen DESC
            """) as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    user_dict = dict(row)
                    result[user_dict.get('bot_username')].append(user_dict)
                return dict(result)
    
    async def queue_notification(self, user_id: int, product_id: int):
        """
        Add a notification to the queue with duplicate prevention.
        Uses pre-check validation and INSERT OR IGNORE for robust deduplication.
        """
        conn = await self._get_optimized_connection()
        try:
            # Pre-check: Verify if notification already exists
            # This prevents unnecessary INSERT attempts and provides early feedback
            async with conn.execute("""
                SELECT id FROM notification_queue
                WHERE user_id = ? AND product_id = ? AND sent_at IS NULL
            """, (user_id, product_id)) as cursor:
                existing = await cursor.fetchone()
                if existing:
                    logger.debug(f"Notification already queued for user {user_id}, product {product_id} (id={existing[0]})")
                    return  # Skip insertion - no commit needed

            # Insert notification - OR IGNORE provides additional safety
            cursor = await conn.execute("""
                INSERT OR IGNORE INTO notification_queue (user_id, product_id, created_at)
                VALUES (?, ?, ?)
            """, (user_id, product_id, datetime.now()))

            if cursor.rowcount == 0:
                # This should rarely happen due to pre-check, but handles race conditions
                logger.debug(f"Notification insert was ignored (race condition) for user {user_id}, product {product_id}")
            else:
                logger.debug(f"Successfully queued notification for user {user_id}, product {product_id}")

            # Commit only after successful insert
            await conn.commit()

        except aiosqlite.IntegrityError as e:
            # Unique index constraint violation - duplicate detected
            # No commit needed - transaction will be rolled back
            logger.debug(f"Duplicate notification prevented by unique constraint for user {user_id}, product {product_id}: {e}")
        except Exception as e:
            # Log unexpected errors and rollback transaction
            logger.error(f"Unexpected error queueing notification for user {user_id}, product {product_id}: {e}", exc_info=True)
            # Transaction will be rolled back automatically when context exits
        finally:
            await conn.close()

    async def mark_notification_sent(self, notification_id: int):
        """Mark a notification as sent."""
        conn = await self._get_optimized_connection()
        try:
            await conn.execute("""
                UPDATE notification_queue SET sent_at = ? WHERE id = ?
            """, (datetime.now(), notification_id))
            await conn.commit()
        finally:
            await conn.close()

    async def delete_notification(self, notification_id: int):
        """Delete a notification from the queue."""
        conn = await self._get_optimized_connection()
        try:
            await conn.execute("""
                DELETE FROM notification_queue WHERE id = ?
            """, (notification_id,))
            await conn.commit()
        finally:
            await conn.close()
    
    async def get_pending_notifications(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get pending notifications from queue."""
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM notification_queue 
                WHERE sent_at IS NULL 
                ORDER BY created_at ASC 
                LIMIT ?
            """, (limit,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    
    async def get_recent_notifications_count(self, user_id: int, minutes: int = 60) -> int:
        """Count notifications sent to user in the last N minutes."""
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT COUNT(*) FROM notification_queue
                WHERE user_id = ? AND sent_at IS NOT NULL
                AND datetime(sent_at) > datetime('now', '-' || ? || ' minutes')
            """, (user_id, minutes)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def get_recent_notifications_count_bulk(self, user_ids: List[int], minutes: int = 60) -> Dict[int, int]:
        """
        Bulk fetch recent notification counts for multiple users in a single query.
        Returns a dict mapping user_id -> count of notifications sent in the last N minutes.
        """
        if not user_ids:
            return {}
        cutoff = (datetime.now() - timedelta(minutes=minutes)).isoformat()
        placeholders = ','.join('?' * len(user_ids))
        async with self.get_connection() as db:
            async with db.execute(f"""
                SELECT user_id, COUNT(*) as cnt
                FROM notification_queue
                WHERE user_id IN ({placeholders})
                  AND sent_at IS NOT NULL
                  AND sent_at >= ?
                GROUP BY user_id
            """, (*user_ids, cutoff)) as cursor:
                rows = await cursor.fetchall()
        result = {row[0]: row[1] for row in rows}
        for uid in user_ids:
            result.setdefault(uid, 0)
        return result

    async def claim_pending_notifications(
        self,
        limit: int = 100,
        claim_token: Optional[str] = None,
        lease_seconds: int = 300,
        claimed_by: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Atomically claim pending notifications for processing.

        This prevents duplicate processing across concurrent workers by:
        1. Selecting unclaimed or expired notifications
        2. Marking them with a claim token and timestamp
        3. Returning the claimed notifications

        Args:
            limit: Maximum number of notifications to claim
            claim_token: Unique token for this claim (auto-generated if None)
            lease_seconds: How long the claim is valid (default 300s/5min)
            claimed_by: Optional identifier for the claiming instance

        Returns:
            List of claimed notification dicts
        """
        if claim_token is None:
            claim_token = str(uuid.uuid4())

        conn = await self._get_optimized_connection()
        try:
            conn.row_factory = aiosqlite.Row

            # Begin transaction for atomicity
            await conn.execute("BEGIN IMMEDIATE")

            try:
                # Calculate lease expiry time (absolute time, not relative)
                now = datetime.now()
                lease_threshold = now - timedelta(seconds=lease_seconds)

                # Select claimable notifications (not sent, and either unclaimed or lease expired)
                # Use datetime() to convert claimed_at to datetime for comparison
                async with conn.execute("""
                    SELECT * FROM notification_queue
                    WHERE sent_at IS NULL
                      AND (claimed_at IS NULL OR datetime(claimed_at) < datetime(?))
                    ORDER BY created_at ASC
                    LIMIT ?
                """, (lease_threshold.isoformat(), limit)) as cursor:
                    rows = await cursor.fetchall()

                    if not rows:
                        await conn.commit()
                        return []

                    # Extract IDs to claim
                    notification_ids = [row['id'] for row in rows]

                    # Claim these notifications with current timestamp
                    claim_timestamp = now.isoformat()
                    placeholders = ','.join('?' * len(notification_ids))
                    await conn.execute(f"""
                        UPDATE notification_queue
                        SET claimed_at = ?,
                            claim_token = ?,
                            claimed_by = ?
                        WHERE id IN ({placeholders})
                    """, (claim_timestamp, claim_token, claimed_by, *notification_ids))

                    await conn.commit()

                    logger.info(
                        f"Claimed {len(notification_ids)} notifications with token {claim_token[:8]}... "
                        f"(claimed_by: {claimed_by or 'unknown'})"
                    )

                    # Re-fetch the updated rows to return current claim info
                    async with conn.execute(f"""
                        SELECT * FROM notification_queue
                        WHERE id IN ({placeholders})
                        ORDER BY created_at ASC
                    """, notification_ids) as cursor2:
                        updated_rows = await cursor2.fetchall()
                        return [dict(row) for row in updated_rows]

            except Exception as e:
                await conn.rollback()
                logger.error(f"Error claiming notifications: {e}", exc_info=True)
                raise
        finally:
            await conn.close()

    async def release_notification_claim(
        self,
        notification_id: Optional[int] = None,
        claim_token: Optional[str] = None
    ) -> int:
        """
        Release claim on notification(s) so they can be retried.

        Use this when a notification send fails temporarily and should be
        retried later. Either notification_id OR claim_token must be provided.

        Args:
            notification_id: Specific notification to release (optional)
            claim_token: Release all notifications with this claim token (optional)

        Returns:
            Number of notifications released
        """
        if notification_id is None and claim_token is None:
            raise ValueError("Either notification_id or claim_token must be provided")

        conn = await self._get_optimized_connection()
        try:
            if notification_id is not None:
                # Release specific notification
                cursor = await conn.execute("""
                    UPDATE notification_queue
                    SET claimed_at = NULL,
                        claim_token = NULL,
                        claimed_by = NULL
                    WHERE id = ? AND sent_at IS NULL
                """, (notification_id,))
            else:
                # Release all notifications with this claim token
                cursor = await conn.execute("""
                    UPDATE notification_queue
                    SET claimed_at = NULL,
                        claim_token = NULL,
                        claimed_by = NULL
                    WHERE claim_token = ? AND sent_at IS NULL
                """, (claim_token,))

            await conn.commit()
            released_count = cursor.rowcount

            if released_count > 0:
                logger.info(
                    f"Released {released_count} notification(s) "
                    f"(id={notification_id}, token={claim_token[:8] if claim_token else 'N/A'}...)"
                )

            return released_count
        finally:
            await conn.close()

    async def block_user(self, user_id: int) -> list:
        """Block a user from using the bot and block all their known IPs.

        Returns the list of IP addresses that were added to blocked_ips so the
        caller can update any in-memory IP-block registry.
        """
        async with self.get_connection() as db:
            async with db.execute(
                "SELECT username, first_name, last_name FROM bot_users WHERE user_id = ?",
                (user_id,),
            ) as cursor:
                user_row = await cursor.fetchone()
            await db.execute("""
                UPDATE bot_users SET is_blocked = 1 WHERE user_id = ?
            """, (user_id,))
            await db.execute("""
                INSERT OR IGNORE INTO blocked_entities
                (telegram_user_id, username, first_name, last_name, reason, is_permanent)
                VALUES (?, ?, ?, ?, ?, 1)
            """, (
                user_id,
                user_row[0] if user_row else None,
                user_row[1] if user_row else None,
                user_row[2] if user_row else None,
                f"blocked_user:{user_id}",
            ))
            # Collect known IPs for this user
            async with db.execute(
                "SELECT ip_address FROM user_ips WHERE user_id = ?", (user_id,)
            ) as cursor:
                rows = await cursor.fetchall()
            ip_list = [row[0] for row in rows]
            # Persist IP blocks
            if ip_list:
                await db.executemany(
                    "INSERT OR IGNORE INTO blocked_ips (ip_address, reason) VALUES (?, ?)",
                    [(ip, f"blocked_user:{user_id}") for ip in ip_list],
                )
                await db.executemany(
                    """
                    INSERT OR IGNORE INTO blocked_entities
                    (telegram_user_id, ip_address, reason, is_permanent)
                    VALUES (?, ?, ?, 1)
                    """,
                    [(user_id, ip, f"blocked_user:{user_id}") for ip in ip_list],
                )
            await db.commit()
            logger.info("[block] persisted user_id=%s ip_count=%d", user_id, len(ip_list))
        try:
            from utils.access_control import invalidate_block_cache
            invalidate_block_cache(user_id=user_id)
        except Exception:
            logger.debug("Could not invalidate block cache for user %s", user_id, exc_info=True)
        return ip_list

    async def unblock_user(self, user_id: int) -> list:
        """Unblock a user and remove all their known IPs from blocked_ips.

        Returns the list of IP addresses that were removed from blocked_ips so the
        caller can update any in-memory IP-block registry.
        """
        async with self.get_connection() as db:
            await db.execute("""
                UPDATE bot_users SET is_blocked = 0 WHERE user_id = ?
            """, (user_id,))
            await db.execute("DELETE FROM blocked_entities WHERE telegram_user_id = ?", (user_id,))
            async with db.execute(
                "SELECT ip_address FROM user_ips WHERE user_id = ?", (user_id,)
            ) as cursor:
                rows = await cursor.fetchall()
            ip_list = [row[0] for row in rows]
            if ip_list:
                await db.executemany(
                    "DELETE FROM blocked_ips WHERE ip_address = ?",
                    [(ip,) for ip in ip_list],
                )
                await db.executemany(
                    "DELETE FROM blocked_entities WHERE ip_address = ?",
                    [(ip,) for ip in ip_list],
                )
            await db.commit()
            logger.info("[block] manually_unblocked user_id=%s ip_count=%d", user_id, len(ip_list))
        try:
            from utils.access_control import invalidate_block_cache
            invalidate_block_cache(user_id=user_id)
        except Exception:
            logger.debug("Could not invalidate block cache for user %s", user_id, exc_info=True)
        return ip_list
    
    async def is_user_blocked(self, user_id: int) -> bool:
        """Check if a user is blocked."""
        if await self.is_entity_blocked(telegram_user_id=user_id):
            return True
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT is_blocked FROM bot_users WHERE user_id = ?
            """, (user_id,)) as cursor:
                row = await cursor.fetchone()
                return row[0] == 1 if row else False

    async def is_entity_blocked(
        self,
        telegram_user_id: Optional[int] = None,
        username: Optional[str] = None,
        ip_address: Optional[str] = None,
        device_fingerprint: Optional[str] = None,
    ) -> bool:
        """Permanent block lookup independent of bot_users lifecycle."""
        clauses = []
        params = []
        if telegram_user_id is not None:
            clauses.append("telegram_user_id = ?")
            params.append(telegram_user_id)
        if username:
            clauses.append("LOWER(username) = LOWER(?)")
            params.append(username)
        if ip_address:
            clauses.append("ip_address = ?")
            params.append(ip_address)
        if device_fingerprint:
            clauses.append("device_fingerprint = ?")
            params.append(device_fingerprint)
        if not clauses:
            return False
        async with self.get_connection() as db:
            async with db.execute(
                f"SELECT 1 FROM blocked_entities WHERE is_permanent = 1 AND ({' OR '.join(clauses)}) LIMIT 1",
                params,
            ) as cursor:
                return await cursor.fetchone() is not None

    async def record_user_ip(self, user_id: int, ip_address: str) -> None:
        """Record (or refresh) an IP address for a user.

        Uses INSERT ... ON CONFLICT so that last_seen is always updated while
        first_seen is preserved from the original insert.
        """
        if await self.is_entity_blocked(telegram_user_id=user_id, ip_address=ip_address):
            logger.info("[block] denied_ip_refresh user_id=%s ip=%s", user_id, ip_address)
            return
        async with self.get_connection() as db:
            await db.execute("""
                INSERT INTO user_ips (user_id, ip_address)
                VALUES (?, ?)
                ON CONFLICT(user_id, ip_address)
                DO UPDATE SET last_seen = CURRENT_TIMESTAMP
            """, (user_id, ip_address))
            await db.commit()

    async def get_user_ips(self, user_id: int) -> list:
        """Return all IP addresses ever seen for a user."""
        async with self.get_connection() as db:
            async with db.execute(
                "SELECT ip_address FROM user_ips WHERE user_id = ?", (user_id,)
            ) as cursor:
                rows = await cursor.fetchall()
        return [row[0] for row in rows]

    async def get_all_blocked_ips(self) -> list:
        """Return all currently blocked IP addresses."""
        async with self.get_connection() as db:
            async with db.execute("SELECT ip_address FROM blocked_ips") as cursor:
                rows = await cursor.fetchall()
        return [row[0] for row in rows]
    
    async def delete_user(self, user_id: int):
        """
        Delete a user completely from the database.
        This is different from blocking - the user can restart the bot and be re-added.
        Deletes user record and all associated data (notifications, etc.).
        """
        async with self.get_connection() as db:
            # Delete from bot_users (cascade will handle related records)
            await db.execute("DELETE FROM bot_users WHERE user_id = ?", (user_id,))
            # Also delete any pending notifications for this user
            await db.execute("DELETE FROM notification_queue WHERE user_id = ?", (user_id,))
            # Delete any custom message queue entries
            await db.execute("DELETE FROM custom_message_queue WHERE user_id = ?", (user_id,))
            await db.commit()
    
    async def delete_users_by_bot(self, bot_username: str):
        """
        Delete all users associated with a specific bot.
        This allows bulk cleanup of users from a particular bot instance.
        Use bot_username="_untracked_" to delete all users without bot_username.
        """
        async with self.get_connection() as db:
            # First get the user IDs to delete their related data
            if bot_username == "_untracked_":
                async with db.execute("""
                    SELECT user_id FROM bot_users 
                    WHERE bot_username IS NULL OR bot_username = ''
                """) as cursor:
                    user_ids = [row[0] for row in await cursor.fetchall()]
            else:
                async with db.execute("""
                    SELECT user_id FROM bot_users WHERE LOWER(bot_username) = LOWER(?)
                """, (bot_username,)) as cursor:
                    user_ids = [row[0] for row in await cursor.fetchall()]
            
            if user_ids:
                # Delete notifications for these users
                placeholders = ','.join('?' * len(user_ids))
                await db.execute(f"DELETE FROM notification_queue WHERE user_id IN ({placeholders})", user_ids)
                await db.execute(f"DELETE FROM custom_message_queue WHERE user_id IN ({placeholders})", user_ids)
                
                # Delete the users themselves
                if bot_username == "_untracked_":
                    await db.execute("DELETE FROM bot_users WHERE bot_username IS NULL OR bot_username = ''")
                else:
                    await db.execute("DELETE FROM bot_users WHERE LOWER(bot_username) = LOWER(?)", (bot_username,))
                await db.commit()
            
            return len(user_ids)
    
    async def get_bot_usernames(self) -> List[str]:
        """
        Get currently registered bot usernames, normalized to lowercase.

        Historical bot usernames are intentionally not sourced from bot_users
        here. Runtime startup is the authority for active bots; stale user rows
        should not make deleted or switched bots reappear in admin menus.
        """
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT LOWER(bot_username) FROM bot_instances
                WHERE bot_username IS NOT NULL AND bot_username != ''
                ORDER BY 1
            """) as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]

    async def forget_inactive_bot_memory(self, active_bot_usernames: List[str]) -> Dict[str, int]:
        """
        Retire bot-specific records for bots that are no longer active.

        This is deliberately conservative: products and users are not deleted.
        Users attached to an inactive bot are moved to the untracked bucket so
        the old bot username disappears from admin views, while real customers
        can still be reassociated when they interact with a current bot.
        """
        normalized_active = sorted({
            self.normalize_bot_username(name)
            for name in active_bot_usernames
            if self.normalize_bot_username(name)
        })
        stats = {
            "active_bots": len(normalized_active),
            "detached_users": 0,
            "deleted_bot_instances": 0,
            "inactive_registry_rows": 0,
            "deleted_file_id_cache": 0,
            "deleted_broadcast_chats": 0,
        }

        if not normalized_active:
            logger.warning("[bot_cleanup] skipped because active bot list is empty")
            return stats

        placeholders = ",".join("?" * len(normalized_active))
        now = datetime.now()

        async with self.get_connection() as db:
            await db.execute("BEGIN IMMEDIATE")

            cursor = await db.execute(f"""
                UPDATE bot_users
                SET bot_username = NULL
                WHERE bot_username IS NOT NULL
                  AND bot_username != ''
                  AND LOWER(bot_username) NOT IN ({placeholders})
            """, normalized_active)
            stats["detached_users"] = max(cursor.rowcount, 0)

            cursor = await db.execute(f"""
                DELETE FROM bot_instances
                WHERE bot_username IS NOT NULL
                  AND bot_username != ''
                  AND LOWER(bot_username) NOT IN ({placeholders})
            """, normalized_active)
            stats["deleted_bot_instances"] = max(cursor.rowcount, 0)

            cursor = await db.execute(f"""
                UPDATE bot_registry
                SET status = 'inactive', updated_at = ?
                WHERE status = 'active'
                  AND bot_username IS NOT NULL
                  AND bot_username != ''
                  AND LOWER(bot_username) NOT IN ({placeholders})
            """, [now, *normalized_active])
            stats["inactive_registry_rows"] = max(cursor.rowcount, 0)

            cursor = await db.execute(f"""
                DELETE FROM bot_file_id_cache
                WHERE bot_username IS NOT NULL
                  AND bot_username != ''
                  AND LOWER(bot_username) NOT IN ({placeholders})
            """, normalized_active)
            stats["deleted_file_id_cache"] = max(cursor.rowcount, 0)

            cursor = await db.execute(f"""
                DELETE FROM broadcast_chats
                WHERE bot_username IS NOT NULL
                  AND bot_username != ''
                  AND LOWER(bot_username) NOT IN ({placeholders})
            """, normalized_active)
            stats["deleted_broadcast_chats"] = max(cursor.rowcount, 0)

            await db.commit()

        if any(value for key, value in stats.items() if key != "active_bots"):
            logger.info("[bot_cleanup] retired inactive bot memory stats=%s", stats)
        else:
            logger.debug("[bot_cleanup] no inactive bot memory found active_bots=%d", stats["active_bots"])
        return stats
    
    async def get_users_count_by_bot(self, bot_username: Optional[str] = None) -> int:
        """
        Get count of users for a specific bot or all users if bot_username is None.
        Use bot_username="_untracked_" to get count of users without bot_username.
        """
        async with self.get_connection() as db:
            if bot_username == "_untracked_":
                async with db.execute("""
                    SELECT COUNT(*) FROM bot_users 
                    WHERE bot_username IS NULL OR bot_username = ''
                """) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else 0
            elif bot_username:
                async with db.execute("""
                    SELECT COUNT(*) FROM bot_users WHERE LOWER(bot_username) = LOWER(?)
                """, (bot_username,)) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else 0
            else:
                async with db.execute("SELECT COUNT(*) FROM bot_users") as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else 0
    
    async def get_users_by_bot_paginated(self, bot_username: str, limit: int = 10, offset: int = 0):
        """
        Get users for a specific bot with pagination.
        
        Args:
            bot_username: Bot username to filter by, or "_untracked_" for users without bot_username
            limit: Number of users per page (default: 10)
            offset: Offset for pagination (default: 0)
        
        Returns:
            tuple: (total_count, users_list) where total_count is the total number of users
                   and users_list is a list of user dictionaries for the current page
        """
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            
            if bot_username == "_untracked_":
                # Get users without bot_username
                async with db.execute("""
                    SELECT * FROM bot_users 
                    WHERE bot_username IS NULL OR bot_username = ''
                    ORDER BY last_seen DESC
                    LIMIT ? OFFSET ?
                """, (limit, offset)) as cursor:
                    rows = await cursor.fetchall()
                    users = [dict(row) for row in rows]
                
                async with db.execute("""
                    SELECT COUNT(*) FROM bot_users 
                    WHERE bot_username IS NULL OR bot_username = ''
                """) as cursor:
                    row = await cursor.fetchone()
                    total_count = row[0] if row else 0
            else:
                # Get users for specific bot
                async with db.execute("""
                    SELECT * FROM bot_users 
                    WHERE LOWER(bot_username) = LOWER(?)
                    ORDER BY last_seen DESC
                    LIMIT ? OFFSET ?
                """, (bot_username, limit, offset)) as cursor:
                    rows = await cursor.fetchall()
                    users = [dict(row) for row in rows]
                
                async with db.execute("""
                    SELECT COUNT(*) FROM bot_users 
                    WHERE LOWER(bot_username) = LOWER(?)
                """, (bot_username,)) as cursor:
                    row = await cursor.fetchone()
                    total_count = row[0] if row else 0
            
            return total_count, users
    
    async def queue_custom_message(self, user_id: int, message_text: str):
        """Queue a custom message for a user."""
        async with self.get_connection() as db:
            await db.execute("""
                INSERT INTO custom_message_queue (user_id, message_text, created_at)
                VALUES (?, ?, ?)
            """, (user_id, message_text, datetime.now()))
            await db.commit()
    
    async def get_pending_custom_messages(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get pending custom messages from queue."""
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM custom_message_queue 
                WHERE sent_at IS NULL 
                ORDER BY created_at ASC 
                LIMIT ?
            """, (limit,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    
    async def mark_custom_message_sent(self, message_id: int):
        """Mark a custom message as sent."""
        async with self.get_connection() as db:
            await db.execute("""
                UPDATE custom_message_queue SET sent_at = ? WHERE id = ?
            """, (datetime.now(), message_id))
            await db.commit()
    
    async def count_pending_custom_messages(self) -> int:
        """Count remaining unsent messages in the custom message queue."""
        async with self.get_connection() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM custom_message_queue WHERE sent_at IS NULL"
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def count_pending_notifications(self) -> int:
        """Count remaining unsent notifications in the notification queue."""
        async with self.get_connection() as db:
            async with db.execute(
                "SELECT COUNT(*) FROM notification_queue WHERE sent_at IS NULL"
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

    async def count_pending_notifications_by_bot(self, top_n: int = 5) -> List[Dict[str, Any]]:
        """
        Count pending (unsent) notifications grouped by bot_username.

        Joins notification_queue with bot_users to determine which bot each user
        is assigned to.  Returns up to `top_n` bots ordered by count descending.

        Returns:
            List of dicts with keys 'bot_username' and 'pending'.
        """
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT bu.bot_username, COUNT(*) AS pending
                FROM notification_queue nq
                LEFT JOIN bot_users bu ON bu.user_id = nq.user_id
                WHERE nq.sent_at IS NULL
                GROUP BY bu.bot_username
                ORDER BY pending DESC
                LIMIT ?
            """, (top_n,)) as cursor:
                rows = await cursor.fetchall()
        return [{"bot_username": row["bot_username"], "pending": row["pending"]} for row in rows]

    async def count_pending_custom_messages_by_bot(self, top_n: int = 5) -> List[Dict[str, Any]]:
        """
        Count pending (unsent) custom messages grouped by bot_username.

        Joins custom_message_queue with bot_users to determine which bot each user
        is assigned to.  Returns up to `top_n` bots ordered by count descending.

        Returns:
            List of dicts with keys 'bot_username' and 'pending'.
        """
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT bu.bot_username, COUNT(*) AS pending
                FROM custom_message_queue cmq
                LEFT JOIN bot_users bu ON bu.user_id = cmq.user_id
                WHERE cmq.sent_at IS NULL
                GROUP BY bu.bot_username
                ORDER BY pending DESC
                LIMIT ?
            """, (top_n,)) as cursor:
                rows = await cursor.fetchall()
        return [{"bot_username": row["bot_username"], "pending": row["pending"]} for row in rows]
    
    async def get_recent_custom_messages_count(self, user_id: int, minutes: int = 60) -> int:
        """Count custom messages sent to user in the last N minutes."""
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT COUNT(*) FROM custom_message_queue 
                WHERE user_id = ? AND sent_at IS NOT NULL
                AND datetime(sent_at) > datetime('now', '-' || ? || ' minutes')
            """, (user_id, minutes)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
    
    async def get_user_language(self, user_id: int) -> str:
        """Get user's preferred language with caching."""
        global _user_language_cache
        
        # Check cache first
        async with _cache_lock:
            if user_id in _user_language_cache:
                lang, cached_at = _user_language_cache[user_id]
                # Return cached value if not expired
                if datetime.now() - cached_at < timedelta(seconds=CACHE_TTL_SECONDS):
                    return lang
        
        # Cache miss or expired - query database
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT language FROM bot_users WHERE user_id = ?
            """, (user_id,)) as cursor:
                row = await cursor.fetchone()
                lang = row[0] if row else 'en'
        
        # Update cache
        async with _cache_lock:
            _user_language_cache[user_id] = (lang, datetime.now())
        
        return lang
    
    async def set_user_language(self, user_id: int, language: str):
        """Set user's preferred language and invalidate cache."""
        global _user_language_cache
        
        async with self.get_connection() as db:
            # Check if user exists
            async with db.execute("SELECT user_id FROM bot_users WHERE user_id = ?", (user_id,)) as cursor:
                exists = await cursor.fetchone()
            
            if exists:
                # User exists, update only language and last_seen
                await db.execute("""
                    UPDATE bot_users SET language = ?, last_seen = ? WHERE user_id = ?
                """, (language, datetime.now(), user_id))
            else:
                # New user, insert with all fields
                await db.execute("""
                    INSERT INTO bot_users (user_id, language, first_seen, last_seen)
                    VALUES (?, ?, ?, ?)
                """, (user_id, language, datetime.now(), datetime.now()))
            
            await db.commit()
        
        # Invalidate cache for this user
        async with _cache_lock:
            _user_language_cache[user_id] = (language, datetime.now())
    
    async def get_cached_translation(
        self,
        source_text: str,
        source_lang: str,
        target_lang: str
    ) -> Optional[str]:
        """
        Get a cached translation from the database.
        
        Args:
            source_text: Text to translate
            source_lang: Source language code
            target_lang: Target language code
        
        Returns:
            Translated text if found in cache, None otherwise
        """
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT translated_text FROM translation_cache
                WHERE source_text = ? AND source_lang = ? AND target_lang = ?
            """, (source_text, source_lang, target_lang)) as cursor:
                row = await cursor.fetchone()
                
                if row:
                    # Update usage statistics
                    await db.execute("""
                        UPDATE translation_cache
                        SET used_count = used_count + 1, last_used = ?
                        WHERE source_text = ? AND source_lang = ? AND target_lang = ?
                    """, (datetime.now(), source_text, source_lang, target_lang))
                    await db.commit()
                    
                    return row[0]
                
                return None
    
    async def cache_translation(
        self,
        source_text: str,
        source_lang: str,
        target_lang: str,
        translated_text: str
    ):
        """
        Cache a translation in the database.
        
        Args:
            source_text: Original text
            source_lang: Source language code
            target_lang: Target language code
            translated_text: Translated text
        """
        async with self.get_connection() as db:
            try:
                await db.execute("""
                    INSERT INTO translation_cache (source_text, source_lang, target_lang, translated_text)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(source_text, source_lang, target_lang) 
                    DO UPDATE SET 
                        translated_text = excluded.translated_text,
                        used_count = used_count + 1,
                        last_used = ?
                """, (source_text, source_lang, target_lang, translated_text, datetime.now()))
                await db.commit()
            except Exception as e:
                logger.error(f"Error caching translation: {e}")
    
    async def cleanup_old_translations(self, days_old: int = 90):
        """
        Clean up old, unused translations from the cache.
        
        Args:
            days_old: Remove translations older than this many days that haven't been used
        """
        async with self.get_connection() as db:
            await db.execute("""
                DELETE FROM translation_cache
                WHERE last_used < datetime('now', '-' || ? || ' days')
                AND used_count = 1
            """, (days_old,))
            await db.commit()
            logger.info(f"Cleaned up translations older than {days_old} days")
    
    async def get_order_contact(self) -> str:
        """Get the order contact username from settings with caching."""
        global _order_contact_cache
        
        # Check cache first
        async with _cache_lock:
            if _order_contact_cache is not None:
                contact, cached_at = _order_contact_cache
                # Return cached value if not expired
                if datetime.now() - cached_at < timedelta(seconds=CACHE_TTL_SECONDS):
                    return contact
        
        # Cache miss or expired - query database
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT value FROM bot_settings WHERE key = 'order_contact'
            """) as cursor:
                row = await cursor.fetchone()
                contact = row[0] if row else DEFAULT_ORDER_CONTACT
        
        # Update cache
        async with _cache_lock:
            _order_contact_cache = (contact, datetime.now())
        
        return contact
    
    async def set_order_contact(self, contact: str):
        """Set the order contact username in settings and invalidate cache."""
        global _order_contact_cache
        
        async with self.get_connection() as db:
            await db.execute("""
                INSERT OR REPLACE INTO bot_settings (key, value, updated_at)
                VALUES ('order_contact', ?, ?)
            """, (contact, datetime.now()))
            await db.commit()
            logger.info(f"Order contact updated to: {contact}")
        
        # Invalidate cache
        async with _cache_lock:
            _order_contact_cache = (contact, datetime.now())

    async def get_order_link(self) -> str:
        """Get the order link URL from settings with caching."""
        global _order_link_cache

        # Check cache first
        async with _cache_lock:
            if _order_link_cache is not None:
                link, cached_at = _order_link_cache
                # Return cached value if not expired
                if datetime.now() - cached_at < timedelta(seconds=CACHE_TTL_SECONDS):
                    return link

        # Cache miss or expired - query database
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT value FROM bot_settings WHERE key = 'order_link'
            """) as cursor:
                row = await cursor.fetchone()
                link = row[0] if row else DEFAULT_ORDER_LINK

        # Update cache
        async with _cache_lock:
            _order_link_cache = (link, datetime.now())

        return link

    async def get_order_links(self) -> List[Dict[str, str]]:
        """Get named order/contact links from settings with backward-compatible fallback."""
        global _order_links_cache

        async with _cache_lock:
            if _order_links_cache is not None:
                links, cached_at = _order_links_cache
                if datetime.now() - cached_at < timedelta(seconds=CACHE_TTL_SECONDS):
                    return [dict(item) for item in links]

        links: List[Dict[str, str]] = []
        has_explicit_order_links = False
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT value FROM bot_settings WHERE key = 'order_links'
            """) as cursor:
                row = await cursor.fetchone()
                has_explicit_order_links = row is not None
                raw_value = row[0] if row else ""

        if raw_value:
            try:
                parsed = json.loads(raw_value)
                if isinstance(parsed, list):
                    for item in parsed:
                        if not isinstance(item, dict):
                            continue
                        label = str(item.get("label") or "").strip()
                        url = str(item.get("url") or "").strip()
                        if label and url:
                            links.append({"label": label[:40], "url": url[:500]})
            except (TypeError, json.JSONDecodeError) as exc:
                logger.warning("Invalid order_links setting; falling back to order_link: %s", exc)
                has_explicit_order_links = False

        if not links and not has_explicit_order_links:
            fallback = await self.get_order_link()
            if fallback:
                links.append({"label": "OUR CONTACTS", "url": fallback})

        async with _cache_lock:
            _order_links_cache = ([dict(item) for item in links], datetime.now())

        return links

    async def set_order_link(self, link: str):
        """Set the order link URL in settings and invalidate cache."""
        global _order_link_cache, _order_links_cache

        clean_links = [{"label": "OUR CONTACTS", "url": link}] if link else []
        payload = json.dumps(clean_links, ensure_ascii=False)

        async with self.get_connection() as db:
            await db.execute("""
                INSERT OR REPLACE INTO bot_settings (key, value, updated_at)
                VALUES ('order_link', ?, ?)
            """, (link, datetime.now()))
            await db.execute("""
                INSERT OR REPLACE INTO bot_settings (key, value, updated_at)
                VALUES ('order_links', ?, ?)
            """, (payload, datetime.now()))
            await db.commit()
            logger.info(f"Order link updated to: {link}")

        # Invalidate cache
        async with _cache_lock:
            _order_link_cache = (link, datetime.now())
            _order_links_cache = (clean_links, datetime.now())

    async def set_order_links(self, links: List[Dict[str, str]]):
        """Set named home-screen contact links without changing the DM order contact."""
        global _order_links_cache

        clean_links: List[Dict[str, str]] = []
        for item in links or []:
            label = str(item.get("label") or "").strip()
            url = str(item.get("url") or "").strip()
            if not label or not url:
                continue
            clean_links.append({"label": label[:40], "url": url[:500]})

        payload = json.dumps(clean_links, ensure_ascii=False)

        async with self.get_connection() as db:
            await db.execute("""
                INSERT OR REPLACE INTO bot_settings (key, value, updated_at)
                VALUES ('order_links', ?, ?)
            """, (payload, datetime.now()))
            await db.commit()
            logger.info("Order links updated: count=%d", len(clean_links))

        async with _cache_lock:
            _order_links_cache = ([dict(item) for item in clean_links], datetime.now())

    async def get_bot_file_id(
        self,
        source_chat_id: int,
        source_message_id: int,
        file_index: int,
        bot_username: str,
        slot_id: Optional[int] = None,
        identity_hash: Optional[str] = None,
        file_type: Optional[str] = None,
    ) -> Optional[str]:
        """Get cached bot-specific file ID from database."""
        normalized_file_type = (file_type or "").strip().lower() or None
        normalized_bot_username = self.normalize_bot_username(bot_username)
        async with self.get_connection() as db:
            if slot_id is not None:
                sql = """
                    SELECT file_id, last_used FROM bot_file_id_cache
                    WHERE source_chat_id = ? AND source_message_id = ?
                      AND file_index = ? AND slot_id = ?
                      AND (? IS NULL OR identity_hash = ?)
                      AND (? IS NULL OR lower(file_type) = ?)
                """
                params = (
                    source_chat_id,
                    source_message_id,
                    file_index,
                    slot_id,
                    identity_hash,
                    identity_hash,
                    normalized_file_type,
                    normalized_file_type,
                )
            else:
                sql = """
                    SELECT file_id, last_used FROM bot_file_id_cache
                    WHERE source_chat_id = ? AND source_message_id = ?
                      AND file_index = ? AND bot_username = ?
                      AND (? IS NULL OR lower(file_type) = ?)
                """
                params = (
                    source_chat_id,
                    source_message_id,
                    file_index,
                    normalized_bot_username,
                    normalized_file_type,
                    normalized_file_type,
                )
            async with db.execute(sql, params) as cursor:
                row = await cursor.fetchone()
                if row:
                    if self._should_touch_cache_entry(row[1]):
                        await self._touch_bot_file_id_cache_entry(
                            db,
                            source_chat_id,
                            source_message_id,
                            file_index,
                            bot_username,
                            slot_id,
                            identity_hash,
                        )
                    return row[0]
                return None

    @staticmethod
    def _should_touch_cache_entry(last_used: Any, min_age_seconds: int = 3600) -> bool:
        """Return True when a cache-hit metadata write is worth doing."""
        if last_used is None:
            return True
        if isinstance(last_used, datetime):
            last_dt = last_used
        else:
            try:
                last_dt = datetime.fromisoformat(str(last_used))
            except (TypeError, ValueError):
                return True
        return (datetime.now() - last_dt).total_seconds() >= min_age_seconds

    async def _touch_bot_file_id_cache_entry(
        self,
        db,
        source_chat_id: int,
        source_message_id: int,
        file_index: int,
        bot_username: str,
        slot_id: Optional[int],
        identity_hash: Optional[str],
    ) -> None:
        if slot_id is not None:
            await db.execute("""
                UPDATE bot_file_id_cache
                SET last_used = ?, use_count = use_count + 1
                WHERE source_chat_id = ? AND source_message_id = ?
                  AND file_index = ? AND slot_id = ?
                  AND (? IS NULL OR identity_hash = ?)
            """, (datetime.now(), source_chat_id, source_message_id, file_index, slot_id, identity_hash, identity_hash))
        else:
            await db.execute("""
                UPDATE bot_file_id_cache
                SET last_used = ?, use_count = use_count + 1
                WHERE source_chat_id = ? AND source_message_id = ?
                  AND file_index = ? AND bot_username = ?
            """, (datetime.now(), source_chat_id, source_message_id, file_index, self.normalize_bot_username(bot_username)))
        await db.commit()
    
    async def cache_bot_file_id(
        self,
        source_chat_id: int,
        source_message_id: int,
        file_index: int,
        file_type: str,
        bot_username: str,
        file_id: str,
        slot_id: Optional[int] = None,
        identity_hash: Optional[str] = None,
    ):
        """Cache a bot-specific file ID in database."""
        async with self.get_connection() as db:
            await db.execute("""
                INSERT OR REPLACE INTO bot_file_id_cache 
                (source_chat_id, source_message_id, file_index, file_type, bot_username, file_id, created_at, last_used, use_count, slot_id, identity_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            """, (source_chat_id, source_message_id, file_index, file_type, 
                  self.normalize_bot_username(bot_username), file_id, datetime.now(), datetime.now(), slot_id, identity_hash))
            await db.commit()
            logger.debug(f"Cached file ID for bot {bot_username}: msg={source_message_id}, idx={file_index}")
    
    async def clear_bot_file_id_cache(self):
        """Clear all bot-specific file ID caches (for admin /clearcache command)."""
        async with self.get_connection() as db:
            cursor = await db.execute("DELETE FROM bot_file_id_cache")
            deleted = cursor.rowcount
            await db.commit()
            logger.info(f"Cleared {deleted} bot file ID cache entries")
            return deleted

    async def upsert_canonical_media(
        self,
        product_id: str,
        media_type: str,
        file_unique_id: str,
        origin_chat_id: Optional[int] = None,
        origin_message_id: Optional[int] = None,
        original_file_id: Optional[str] = None,
        mime_type: Optional[str] = None,
        file_size: Optional[int] = None,
    ) -> None:
        """Create or refresh canonical media metadata without touching products."""
        if not file_unique_id:
            return
        async with self.get_connection() as db:
            await db.execute("""
                INSERT INTO canonical_media
                (product_id, media_type, file_unique_id, origin_chat_id, origin_message_id,
                 original_file_id, mime_type, file_size, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON CONFLICT(file_unique_id) DO UPDATE SET
                    product_id = COALESCE(excluded.product_id, canonical_media.product_id),
                    media_type = COALESCE(excluded.media_type, canonical_media.media_type),
                    origin_chat_id = COALESCE(excluded.origin_chat_id, canonical_media.origin_chat_id),
                    origin_message_id = COALESCE(excluded.origin_message_id, canonical_media.origin_message_id),
                    original_file_id = COALESCE(excluded.original_file_id, canonical_media.original_file_id),
                    mime_type = COALESCE(excluded.mime_type, canonical_media.mime_type),
                    file_size = COALESCE(excluded.file_size, canonical_media.file_size),
                    updated_at = CURRENT_TIMESTAMP
            """, (str(product_id), media_type, file_unique_id, origin_chat_id, origin_message_id,
                  original_file_id, mime_type, file_size))
            await db.commit()
            logger.info("[media_registry] canonical_media_upsert product_id=%s type=%s", product_id, media_type)

    async def get_canonical_media_by_origin(
        self,
        origin_chat_id: int,
        origin_message_id: int,
        media_type: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            if media_type:
                sql = """
                    SELECT * FROM canonical_media
                    WHERE origin_chat_id = ? AND origin_message_id = ? AND media_type = ?
                    ORDER BY updated_at DESC LIMIT 1
                """
                params = (origin_chat_id, origin_message_id, media_type)
            else:
                sql = """
                    SELECT * FROM canonical_media
                    WHERE origin_chat_id = ? AND origin_message_id = ?
                    ORDER BY updated_at DESC LIMIT 1
                """
                params = (origin_chat_id, origin_message_id)
            async with db.execute(sql, params) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_bot_media_file_id(self, bot_identifier: str, file_unique_id: str) -> Optional[str]:
        async with self.get_connection() as db:
            async with db.execute("""
                SELECT resolved_file_id FROM bot_media_file_ids
                WHERE bot_identifier = ? AND file_unique_id = ? AND is_valid = 1
            """, (bot_identifier, file_unique_id)) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return None
            await db.execute("""
                UPDATE bot_media_file_ids
                SET last_verified_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
                WHERE bot_identifier = ? AND file_unique_id = ?
            """, (bot_identifier, file_unique_id))
            await db.commit()
            return row[0]

    async def upsert_bot_media_file_id(
        self,
        bot_identifier: str,
        file_unique_id: str,
        resolved_file_id: str,
        slot_id: Optional[int] = None,
        identity_hash: Optional[str] = None,
    ) -> None:
        if not bot_identifier or not file_unique_id or not resolved_file_id:
            return
        async with self.get_connection() as db:
            await db.execute("""
                INSERT INTO bot_media_file_ids
                (bot_identifier, file_unique_id, resolved_file_id, last_verified_at, is_valid, created_at, updated_at, slot_id, identity_hash)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?, ?)
                ON CONFLICT(bot_identifier, file_unique_id) DO UPDATE SET
                    resolved_file_id = excluded.resolved_file_id,
                    last_verified_at = CURRENT_TIMESTAMP,
                    is_valid = 1,
                    slot_id = COALESCE(excluded.slot_id, bot_media_file_ids.slot_id),
                    identity_hash = COALESCE(excluded.identity_hash, bot_media_file_ids.identity_hash),
                    updated_at = CURRENT_TIMESTAMP
            """, (bot_identifier, file_unique_id, resolved_file_id, slot_id, identity_hash))
            await db.commit()
            logger.info("[media_registry] bot_media_mapping_upsert bot=%s", bot_identifier)

    async def invalidate_bot_media_file_id(self, bot_identifier: str, file_unique_id: str) -> None:
        async with self.get_connection() as db:
            await db.execute("""
                UPDATE bot_media_file_ids
                SET is_valid = 0, updated_at = CURRENT_TIMESTAMP
                WHERE bot_identifier = ? AND file_unique_id = ?
            """, (bot_identifier, file_unique_id))
            await db.commit()
            logger.info("[media_registry] bot_media_mapping_invalidated bot=%s", bot_identifier)

    async def delete_bot_file_id_cache_entry(
        self,
        source_chat_id: int,
        source_message_id: int,
        file_index: int,
        bot_username: str,
        slot_id: Optional[int] = None,
        identity_hash: Optional[str] = None,
    ):
        """Delete a specific bot file ID cache entry (used when cached ID is invalid)."""
        async with self.get_connection() as db:
            if slot_id is not None:
                await db.execute("""
                    DELETE FROM bot_file_id_cache
                    WHERE source_chat_id = ? AND source_message_id = ?
                      AND file_index = ? AND slot_id = ?
                      AND (? IS NULL OR identity_hash = ?)
                """, (source_chat_id, source_message_id, file_index, slot_id, identity_hash, identity_hash))
            else:
                await db.execute("""
                    DELETE FROM bot_file_id_cache
                    WHERE source_chat_id = ? AND source_message_id = ? 
                      AND file_index = ? AND bot_username = ?
                """, (source_chat_id, source_message_id, file_index, self.normalize_bot_username(bot_username)))
            await db.commit()
            logger.debug(f"Deleted invalid cache entry: bot={bot_username}, msg={source_message_id}, idx={file_index}")
    async def clear_bot_file_id_cache_for_origin(self, source_chat_id: int, source_message_id: int) -> int:
        """Delete all bot file ID cache entries for one source message across bots."""
        async with self.get_connection() as db:
            cursor = await db.execute(
                """
                DELETE FROM bot_file_id_cache
                WHERE source_chat_id = ? AND source_message_id = ?
                """,
                (source_chat_id, source_message_id),
            )
            deleted = cursor.rowcount
            await db.commit()
            logger.info(
                "Cleared %s bot file ID cache entries for origin chat=%s msg=%s",
                deleted,
                source_chat_id,
                source_message_id,
            )
            return deleted


    async def clear_bot_file_id_cache_for_bot(self, bot_username: str) -> int:
        """Clear all cached file IDs for a specific bot username."""
        async with self.get_connection() as db:
            cursor = await db.execute(
                "DELETE FROM bot_file_id_cache WHERE bot_username = ?",
                (self.normalize_bot_username(bot_username),)
            )
            deleted = cursor.rowcount
            await db.commit()
            logger.info(f"Cleared {deleted} bot file ID cache entries for @{bot_username}")
            return deleted

    async def clear_bot_file_id_cache_for_slot(self, slot_id: int) -> int:
        """Clear cached file IDs for one permanent bot slot only."""
        async with self.get_connection() as db:
            cursor = await db.execute(
                """
                DELETE FROM bot_file_id_cache
                WHERE slot_id = ?
                   OR (
                        slot_id IS NULL
                        AND bot_username IN (
                            SELECT bot_username FROM bot_instances WHERE slot_id = ?
                        )
                   )
                """,
                (slot_id, slot_id)
            )
            deleted = cursor.rowcount
            await db.commit()
            logger.info("Cleared bot file ID cache for slot %s entries=%s", slot_id, deleted)
            return deleted

    async def clear_bot_media_file_ids_for_slot(self, slot_id: int) -> int:
        """Invalidate resolver/media mappings for one permanent bot slot only."""
        async with self.get_connection() as db:
            cursor = await db.execute(
                """
                UPDATE bot_media_file_ids
                SET is_valid = 0, updated_at = CURRENT_TIMESTAMP
                WHERE slot_id = ?
                   OR (
                        slot_id IS NULL
                        AND bot_identifier IN (
                            SELECT identity_hash FROM bot_instances WHERE slot_id = ?
                            UNION
                            SELECT bot_username FROM bot_instances WHERE slot_id = ?
                        )
                   )
                """,
                (slot_id, slot_id, slot_id)
            )
            changed = cursor.rowcount
            await db.commit()
            logger.info("Invalidated bot media resolver cache for slot %s entries=%s", slot_id, changed)
            return changed

    async def register_bot_instance(self, bot_username: str, bot_id: int, token_hash: str):
        """Register or update a bot instance record."""
        now = datetime.now()
        async with self.get_connection() as db:
            await db.execute("""
                INSERT INTO bot_instances (bot_username, bot_id, token_hash, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(bot_username) DO UPDATE SET
                    bot_id = excluded.bot_id,
                    token_hash = excluded.token_hash,
                    last_seen = excluded.last_seen
            """, (self.normalize_bot_username(bot_username), bot_id, token_hash, now, now))
            await db.commit()
            logger.debug(f"Registered bot instance: @{bot_username} (id={bot_id})")

    async def resolve_bot_id_from_registry(
        self,
        telegram_bot_id: int,
        bot_username: str,
        token_hash: str,
        webhook_secret_factory,
        force_webhook_secret: bool = True,
    ) -> Dict[str, Any]:
        """Resolve the immutable bot_registry row for a Telegram bot ID."""
        now = datetime.now()
        normalized_username = self.normalize_bot_username(bot_username)
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT * FROM bot_registry WHERE telegram_bot_id = ?",
                (telegram_bot_id,),
            ) as cursor:
                row = await cursor.fetchone()

            if row:
                webhook_secret = webhook_secret_factory()
                if not force_webhook_secret:
                    webhook_secret = row["webhook_secret"]
                await db.execute("""
                    UPDATE bot_registry
                    SET bot_username = ?, token_hash = ?, webhook_secret = ?, status = 'active', updated_at = ?
                    WHERE bot_id = ?
                """, (normalized_username, token_hash, webhook_secret, now, row["bot_id"]))
                await db.commit()
                return await self.get_bot_registry_by_id(row["bot_id"]) or dict(row)

            registry_bot_id = str(uuid.uuid4())
            webhook_secret = webhook_secret_factory()
            await db.execute("""
                INSERT INTO bot_registry (
                    bot_id, telegram_bot_id, bot_username, token_hash,
                    webhook_secret, status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, 'active', ?, ?)
            """, (
                registry_bot_id,
                telegram_bot_id,
                normalized_username,
                token_hash,
                webhook_secret,
                now,
                now,
            ))
            await db.commit()
            return await self.get_bot_registry_by_id(registry_bot_id) or {
                "bot_id": registry_bot_id,
                "telegram_bot_id": telegram_bot_id,
                "bot_username": normalized_username,
                "token_hash": token_hash,
                "webhook_secret": webhook_secret,
                "status": "active",
            }

    async def get_bot_registry_by_id(self, bot_id: str) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM bot_registry WHERE bot_id = ? AND status = 'active'",
                (bot_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_bot_registry_by_id_any_status(self, bot_id: str) -> Optional[Dict[str, Any]]:
        """Return a bot_registry row regardless of active/inactive status."""
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM bot_registry WHERE bot_id = ?",
                (bot_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_bot_registry_by_telegram_id(self, telegram_bot_id: int) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM bot_registry WHERE telegram_bot_id = ? AND status = 'active'",
                (telegram_bot_id,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_bot_registry_by_webhook_secret(self, webhook_secret: str) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM bot_registry WHERE webhook_secret = ? AND status = 'active'",
                (webhook_secret,),
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def record_media_object(
        self,
        media_id: str,
        media_owner_id: str,
        storage_path: str,
        *,
        file_unique_id: Optional[str] = None,
        product_id: Optional[str] = None,
        ttl_expiry: Optional[datetime] = None,
    ) -> None:
        async with self.get_connection() as db:
            await db.execute("""
                INSERT INTO media_objects (
                    media_id, media_owner_id, storage_path, file_unique_id,
                    product_id, created_at, ttl_expiry, deleted_at
                )
                VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP, ?, NULL)
                ON CONFLICT(media_id) DO UPDATE SET
                    media_owner_id = excluded.media_owner_id,
                    storage_path = excluded.storage_path,
                    file_unique_id = excluded.file_unique_id,
                    product_id = excluded.product_id,
                    ttl_expiry = excluded.ttl_expiry,
                    deleted_at = NULL
            """, (media_id, media_owner_id, storage_path, file_unique_id, product_id, ttl_expiry))
            await db.commit()

    async def list_expired_media_objects(self, now: Optional[datetime] = None) -> List[Dict[str, Any]]:
        now = now or datetime.now()
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM media_objects
                WHERE deleted_at IS NULL AND ttl_expiry IS NOT NULL AND ttl_expiry <= ?
            """, (now,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_products_page(
        self,
        *,
        category: Optional[str] = None,
        subcategory: Optional[str] = None,
        excluded_categories: Optional[List[str]] = None,
        limit: int = 24,
        offset: int = 0,
    ) -> tuple[List[Dict[str, Any]], int]:
        """Fetch a storefront product page and total count using one DB connection."""
        limit = max(1, min(limit, 50))
        offset = max(0, offset)
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            if category and subcategory:
                where_sql = "category = ? AND subcategory = ?"
                params: list[Any] = [category, subcategory]
            elif category:
                where_sql = "category = ?"
                params = [category]
            else:
                excluded_categories = excluded_categories or []
                if excluded_categories:
                    placeholders = ",".join("?" * len(excluded_categories))
                    where_sql = f"(category IS NULL OR category = '' OR category NOT IN ({placeholders}))"
                    params = list(excluded_categories)
                else:
                    where_sql = "1 = 1"
                    params = []

            async with db.execute(
                f"SELECT COUNT(*) FROM products WHERE {where_sql}",
                params,
            ) as cursor:
                row = await cursor.fetchone()
                total = row[0] if row else 0

            async with db.execute(
                f"""
                SELECT * FROM products
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
                products = [dict(row) for row in rows]

            return products, total

    async def mark_media_object_deleted(self, media_id: str) -> None:
        async with self.get_connection() as db:
            await db.execute(
                "UPDATE media_objects SET deleted_at = CURRENT_TIMESTAMP WHERE media_id = ?",
                (media_id,),
            )
            await db.commit()

    async def get_bot_slot_identity(self, slot_id: int) -> Optional[Dict[str, Any]]:
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM bot_instances WHERE slot_id = ?",
                (slot_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def register_bot_slot(
        self,
        slot_id: int,
        slot_name: str,
        telegram_username: str,
        telegram_bot_id: int,
        token_hash: str,
        identity_version: int,
        identity_hash: str,
        webhook_secret: str,
    ) -> None:
        """Register/update permanent slot identity while preserving username APIs."""
        now = datetime.now()
        normalized_username = self.normalize_bot_username(telegram_username)
        async with self.get_connection() as db:
            await db.execute("""
                INSERT INTO bot_instances (
                    bot_username, bot_id, token_hash, first_seen, last_seen,
                    slot_id, slot_name, identity_version, identity_hash,
                    webhook_secret, telegram_username
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(bot_username) DO UPDATE SET
                    bot_id = excluded.bot_id,
                    token_hash = excluded.token_hash,
                    last_seen = excluded.last_seen,
                    slot_id = excluded.slot_id,
                    slot_name = excluded.slot_name,
                    identity_version = excluded.identity_version,
                    identity_hash = excluded.identity_hash,
                    webhook_secret = excluded.webhook_secret,
                    telegram_username = excluded.telegram_username
            """, (
                normalized_username, telegram_bot_id, token_hash, now, now,
                slot_id, slot_name, identity_version, identity_hash,
                webhook_secret, normalized_username,
            ))
            await db.commit()

    async def acquire_bot_slot_lock(self, slot_id: int, owner: str, ttl_seconds: int = 60) -> bool:
        """Acquire a lightweight SQLite lock for one bot slot."""
        expires = datetime.now() + timedelta(seconds=ttl_seconds)
        now = datetime.now()
        async with self.get_connection() as db:
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT lock_until FROM bot_slot_locks WHERE slot_id = ?",
                (slot_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if row and datetime.fromisoformat(str(row[0])) > now:
                await db.rollback()
                return False
            await db.execute("""
                INSERT OR REPLACE INTO bot_slot_locks (slot_id, owner, lock_until, updated_at)
                VALUES (?, ?, ?, ?)
            """, (slot_id, owner, expires.isoformat(), now.isoformat()))
            await db.commit()
            return True

    async def release_bot_slot_lock(self, slot_id: int, owner: str) -> None:
        async with self.get_connection() as db:
            await db.execute(
                "DELETE FROM bot_slot_locks WHERE slot_id = ? AND owner = ?",
                (slot_id, owner)
            )
            await db.commit()

    async def has_bot_token_changed(self, bot_username: str, token_hash: str) -> bool:
        """Check if the bot token has changed since last run. Returns True if changed."""
        async with self.get_connection() as db:
            async with db.execute(
                "SELECT token_hash FROM bot_instances WHERE bot_username = ?",
                (self.normalize_bot_username(bot_username),)
            ) as cursor:
                row = await cursor.fetchone()
                if row is None:
                    # First time seeing this bot — not a change
                    return False
                return row[0] != token_hash

    async def get_users_with_inactive_bots(self, active_bot_usernames: List[str]) -> List[Dict[str, Any]]:
        """
        Get all users associated with bots that are no longer in the active pool.
        
        Args:
            active_bot_usernames: List of currently active bot usernames
            
        Returns:
            List of user dictionaries with bot_username not in active list
        """
        if not active_bot_usernames:
            # If no active bots, return all users with bot_username set
            async with self.get_connection() as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("""
                    SELECT * FROM bot_users 
                    WHERE bot_username IS NOT NULL AND bot_username != ''
                    ORDER BY last_seen DESC
                """) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        
        # Normalize active bot usernames for comparison
        normalized_active = [self.normalize_bot_username(name) for name in active_bot_usernames]
        
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            # Build a query that excludes active bots
            placeholders = ','.join('?' * len(normalized_active))
            query = f"""
                SELECT * FROM bot_users 
                WHERE bot_username IS NOT NULL 
                AND bot_username != ''
                AND LOWER(bot_username) NOT IN ({placeholders})
                ORDER BY last_seen DESC
            """
            async with db.execute(query, normalized_active) as cursor:
                rows = await cursor.fetchall()
                users = [dict(row) for row in rows]
                
            logger.info(f"Found {len(users)} users associated with inactive bots")
            return users
    
    async def get_products_with_inactive_bots(self, active_bot_usernames: List[str]) -> List[Dict[str, Any]]:
        """
        Get all products associated with bots that are no longer in the active pool.
        
        Args:
            active_bot_usernames: List of currently active bot usernames
            
        Returns:
            List of product dictionaries with bot_username not in active list
        """
        if not active_bot_usernames:
            # If no active bots, return all products with bot_username set
            async with self.get_connection() as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("""
                    SELECT * FROM products 
                    WHERE bot_username IS NOT NULL AND bot_username != ''
                    ORDER BY created_at DESC
                """) as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
        
        # Normalize active bot usernames for comparison
        normalized_active = [self.normalize_bot_username(name) for name in active_bot_usernames]
        
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            # Build a query that excludes active bots
            placeholders = ','.join('?' * len(normalized_active))
            query = f"""
                SELECT * FROM products 
                WHERE bot_username IS NOT NULL 
                AND bot_username != ''
                AND LOWER(bot_username) NOT IN ({placeholders})
                ORDER BY created_at DESC
            """
            async with db.execute(query, normalized_active) as cursor:
                rows = await cursor.fetchall()
                products = [dict(row) for row in rows]
                
            logger.info(f"Found {len(products)} products associated with inactive bots")
            return products
    
    async def prune_inactive_bot_users(self, active_bot_usernames: List[str], dry_run: bool = True) -> Dict[str, int]:
        """
        Prune users and products associated with inactive/deleted bots.
        
        Args:
            active_bot_usernames: List of currently active bot usernames
            dry_run: If True, only report what would be deleted without actually deleting
            
        Returns:
            Dictionary with counts of users, products, notifications, and messages that were (or would be) deleted
        """
        stats = {
            'users': 0,
            'products': 0,
            'notifications': 0,
            'custom_messages': 0
        }
        
        # Get users with inactive bots
        inactive_users = await self.get_users_with_inactive_bots(active_bot_usernames)
        
        if not inactive_users:
            logger.info("No users with inactive bots found")
            return stats
        
        user_ids = [user['user_id'] for user in inactive_users]
        inactive_bot_usernames = set(user.get('bot_username') for user in inactive_users if user.get('bot_username'))
        
        logger.info(f"{'[DRY RUN] ' if dry_run else ''}Found {len(user_ids)} users from {len(inactive_bot_usernames)} inactive bots: {', '.join(sorted(inactive_bot_usernames))}")
        
        if dry_run:
            # Count what would be deleted
            async with self.get_connection() as db:
                # Count notifications
                placeholders = ','.join('?' * len(user_ids))
                async with db.execute(f"SELECT COUNT(*) FROM notification_queue WHERE user_id IN ({placeholders})", user_ids) as cursor:
                    row = await cursor.fetchone()
                    stats['notifications'] = row[0] if row else 0
                
                # Count custom messages
                async with db.execute(f"SELECT COUNT(*) FROM custom_message_queue WHERE user_id IN ({placeholders})", user_ids) as cursor:
                    row = await cursor.fetchone()
                    stats['custom_messages'] = row[0] if row else 0
            
            # Count products
            inactive_products = await self.get_products_with_inactive_bots(active_bot_usernames)
            stats['products'] = len(inactive_products)
            stats['users'] = len(user_ids)
            
            logger.info(f"[DRY RUN] Would delete: {stats['users']} users, {stats['products']} products, "
                       f"{stats['notifications']} notifications, {stats['custom_messages']} custom messages")
        else:
            # Actually delete
            async with self.get_connection() as db:
                # Delete notifications for these users
                placeholders = ','.join('?' * len(user_ids))
                cursor = await db.execute(f"DELETE FROM notification_queue WHERE user_id IN ({placeholders})", user_ids)
                stats['notifications'] = cursor.rowcount
                
                # Delete custom messages for these users
                cursor = await db.execute(f"DELETE FROM custom_message_queue WHERE user_id IN ({placeholders})", user_ids)
                stats['custom_messages'] = cursor.rowcount
                
                # Delete users from inactive bots
                for bot_username in inactive_bot_usernames:
                    cursor = await db.execute("DELETE FROM bot_users WHERE LOWER(bot_username) = LOWER(?)", (bot_username,))
                    stats['users'] += cursor.rowcount
                
                # Delete products from inactive bots
                normalized_active = [self.normalize_bot_username(name) for name in active_bot_usernames]
                if normalized_active:
                    placeholders = ','.join('?' * len(normalized_active))
                    cursor = await db.execute(f"""
                        DELETE FROM products 
                        WHERE bot_username IS NOT NULL 
                        AND bot_username != ''
                        AND LOWER(bot_username) NOT IN ({placeholders})
                    """, normalized_active)
                    stats['products'] = cursor.rowcount
                else:
                    cursor = await db.execute("""
                        DELETE FROM products 
                        WHERE bot_username IS NOT NULL AND bot_username != ''
                    """)
                    stats['products'] = cursor.rowcount
                
                await db.commit()
                
            logger.info(f"Pruned: {stats['users']} users, {stats['products']} products, "
                       f"{stats['notifications']} notifications, {stats['custom_messages']} custom messages "
                       f"from inactive bots: {', '.join(sorted(inactive_bot_usernames))}")
        
        return stats

    # ------------------------------------------------------------------
    # Inline analytics
    # ------------------------------------------------------------------

    async def log_inline_query(
        self,
        user_id: int,
        query: str,
        bot_username: str,
        latency_ms: Optional[int] = None,
        results_count: Optional[int] = None,
        is_empty_query: bool = False,
    ) -> None:
        """Log an inline query event for analytics."""
        query_length = len(query) if query else 0
        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO inline_queries
                (user_id, query, bot_username, latency_ms, results_count, is_empty_query, query_length)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (user_id, query, bot_username, latency_ms, results_count, int(is_empty_query), query_length),
            )
            await db.commit()

    async def log_chosen_inline_result(
        self,
        user_id: int,
        result_id: str,
        query: str,
        bot_username: str,
    ) -> None:
        """Log a chosen inline result event for analytics."""
        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO inline_chosen_results (user_id, result_id, query, bot_username)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, result_id, query, bot_username),
            )
            await db.commit()

    async def log_webapp_events(self, logs: List[Dict[str, Any]]) -> int:
        """Persist WebApp diagnostic/user-interaction events for admin analytics."""
        if not logs:
            return 0

        rows = []
        for entry in logs:
            details = entry.get("details", {})
            try:
                details_json = json.dumps(details, ensure_ascii=True, default=str)
            except Exception:
                details_json = "{}"
            details_json = details_json[:4000]
            rows.append((
                str(entry.get("session_id") or ""),
                str(entry.get("user_id") or "unknown"),
                str(entry.get("username") or "unknown"),
                str(entry.get("event_type") or "unknown")[:80],
                str(entry.get("action") or "")[:120],
                str(entry.get("status") or "")[:40],
                str(entry.get("error") or "")[:500],
                details_json,
                str(entry.get("timestamp") or ""),
            ))

        async with self.get_connection() as db:
            await db.executemany(
                """
                INSERT INTO webapp_events
                    (session_id, user_id, username, event_type, action, status, error, details_json, client_timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            await db.commit()
        return len(rows)

    async def log_visitor_event(
        self,
        *,
        visitor_hash: str,
        telegram_user_id: Optional[Any] = None,
        username: Optional[str] = None,
        source: str = "website",
        page_type: str = "",
        path: str = "",
        product_id: Optional[int] = None,
        category: Optional[str] = None,
        subcategory: Optional[str] = None,
        country: str = "Unknown",
        region: str = "Unknown",
        city: str = "Unknown",
        language: str = "",
        device_type: str = "unknown",
        user_agent_hash: str = "",
        referrer: str = "",
        is_authenticated: bool = False,
    ) -> None:
        """Persist a privacy-aware visitor/page-open event."""
        visitor_hash = str(visitor_hash or "").strip()
        if not visitor_hash:
            return

        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO visitor_events (
                    visitor_hash, telegram_user_id, username, source, page_type, path,
                    product_id, category, subcategory, country, region, city, language,
                    device_type, user_agent_hash, referrer, is_authenticated
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    visitor_hash[:80],
                    str(telegram_user_id)[:80] if telegram_user_id is not None else None,
                    str(username or "")[:120],
                    str(source or "website")[:40],
                    str(page_type or "")[:80],
                    str(path or "")[:300],
                    product_id,
                    str(category or "")[:80],
                    str(subcategory or "")[:120],
                    str(country or "Unknown")[:80],
                    str(region or "Unknown")[:120],
                    str(city or "Unknown")[:120],
                    str(language or "")[:40],
                    str(device_type or "unknown")[:40],
                    str(user_agent_hash or "")[:80],
                    str(referrer or "")[:200],
                    int(bool(is_authenticated)),
                ),
            )
            await db.commit()

    async def log_search_query(
        self,
        *,
        query: str,
        expanded_query: str = "",
        matched_count: int = 0,
        source: str = "webapp",
        language: str = "",
        user_id: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> None:
        """Persist a search query for SEO/search intelligence."""
        from utils.seo_terms import normalize_term

        normalized_query = normalize_term(query)
        if not normalized_query:
            return

        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO search_query_events
                    (query, normalized_query, expanded_query, matched_count, source, language, user_id, ip_address)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    query[:300],
                    normalized_query[:300],
                    expanded_query[:1000],
                    max(0, int(matched_count or 0)),
                    source[:40],
                    str(language or "")[:12].lower(),
                    str(user_id)[:80] if user_id is not None else None,
                    str(ip_address)[:80] if ip_address is not None else None,
                ),
            )
            await db.commit()

    async def upsert_seo_query_mappings(
        self,
        mappings: List[Dict[str, Any]],
        *,
        source: str = "admin_import",
        approved: bool = True,
    ) -> int:
        """Insert/update approved SEO query mappings imported from admin CSV/text."""
        from utils.seo_terms import normalize_term

        rows = []
        seen = set()
        for mapping in mappings:
            term = str(mapping.get("term") or "").strip()
            normalized = normalize_term(term)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            rows.append((
                term[:300],
                normalized[:300],
                str(mapping.get("category") or "").strip()[:80],
                str(mapping.get("subcategory") or "").strip()[:120],
                source[:80],
                str(mapping.get("language") or "")[:12].lower(),
                int(bool(approved)),
            ))

        if not rows:
            return 0

        async with self.get_connection() as db:
            await db.executemany(
                """
                INSERT INTO seo_query_mappings (
                    term, normalized_term, category, subcategory, source, language, approved, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(normalized_term) DO UPDATE SET
                    term = excluded.term,
                    category = excluded.category,
                    subcategory = excluded.subcategory,
                    source = excluded.source,
                    language = seo_query_mappings.language,
                    approved = excluded.approved,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
            await db.commit()
        return len(rows)

    async def increment_seo_mapping_hits(self, terms: List[str]) -> int:
        """Increment usage counters for approved SEO mappings matched by search."""
        from utils.seo_terms import normalize_term

        normalized_terms = []
        seen = set()
        for term in terms or []:
            normalized = normalize_term(term)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            normalized_terms.append((normalized[:300],))

        if not normalized_terms:
            return 0

        async with self.get_connection() as db:
            await db.executemany(
                """
                UPDATE seo_query_mappings
                SET
                    hits = COALESCE(hits, 0) + 1,
                    updated_at = CURRENT_TIMESTAMP
                WHERE approved = 1 AND normalized_term = ?
                """,
                normalized_terms,
            )
            await db.commit()
        return len(normalized_terms)

    async def upsert_search_console_query_events(self, rows: List[Dict[str, Any]]) -> int:
        """Insert/update Search Console query snapshots."""
        from utils.seo_terms import normalize_term

        normalized_rows = []
        seen = set()
        for row in rows or []:
            query = str(row.get("query") or "").strip()
            normalized_query = normalize_term(query)
            source_date = str(row.get("date") or row.get("source_date") or "").strip()
            if not normalized_query or not source_date:
                continue
            page = str(row.get("page") or "")[:500]
            country = str(row.get("country") or "")[:20]
            device = str(row.get("device") or "")[:40]
            key = (normalized_query, page, country, device, source_date)
            if key in seen:
                continue
            seen.add(key)
            normalized_rows.append((
                query[:300],
                normalized_query[:300],
                page,
                country,
                device,
                max(0, int(row.get("clicks") or 0)),
                max(0, int(row.get("impressions") or 0)),
                float(row.get("ctr") or 0),
                float(row.get("position") or 0),
                source_date[:20],
            ))

        if not normalized_rows:
            return 0

        async with self.get_connection() as db:
            await db.executemany(
                """
                INSERT INTO search_console_query_events (
                    query, normalized_query, page, country, device,
                    clicks, impressions, ctr, position, source_date
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(normalized_query, page, country, device, source_date) DO UPDATE SET
                    query = excluded.query,
                    clicks = excluded.clicks,
                    impressions = excluded.impressions,
                    ctr = excluded.ctr,
                    position = excluded.position,
                    created_at = CURRENT_TIMESTAMP
                """,
                normalized_rows,
            )
            await db.commit()
        return len(normalized_rows)

    async def upsert_external_seo_signals(
        self,
        signals: List[Dict[str, Any]],
        *,
        provider: str = "internal",
        source: str = "",
        approved: bool = False,
    ) -> int:
        """Record external SEO evidence such as Search Console or approved taxonomy exports."""
        from utils.seo_terms import normalize_term

        rows = []
        seen = set()
        for signal in signals or []:
            term = str(signal.get("term") or "").strip()
            normalized = normalize_term(term)
            if not normalized:
                continue
            row_provider = str(signal.get("provider") or provider or "internal").strip()[:80]
            category = str(signal.get("category") or "").strip()[:80]
            subcategory = str(signal.get("subcategory") or "").strip()[:120]
            key = (row_provider, normalized, category, subcategory)
            if key in seen:
                continue
            seen.add(key)
            try:
                notes_json = json.dumps(signal.get("notes") or {}, ensure_ascii=True)
            except (TypeError, ValueError):
                notes_json = "{}"
            rows.append((
                term[:300],
                normalized[:300],
                str(signal.get("term_type") or "query").strip()[:40],
                row_provider,
                str(signal.get("source") or source or "").strip()[:120],
                category,
                subcategory,
                max(0, min(100, int(signal.get("confidence") or 0))),
                int(bool(signal.get("approved", approved))),
                max(1, int(signal.get("evidence_count") or 1)),
                notes_json[:2000],
            ))

        if not rows:
            return 0

        async with self.get_connection() as db:
            await db.executemany(
                """
                INSERT INTO external_seo_signals (
                    term, normalized_term, term_type, provider, source,
                    category, subcategory, confidence, approved, evidence_count, notes_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(provider, normalized_term, category, subcategory) DO UPDATE SET
                    term = excluded.term,
                    term_type = excluded.term_type,
                    source = excluded.source,
                    confidence = max(external_seo_signals.confidence, excluded.confidence),
                    approved = CASE
                        WHEN external_seo_signals.approved = 1 OR excluded.approved = 1 THEN 1
                        ELSE 0
                    END,
                    evidence_count = max(external_seo_signals.evidence_count, excluded.evidence_count),
                    notes_json = excluded.notes_json,
                    last_seen = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
            await db.commit()
        return len(rows)

    async def get_approved_external_seo_signals_without_mapping(
        self,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """Return approved external terms that have not yet been promoted to mappings."""
        limit = max(1, min(limit, 100))
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    s.term, s.normalized_term, s.provider, s.source,
                    s.category, s.subcategory, s.confidence, s.evidence_count
                FROM external_seo_signals s
                LEFT JOIN seo_query_mappings m
                    ON m.normalized_term = s.normalized_term AND m.approved = 1
                WHERE s.approved = 1
                  AND s.category != ''
                  AND m.id IS NULL
                ORDER BY s.confidence DESC, s.evidence_count DESC, s.last_seen DESC
                LIMIT ?
                """,
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_recent_external_seo_signals(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return recent approved external SEO signals for admin status."""
        limit = max(1, min(limit, 50))
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT term, provider, source, category, subcategory, confidence,
                       evidence_count, last_seen
                FROM external_seo_signals
                WHERE approved = 1
                ORDER BY last_seen DESC, confidence DESC
                LIMIT ?
                """,
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_seo_side_notes(
        self,
        category: str,
        subcategory: str = "",
        *,
        language: str = "en",
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        """Return approved, visible SEO side-note terms for a category page."""
        category = str(category or "").strip()
        subcategory = str(subcategory or "").strip()
        if not category:
            return []

        limit = max(1, min(limit, 25))
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            clean_language = str(language or "en").lower()[:12]
            params: list[Any] = [category]
            subcategory_filter = ""
            if subcategory:
                subcategory_filter = "AND LOWER(TRIM(COALESCE(subcategory, ''))) = LOWER(TRIM(?))"
                params.append(subcategory)

            if clean_language == "en":
                language_filter = "AND (language = 'en' OR language = '')"
            else:
                language_filter = "AND language = ?"
                params.append(clean_language)
            params.append(limit)
            async with db.execute(
                f"""
                SELECT term, source, 'mapping' AS signal_type, hits AS evidence_count
                FROM seo_query_mappings
                WHERE approved = 1
                  AND LOWER(TRIM(COALESCE(category, ''))) = LOWER(TRIM(?))
                  {subcategory_filter}
                  {language_filter}
                ORDER BY hits DESC, updated_at DESC
                LIMIT ?
                """,
                tuple(params),
            ) as cursor:
                mapping_rows = [dict(row) for row in await cursor.fetchall()]

            remaining = max(0, limit - len(mapping_rows))
            signal_rows: List[Dict[str, Any]] = []
            if remaining and clean_language == "en":
                params = [category]
                subcategory_filter = ""
                if subcategory:
                    subcategory_filter = "AND LOWER(TRIM(COALESCE(subcategory, ''))) = LOWER(TRIM(?))"
                    params.append(subcategory)
                params.append(remaining)
                async with db.execute(
                    f"""
                    SELECT term, provider AS source, 'external' AS signal_type, evidence_count
                    FROM external_seo_signals
                    WHERE approved = 1
                      AND LOWER(TRIM(COALESCE(category, ''))) = LOWER(TRIM(?))
                      {subcategory_filter}
                    ORDER BY confidence DESC, evidence_count DESC, last_seen DESC
                    LIMIT ?
                    """,
                    tuple(params),
                ) as cursor:
                    signal_rows = [dict(row) for row in await cursor.fetchall()]

        seen = set()
        merged = []
        from utils.seo_terms import normalize_term

        for row in [*mapping_rows, *signal_rows]:
            key = normalize_term(row.get("term") or "")
            if not key or key in seen:
                continue
            seen.add(key)
            merged.append(row)
            if len(merged) >= limit:
                break
        return merged

    async def get_approved_seo_query_mappings(self, limit: int = 2000) -> List[Dict[str, Any]]:
        """Return admin-approved SEO query mappings."""
        limit = max(1, min(limit, 5000))
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT term, category, subcategory, source, language, hits
                FROM seo_query_mappings
                WHERE approved = 1
                ORDER BY hits DESC, updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_recent_seo_query_mappings(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return recently approved SEO mappings for admin visibility."""
        limit = max(1, min(limit, 50))
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT term, category, subcategory, source, language, hits, updated_at
                FROM seo_query_mappings
                WHERE approved = 1
                ORDER BY updated_at DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_latest_seo_mapping_updated_at(self) -> Optional[str]:
        """Return the newest approved mapping timestamp for sitemap freshness."""
        async with self.get_connection() as db:
            async with db.execute(
                "SELECT MAX(updated_at) FROM seo_query_mappings WHERE approved = 1"
            ) as cursor:
                row = await cursor.fetchone()
                return str(row[0]) if row and row[0] else None

    async def get_seo_mapping_lastmods(self) -> List[Dict[str, Any]]:
        """Return mapping freshness grouped by its affected taxonomy."""
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    TRIM(COALESCE(category, '')) AS category,
                    TRIM(COALESCE(subcategory, '')) AS subcategory,
                    MAX(updated_at) AS updated_at
                FROM seo_query_mappings
                WHERE approved = 1
                  AND TRIM(COALESCE(category, '')) <> ''
                GROUP BY
                    TRIM(COALESCE(category, '')),
                    TRIM(COALESCE(subcategory, ''))
                """
            ) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def get_seo_auto_learning_candidates(
        self,
        *,
        limit: int = 25,
        min_count: int = 1,
        lookback_hours: int = 720,
        max_matched_count: int = 3,
    ) -> List[Dict[str, Any]]:
        """Return repeated low/no-result searches that are not already approved mappings."""
        limit = max(1, min(limit, 100))
        min_count = max(1, min_count)
        max_matched_count = max(0, min(max_matched_count, 25))
        since = datetime.now() - timedelta(hours=max(1, lookback_hours))
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    (
                        SELECT e2.query
                        FROM search_query_events e2
                        WHERE e2.normalized_query = e.normalized_query
                        ORDER BY e2.created_at DESC, e2.id DESC
                        LIMIT 1
                    ) AS query,
                    (
                        SELECT COALESCE(NULLIF(e2.language, ''), 'en')
                        FROM search_query_events e2
                        WHERE e2.normalized_query = e.normalized_query
                        ORDER BY e2.created_at DESC, e2.id DESC
                        LIMIT 1
                    ) AS language,
                    e.normalized_query,
                    COUNT(*) AS count,
                    SUM(CASE WHEN e.matched_count = 0 THEN 1 ELSE 0 END) AS no_result_count,
                    MIN(e.matched_count) AS best_matched_count,
                    ROUND(AVG(e.matched_count), 1) AS avg_matched_count,
                    MAX(e.created_at) AS last_seen
                FROM search_query_events e
                LEFT JOIN seo_query_mappings m
                    ON m.normalized_term = e.normalized_query AND m.approved = 1
                WHERE e.matched_count <= ?
                  AND e.created_at >= ?
                  AND m.id IS NULL
                GROUP BY e.normalized_query
                HAVING COUNT(*) >= ?
                ORDER BY no_result_count DESC, count DESC, last_seen DESC
                LIMIT ?
                """,
                (max_matched_count, since, min_count, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_unmapped_search_queries(
        self,
        limit: int = 20,
        *,
        max_matched_count: Optional[int] = None,
        lookback_hours: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return frequent low/no-result queries that are not yet approved mappings."""
        limit = max(1, min(limit, 100))
        if max_matched_count is None:
            max_matched_count = Config.SEO_AUTO_LEARN_MAX_MATCHED_COUNT
        max_matched_count = max(0, min(max_matched_count, 25))
        if lookback_hours is None:
            lookback_hours = Config.SEO_AUTO_LEARN_LOOKBACK_HOURS
        since = datetime.now() - timedelta(hours=max(1, lookback_hours))
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT
                    (
                        SELECT e2.query
                        FROM search_query_events e2
                        WHERE e2.normalized_query = e.normalized_query
                        ORDER BY e2.created_at DESC, e2.id DESC
                        LIMIT 1
                    ) AS query,
                    e.normalized_query,
                    COUNT(*) AS count,
                    SUM(CASE WHEN e.matched_count = 0 THEN 1 ELSE 0 END) AS no_result_count,
                    MIN(e.matched_count) AS best_matched_count,
                    (
                        SELECT e2.matched_count
                        FROM search_query_events e2
                        WHERE e2.normalized_query = e.normalized_query
                        ORDER BY e2.created_at DESC, e2.id DESC
                        LIMIT 1
                    ) AS last_matched_count,
                    MAX(e.created_at) AS last_seen
                FROM search_query_events e
                LEFT JOIN seo_query_mappings m
                    ON m.normalized_term = e.normalized_query AND m.approved = 1
                WHERE e.matched_count <= ?
                  AND e.created_at >= ?
                  AND m.id IS NULL
                GROUP BY e.normalized_query
                ORDER BY no_result_count DESC, count DESC, last_seen DESC
                LIMIT ?
                """,
                (max_matched_count, since, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_search_intelligence_snapshot(self, limit: int = 10) -> Dict[str, Any]:
        """Compact snapshot for the /seostatus admin command."""
        limit = max(1, min(limit, 250))
        since_24h = datetime.now() - timedelta(hours=24)
        search_console_days = Config.SEARCH_CONSOLE_LOOKBACK_DAYS
        search_console_since = (datetime.now() - timedelta(days=search_console_days)).date().isoformat()
        low_result_threshold = Config.SEO_AUTO_LEARN_MAX_MATCHED_COUNT
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row

            async def scalar(sql: str, params: tuple = ()) -> int:
                async with db.execute(sql, params) as cursor:
                    row = await cursor.fetchone()
                    return int(row[0] or 0) if row else 0

            async def value(sql: str, params: tuple = ()):
                async with db.execute(sql, params) as cursor:
                    row = await cursor.fetchone()
                    return row[0] if row else None

            async def rows(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
                async with db.execute(sql, params) as cursor:
                    result = await cursor.fetchall()
                    return [dict(row) for row in result]

            return {
                "queries_24h": await scalar(
                    "SELECT COUNT(*) FROM search_query_events WHERE created_at >= ?",
                    (since_24h,),
                ),
                "no_result_24h": await scalar(
                    "SELECT COUNT(*) FROM search_query_events WHERE matched_count = 0 AND created_at >= ?",
                    (since_24h,),
                ),
                "low_result_threshold": low_result_threshold,
                "low_result_24h": await scalar(
                    "SELECT COUNT(*) FROM search_query_events WHERE matched_count <= ? AND created_at >= ?",
                    (low_result_threshold, since_24h),
                ),
                "approved_mappings": await scalar("SELECT COUNT(*) FROM seo_query_mappings WHERE approved = 1"),
                "auto_mappings": await scalar(
                    "SELECT COUNT(*) FROM seo_query_mappings WHERE approved = 1 AND source = 'auto_learn'"
                ),
                "search_console_window_days": search_console_days,
                "search_console_queries_window": await scalar(
                    "SELECT COUNT(DISTINCT normalized_query) FROM search_console_query_events WHERE source_date >= ?",
                    (search_console_since,),
                ),
                "search_console_clicks_window": await scalar(
                    "SELECT COALESCE(SUM(clicks), 0) FROM search_console_query_events WHERE source_date >= ?",
                    (search_console_since,),
                ),
                "search_console_impressions_window": await scalar(
                    "SELECT COALESCE(SUM(impressions), 0) FROM search_console_query_events WHERE source_date >= ?",
                    (search_console_since,),
                ),
                "search_console_pages_window": await scalar(
                    "SELECT COUNT(DISTINCT page) FROM search_console_query_events WHERE source_date >= ? AND page <> ''",
                    (search_console_since,),
                ),
                "search_console_total_rows": await scalar("SELECT COUNT(*) FROM search_console_query_events"),
                "search_console_latest_source_date": await value(
                    "SELECT MAX(source_date) FROM search_console_query_events"
                ),
                "search_console_latest_imported_at": await value(
                    "SELECT MAX(created_at) FROM search_console_query_events"
                ),
                "search_console_sitemap": await self.get_search_console_sitemap_snapshot(),
                "seo_auto_learn_interval_seconds": Config.SEO_AUTO_LEARN_INTERVAL_SECONDS,
                "seo_auto_learn_score": Config.SEO_AUTO_LEARN_SCORE,
                "seo_auto_learn_max_matched_count": Config.SEO_AUTO_LEARN_MAX_MATCHED_COUNT,
                "seo_auto_learn_max_per_run": Config.SEO_AUTO_LEARN_MAX_PER_RUN,
                "external_signals": await scalar("SELECT COUNT(*) FROM external_seo_signals WHERE approved = 1"),
                "top_queries": await rows(
                    """
                    SELECT
                        e.query,
                        COUNT(*) AS count,
                        MAX(e.created_at) AS last_seen,
                        (
                            SELECT e2.matched_count
                            FROM search_query_events e2
                            WHERE e2.normalized_query = e.normalized_query
                            ORDER BY e2.created_at DESC, e2.id DESC
                            LIMIT 1
                        ) AS last_matched_count
                    FROM search_query_events e
                    WHERE e.created_at >= ?
                    GROUP BY e.normalized_query
                    ORDER BY count DESC, last_seen DESC
                    LIMIT ?
                    """,
                    (since_24h, limit),
                ),
                "unmapped": await self.get_unmapped_search_queries(
                    limit=limit,
                    max_matched_count=low_result_threshold,
                    lookback_hours=Config.SEO_AUTO_LEARN_LOOKBACK_HOURS,
                ),
                "recent_mappings": await self.get_recent_seo_query_mappings(limit=limit),
                "mapping_languages": await rows(
                    """
                    SELECT
                        CASE WHEN TRIM(COALESCE(language, '')) = '' THEN 'en' ELSE language END AS language,
                        COUNT(*) AS count
                    FROM seo_query_mappings
                    WHERE approved = 1
                    GROUP BY CASE WHEN TRIM(COALESCE(language, '')) = '' THEN 'en' ELSE language END
                    ORDER BY count DESC, language ASC
                    """
                ),
                "search_languages_30d": await rows(
                    """
                    SELECT
                        CASE WHEN TRIM(COALESCE(language, '')) = '' THEN 'en' ELSE language END AS language,
                        COUNT(*) AS count
                    FROM search_query_events
                    WHERE created_at >= ?
                    GROUP BY CASE WHEN TRIM(COALESCE(language, '')) = '' THEN 'en' ELSE language END
                    ORDER BY count DESC, language ASC
                    """,
                    (datetime.now() - timedelta(days=30),),
                ),
                "recent_external_signals": await self.get_recent_external_seo_signals(limit=limit),
                "google_trends_signals": await scalar(
                    "SELECT COUNT(*) FROM external_seo_signals WHERE approved = 1 AND provider = 'google_trends'"
                ),
                "google_trends_latest_seen": await value(
                    "SELECT MAX(last_seen) FROM external_seo_signals WHERE approved = 1 AND provider = 'google_trends'"
                ),
                "top_search_console_queries": await rows(
                    """
                    SELECT
                        query,
                        SUM(clicks) AS clicks,
                        SUM(impressions) AS impressions,
                        ROUND(AVG(position), 1) AS avg_position,
                        MAX(source_date) AS last_seen
                    FROM search_console_query_events
                    WHERE source_date >= ?
                    GROUP BY normalized_query
                    ORDER BY clicks DESC, impressions DESC, last_seen DESC
                    LIMIT ?
                    """,
                    (search_console_since, limit),
                ),
                "top_search_console_pages": await rows(
                    """
                    SELECT
                        page,
                        SUM(clicks) AS clicks,
                        SUM(impressions) AS impressions,
                        ROUND(AVG(position), 1) AS avg_position,
                        MAX(source_date) AS last_seen,
                        COUNT(DISTINCT normalized_query) AS query_count
                    FROM search_console_query_events
                    WHERE source_date >= ? AND page <> ''
                    GROUP BY page
                    ORDER BY clicks DESC, impressions DESC, last_seen DESC
                    LIMIT ?
                    """,
                    (search_console_since, limit),
                ),
            }

    async def get_admin_dashboard_snapshot(self, recent_limit: int = 30) -> Dict[str, Any]:
        """Return read-only dashboard data from persistent application state."""
        recent_limit = max(5, min(recent_limit, 100))
        since_24h = datetime.now() - timedelta(hours=24)

        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row

            async def scalar(sql: str, params: tuple = ()) -> int:
                async with db.execute(sql, params) as cursor:
                    row = await cursor.fetchone()
                    return int(row[0] or 0) if row else 0

            summary = {
                "products": await scalar("SELECT COUNT(*) FROM products"),
                "bot_users": await scalar("SELECT COUNT(*) FROM bot_users"),
                "active_users_24h": await scalar(
                    "SELECT COUNT(*) FROM bot_users WHERE last_seen >= ?",
                    (since_24h,),
                ),
                "blocked_users": await scalar("SELECT COUNT(*) FROM bot_users WHERE is_blocked = 1"),
                "subscribed_users": await scalar("SELECT COUNT(*) FROM bot_users WHERE is_subscribed = 1"),
                "pending_notifications": await scalar("SELECT COUNT(*) FROM notification_queue WHERE sent_at IS NULL"),
                "queued_custom_messages": await scalar("SELECT COUNT(*) FROM custom_message_queue WHERE sent_at IS NULL"),
                "file_id_cache_entries": await scalar("SELECT COUNT(*) FROM bot_file_id_cache"),
                "canonical_media": await scalar("SELECT COUNT(*) FROM canonical_media"),
                "bot_media_mappings": await scalar("SELECT COUNT(*) FROM bot_media_file_ids WHERE is_valid = 1"),
                "bot_registry_active": await scalar("SELECT COUNT(*) FROM bot_registry WHERE status = 'active'"),
                "webapp_events_24h": await scalar(
                    "SELECT COUNT(*) FROM webapp_events WHERE created_at >= ?",
                    (since_24h,),
                ),
                "visitor_events_24h": await scalar(
                    "SELECT COUNT(*) FROM visitor_events WHERE created_at >= ?",
                    (since_24h,),
                ),
                "unique_visitors_24h": await scalar(
                    "SELECT COUNT(DISTINCT visitor_hash) FROM visitor_events WHERE created_at >= ?",
                    (since_24h,),
                ),
                "known_visitor_users_24h": await scalar(
                    "SELECT COUNT(DISTINCT telegram_user_id) FROM visitor_events WHERE created_at >= ? AND telegram_user_id IS NOT NULL",
                    (since_24h,),
                ),
                "website_visits_24h": await scalar(
                    "SELECT COUNT(*) FROM visitor_events WHERE created_at >= ? AND source = 'website'",
                    (since_24h,),
                ),
                "webapp_visits_24h": await scalar(
                    "SELECT COUNT(*) FROM visitor_events WHERE created_at >= ? AND source = 'webapp'",
                    (since_24h,),
                ),
                "inline_queries_24h": await scalar(
                    "SELECT COUNT(*) FROM inline_queries WHERE created_at >= ?",
                    (since_24h,),
                ),
                "inline_chosen_24h": await scalar(
                    "SELECT COUNT(*) FROM inline_chosen_results WHERE created_at >= ?",
                    (since_24h,),
                ),
                "search_queries_24h": await scalar(
                    "SELECT COUNT(*) FROM search_query_events WHERE created_at >= ?",
                    (since_24h,),
                ),
                "search_no_result_24h": await scalar(
                    "SELECT COUNT(*) FROM search_query_events WHERE matched_count = 0 AND created_at >= ?",
                    (since_24h,),
                ),
                "seo_query_mappings": await scalar("SELECT COUNT(*) FROM seo_query_mappings WHERE approved = 1"),
            }

            async def rows(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
                async with db.execute(sql, params) as cursor:
                    fetched = await cursor.fetchall()
                    return [dict(row) for row in fetched]

            def expand_country_names(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
                for item in items:
                    stored_country = item.get("country")
                    item["country_code"] = country_code(stored_country)
                    item["country"] = country_display_name(stored_country)
                return items

            bots = await rows("""
                SELECT
                    bot_id, telegram_bot_id, bot_username, status,
                    created_at, updated_at
                FROM bot_registry
                ORDER BY updated_at DESC
            """)

            bot_instances = await rows("""
                SELECT
                    bot_username, bot_id, slot_id, slot_name,
                    telegram_username, first_seen, last_seen
                FROM bot_instances
                ORDER BY COALESCE(slot_id, 9999), bot_username
            """)

            bot_user_counts = await rows("""
                SELECT
                    COALESCE(NULLIF(LOWER(bot_username), ''), '_untracked_') AS bot_username,
                    COUNT(*) AS users,
                    SUM(CASE WHEN last_seen >= ? THEN 1 ELSE 0 END) AS active_24h,
                    SUM(CASE WHEN is_blocked = 1 THEN 1 ELSE 0 END) AS blocked
                FROM bot_users
                GROUP BY COALESCE(NULLIF(LOWER(bot_username), ''), '_untracked_')
                ORDER BY users DESC
            """, (since_24h,))

            recent_users = await rows("""
                SELECT
                    user_id, username, first_name, last_name, bot_username,
                    language, is_subscribed, is_blocked, notifications_enabled,
                    interaction_count, first_seen, last_seen
                FROM bot_users
                ORDER BY last_seen DESC
                LIMIT ?
            """, (recent_limit,))

            recent_webapp_events = await rows("""
                SELECT
                    created_at, client_timestamp, session_id, user_id, username,
                    event_type, action, status, error, details_json
                FROM webapp_events
                ORDER BY created_at DESC
                LIMIT ?
            """, (recent_limit,))

            webapp_event_counts = await rows("""
                SELECT event_type, action, status, COUNT(*) AS count
                FROM webapp_events
                WHERE created_at >= ?
                GROUP BY event_type, action, status
                ORDER BY count DESC
                LIMIT 25
            """, (since_24h,))

            inline_activity = await rows("""
                SELECT
                    COALESCE(NULLIF(LOWER(bot_username), ''), '_unknown_') AS bot_username,
                    COUNT(*) AS queries,
                    AVG(latency_ms) AS avg_latency_ms,
                    AVG(results_count) AS avg_results
                FROM inline_queries
                WHERE created_at >= ?
                GROUP BY COALESCE(NULLIF(LOWER(bot_username), ''), '_unknown_')
                ORDER BY queries DESC
            """, (since_24h,))

            top_visitor_locations = await rows("""
                SELECT
                    COALESCE(NULLIF(country, ''), 'Unknown') AS country,
                    COALESCE(NULLIF(region, ''), 'Unknown') AS region,
                    COALESCE(NULLIF(city, ''), 'Unknown') AS city,
                    COUNT(*) AS visits,
                    COUNT(DISTINCT visitor_hash) AS visitors,
                    SUM(CASE WHEN is_authenticated = 1 THEN 1 ELSE 0 END) AS signed_visits
                FROM visitor_events
                WHERE created_at >= ?
                GROUP BY
                    COALESCE(NULLIF(country, ''), 'Unknown'),
                    COALESCE(NULLIF(region, ''), 'Unknown'),
                    COALESCE(NULLIF(city, ''), 'Unknown')
                ORDER BY visitors DESC, visits DESC
                LIMIT 20
            """, (since_24h,))
            top_visitor_locations = expand_country_names(top_visitor_locations)

            top_public_pages = await rows("""
                SELECT
                    source, page_type, path,
                    COUNT(*) AS visits,
                    COUNT(DISTINCT visitor_hash) AS visitors
                FROM visitor_events
                WHERE created_at >= ?
                GROUP BY source, page_type, path
                ORDER BY visits DESC
                LIMIT 20
            """, (since_24h,))

            visitor_devices = await rows("""
                SELECT
                    COALESCE(NULLIF(device_type, ''), 'unknown') AS device_type,
                    COUNT(*) AS visits,
                    COUNT(DISTINCT visitor_hash) AS visitors
                FROM visitor_events
                WHERE created_at >= ?
                GROUP BY COALESCE(NULLIF(device_type, ''), 'unknown')
                ORDER BY visitors DESC, visits DESC
            """, (since_24h,))

            recent_visitor_events = await rows("""
                SELECT
                    created_at, telegram_user_id, username, source, page_type, path,
                    product_id, category, subcategory, country, region, city,
                    language, device_type, is_authenticated
                FROM visitor_events
                ORDER BY created_at DESC
                LIMIT ?
            """, (recent_limit,))
            recent_visitor_events = expand_country_names(recent_visitor_events)

            indexnow_last_rows = await rows("""
                SELECT url, reason, status, attempts, http_status, last_error,
                       first_seen, updated_at, submitted_at
                FROM indexnow_submissions
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
            """)
            indexnow = {
                "pending": await scalar("SELECT COUNT(*) FROM indexnow_submissions WHERE status = 'pending'"),
                "submitted": await scalar("SELECT COUNT(*) FROM indexnow_submissions WHERE status = 'submitted'"),
                "failed": await scalar("SELECT COUNT(*) FROM indexnow_submissions WHERE status = 'failed'"),
                "total": await scalar("SELECT COUNT(*) FROM indexnow_submissions"),
                "last": indexnow_last_rows[0] if indexnow_last_rows else None,
            }

        return {
            "summary": summary,
            "bots": bots,
            "bot_instances": bot_instances,
            "bot_user_counts": bot_user_counts,
            "recent_users": recent_users,
            "recent_webapp_events": recent_webapp_events,
            "webapp_event_counts": webapp_event_counts,
            "inline_activity": inline_activity,
            "top_visitor_locations": top_visitor_locations,
            "top_public_pages": top_public_pages,
            "visitor_devices": visitor_devices,
            "recent_visitor_events": recent_visitor_events,
            "indexnow": indexnow,
        }

    # ------------------------------------------------------------------
    # Generic settings + IndexNow search-engine submission queue
    # ------------------------------------------------------------------

    async def get_bot_setting(self, key: str, default: Optional[str] = None) -> Optional[str]:
        """Return a bot_settings value without touching the specialized cached settings."""
        key = str(key or "").strip()
        if not key:
            return default
        async with self.get_connection() as db:
            async with db.execute("SELECT value FROM bot_settings WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else default

    async def set_bot_setting(self, key: str, value: str) -> None:
        """Persist a simple bot_settings value."""
        key = str(key or "").strip()
        if not key:
            return
        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO bot_settings (key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (key, str(value or "")),
            )
            await db.commit()

    async def get_search_console_sitemap_snapshot(self) -> Dict[str, Any]:
        """Return the latest Google Search Console sitemap submission status."""
        keys = [
            "search_console_sitemap_last_attempt_at",
            "search_console_sitemap_last_submitted_at",
            "search_console_sitemap_last_status",
            "search_console_sitemap_last_http_status",
            "search_console_sitemap_last_error",
            "search_console_sitemap_last_url",
            "search_console_sitemap_last_site_url",
            "search_console_sitemap_last_auth_source",
        ]
        placeholders = ",".join("?" * len(keys))
        async with self.get_connection() as db:
            async with db.execute(
                f"SELECT key, value, updated_at FROM bot_settings WHERE key IN ({placeholders})",
                tuple(keys),
            ) as cursor:
                rows = await cursor.fetchall()

        values = {row[0]: row[1] for row in rows}
        return {
            "last_attempt_at": values.get("search_console_sitemap_last_attempt_at"),
            "last_submitted_at": values.get("search_console_sitemap_last_submitted_at"),
            "status": values.get("search_console_sitemap_last_status") or "never",
            "http_status": values.get("search_console_sitemap_last_http_status"),
            "last_error": values.get("search_console_sitemap_last_error") or "",
            "sitemap_url": values.get("search_console_sitemap_last_url") or "",
            "site_url": values.get("search_console_sitemap_last_site_url") or "",
            "auth_source": values.get("search_console_sitemap_last_auth_source") or "",
        }

    async def enqueue_indexnow_urls(
        self,
        urls: List[str],
        *,
        reason: str = "catalog_change",
    ) -> int:
        """Queue canonical public URLs for bounded IndexNow submission."""
        rows = []
        seen = set()
        clean_reason = str(reason or "catalog_change")[:80]
        for url in urls or []:
            url = str(url or "").strip()
            if not (url.startswith("https://") or url.startswith("http://")):
                continue
            if url in seen:
                continue
            seen.add(url)
            rows.append((url[:1000], clean_reason))

        if not rows:
            return 0

        async with self.get_connection() as db:
            await db.executemany(
                """
                INSERT INTO indexnow_submissions (
                    url, reason, status, attempts, http_status, last_error,
                    first_seen, updated_at, submitted_at
                )
                VALUES (?, ?, 'pending', 0, NULL, NULL, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, NULL)
                ON CONFLICT(url) DO UPDATE SET
                    reason = excluded.reason,
                    status = 'pending',
                    attempts = 0,
                    http_status = NULL,
                    last_error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                """,
                rows,
            )
            await db.commit()
        return len(rows)

    async def get_pending_indexnow_submissions(
        self,
        *,
        limit: int = 100,
        max_attempts: int = 5,
    ) -> List[Dict[str, Any]]:
        """Return pending IndexNow URLs ready to submit."""
        limit = max(1, min(limit, 1000))
        max_attempts = max(1, max_attempts)
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                """
                UPDATE indexnow_submissions
                SET status = 'failed',
                    last_error = COALESCE(last_error, 'max attempts reached'),
                    updated_at = CURRENT_TIMESTAMP
                WHERE status = 'pending'
                  AND attempts >= ?
                """,
                (max_attempts,),
            )
            await db.commit()
            async with db.execute(
                """
                SELECT id, url, reason, attempts, first_seen, updated_at
                FROM indexnow_submissions
                WHERE status = 'pending'
                  AND attempts < ?
                ORDER BY updated_at ASC, id ASC
                LIMIT ?
                """,
                (max_attempts, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def mark_indexnow_submissions_submitted(
        self,
        urls: List[str],
        *,
        http_status: int,
    ) -> None:
        """Mark IndexNow URLs as accepted by the endpoint."""
        clean_urls = [str(url)[:1000] for url in urls or [] if url]
        if not clean_urls:
            return
        placeholders = ",".join("?" * len(clean_urls))
        async with self.get_connection() as db:
            await db.execute(
                f"""
                UPDATE indexnow_submissions
                SET status = 'submitted',
                    attempts = attempts + 1,
                    http_status = ?,
                    last_error = NULL,
                    submitted_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE url IN ({placeholders})
                """,
                (int(http_status), *clean_urls),
            )
            await db.commit()

    async def mark_indexnow_submissions_failed(
        self,
        urls: List[str],
        *,
        http_status: Optional[int] = None,
        error: str = "",
        retry: bool = True,
    ) -> None:
        """Record a failed IndexNow batch; keep retryable failures pending."""
        clean_urls = [str(url)[:1000] for url in urls or [] if url]
        if not clean_urls:
            return
        placeholders = ",".join("?" * len(clean_urls))
        next_status = "pending" if retry else "failed"
        async with self.get_connection() as db:
            await db.execute(
                f"""
                UPDATE indexnow_submissions
                SET status = ?,
                    attempts = attempts + 1,
                    http_status = ?,
                    last_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE url IN ({placeholders})
                """,
                (next_status, http_status, str(error or "")[:500], *clean_urls),
            )
            await db.commit()

    async def get_indexnow_submission_count(self) -> int:
        """Return total IndexNow queue rows."""
        async with self.get_connection() as db:
            async with db.execute("SELECT COUNT(*) FROM indexnow_submissions") as cursor:
                row = await cursor.fetchone()
                return int(row[0] or 0) if row else 0

    async def get_indexnow_snapshot(self) -> Dict[str, Any]:
        """Return a compact IndexNow queue snapshot for admin status."""
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row

            async def scalar(sql: str, params: tuple = ()) -> int:
                async with db.execute(sql, params) as cursor:
                    row = await cursor.fetchone()
                    return int(row[0] or 0) if row else 0

            async with db.execute(
                """
                SELECT url, reason, status, attempts, http_status, last_error,
                       first_seen, updated_at, submitted_at
                FROM indexnow_submissions
                ORDER BY updated_at DESC, id DESC
                LIMIT 1
                """
            ) as cursor:
                last_row = await cursor.fetchone()

            return {
                "pending": await scalar("SELECT COUNT(*) FROM indexnow_submissions WHERE status = 'pending'"),
                "submitted": await scalar("SELECT COUNT(*) FROM indexnow_submissions WHERE status = 'submitted'"),
                "failed": await scalar("SELECT COUNT(*) FROM indexnow_submissions WHERE status = 'failed'"),
                "total": await scalar("SELECT COUNT(*) FROM indexnow_submissions"),
                "last": dict(last_row) if last_row else None,
            }

    async def get_recent_indexnow_product_changes(
        self,
        since: datetime,
        *,
        limit: int = 250,
    ) -> List[Dict[str, Any]]:
        """Return recently added/recategorized products that should be re-announced."""
        limit = max(1, min(limit, 1000))
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"""
                SELECT id, caption, category, subcategory, created_at
                FROM products
                WHERE created_at >= ?
                  AND (category IS NULL OR category = '' OR category NOT IN ({_EXCLUDED_PLACEHOLDERS}))
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (since, *EXCLUDED_FROM_ALL_PRODUCTS, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_recent_indexnow_deleted_products(
        self,
        since: datetime,
        *,
        limit: int = 250,
    ) -> List[Dict[str, Any]]:
        """Return recently deleted product redirect metadata for IndexNow URL deletion notices."""
        limit = max(1, min(limit, 1000))
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT product_id, caption, category, subcategory, deleted_at
                FROM deleted_product_redirects
                WHERE deleted_at >= ?
                ORDER BY deleted_at ASC, product_id ASC
                LIMIT ?
                """,
                (since, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Broadcast chat CRUD methods
    # ------------------------------------------------------------------

    async def upsert_broadcast_chat(
        self,
        bot_username: str,
        chat_id: int,
        chat_type: str,
        title: Optional[str] = None,
        added_by_user_id: Optional[int] = None,
    ) -> None:
        """
        Insert or update a broadcast destination chat entry.

        When the chat already exists the title and updated_at fields are refreshed
        but enabled is left unchanged so that an admin-configured preference is not
        accidentally reset when the bot re-encounters the same chat.
        """
        bot_username = self.normalize_bot_username(bot_username)
        now = datetime.now()
        async with self.get_connection() as db:
            await db.execute(
                """
                INSERT INTO broadcast_chats
                    (bot_username, chat_id, chat_type, title, enabled, added_at, updated_at, added_by_user_id)
                VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(bot_username, chat_id) DO UPDATE SET
                    chat_type       = excluded.chat_type,
                    title           = COALESCE(excluded.title, broadcast_chats.title),
                    updated_at      = excluded.updated_at,
                    added_by_user_id = COALESCE(broadcast_chats.added_by_user_id, excluded.added_by_user_id)
                """,
                (bot_username, chat_id, chat_type, title, now, now, added_by_user_id),
            )
            await db.commit()
        logger.debug(
            f"upsert_broadcast_chat: bot=@{bot_username} chat_id={chat_id} type={chat_type}"
        )

    async def delete_broadcast_chat(self, bot_username: str, chat_id: int) -> int:
        """Remove a broadcast destination permanently. Returns number of rows deleted."""
        bot_username = self.normalize_bot_username(bot_username)
        async with self.get_connection() as db:
            cursor = await db.execute(
                "DELETE FROM broadcast_chats WHERE bot_username = ? AND chat_id = ?",
                (bot_username, chat_id),
            )
            await db.commit()
            deleted = cursor.rowcount
        logger.debug(
            f"delete_broadcast_chat: bot=@{bot_username} chat_id={chat_id} deleted={deleted}"
        )
        return deleted

    async def set_broadcast_chat_enabled(
        self, bot_username: str, chat_id: int, enabled: bool
    ) -> bool:
        """
        Enable or disable broadcasting for a specific chat.

        Returns True if the row was found and updated, False if the chat is not
        registered as a broadcast destination for this bot.
        """
        bot_username = self.normalize_bot_username(bot_username)
        now = datetime.now()
        async with self.get_connection() as db:
            cursor = await db.execute(
                """
                UPDATE broadcast_chats
                SET enabled = ?,
                    delivery_failure_count = CASE WHEN ? = 1 THEN 0 ELSE delivery_failure_count END,
                    last_delivery_failure = CASE WHEN ? = 1 THEN NULL ELSE last_delivery_failure END,
                    last_delivery_error = CASE WHEN ? = 1 THEN NULL ELSE last_delivery_error END,
                    updated_at = ?
                WHERE bot_username = ? AND chat_id = ?
                """,
                (1 if enabled else 0, 1 if enabled else 0, 1 if enabled else 0, 1 if enabled else 0, now, bot_username, chat_id),
            )
            await db.commit()
            updated = cursor.rowcount > 0
        logger.debug(
            f"set_broadcast_chat_enabled: bot=@{bot_username} chat_id={chat_id} enabled={enabled} updated={updated}"
        )
        return updated

    async def record_broadcast_chat_success(self, bot_username: str, chat_id: int) -> None:
        """Reset delivery failure state after a successful broadcast send."""
        bot_username = self.normalize_bot_username(bot_username)
        now = datetime.now()
        async with self.get_connection() as db:
            await db.execute(
                """
                UPDATE broadcast_chats
                SET delivery_failure_count = 0,
                    last_delivery_failure = NULL,
                    last_delivery_error = NULL,
                    updated_at = ?
                WHERE bot_username = ? AND chat_id = ?
                """,
                (now, bot_username, chat_id),
            )
            await db.commit()

    async def record_broadcast_chat_failure(
        self,
        bot_username: str,
        chat_id: int,
        error: str,
        *,
        disable_after: int = 2,
    ) -> Dict[str, Any]:
        """
        Record a failed broadcast delivery and disable the destination after
        ``disable_after`` consecutive failures.
        """
        bot_username = self.normalize_bot_username(bot_username)
        now = datetime.now()
        error = (error or "delivery failed")[:500]
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                """
                UPDATE broadcast_chats
                SET delivery_failure_count = COALESCE(delivery_failure_count, 0) + 1,
                    last_delivery_failure = ?,
                    last_delivery_error = ?,
                    enabled = CASE
                        WHEN COALESCE(delivery_failure_count, 0) + 1 >= ? THEN 0
                        ELSE enabled
                    END,
                    updated_at = ?
                WHERE bot_username = ? AND chat_id = ?
                """,
                (now, error, disable_after, now, bot_username, chat_id),
            )
            await db.commit()
            async with db.execute(
                """
                SELECT delivery_failure_count, enabled, last_delivery_error
                FROM broadcast_chats
                WHERE bot_username = ? AND chat_id = ?
                """,
                (bot_username, chat_id),
            ) as cursor:
                row = await cursor.fetchone()
                if not row:
                    return {"failure_count": 0, "disabled": False, "updated": False}
                return {
                    "failure_count": row["delivery_failure_count"],
                    "disabled": row["enabled"] == 0,
                    "updated": True,
                    "last_delivery_error": row["last_delivery_error"],
                }

    async def get_broadcast_chats(self, bot_username: str) -> List[Dict[str, Any]]:
        """Return all broadcast destinations for a bot (both enabled and disabled)."""
        bot_username = self.normalize_bot_username(bot_username)
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT id, bot_username, chat_id, chat_type, title, enabled,
                       delivery_failure_count, last_delivery_failure, last_delivery_error,
                       added_at, updated_at, added_by_user_id
                FROM broadcast_chats
                WHERE bot_username = ?
                ORDER BY added_at ASC
                """,
                (bot_username,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_enabled_broadcast_chats(self, bot_username: str) -> List[Dict[str, Any]]:
        """Return only enabled broadcast destinations for a bot."""
        bot_username = self.normalize_bot_username(bot_username)
        async with self.get_connection() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT id, bot_username, chat_id, chat_type, title, enabled,
                       delivery_failure_count, last_delivery_failure, last_delivery_error,
                       added_at, updated_at, added_by_user_id
                FROM broadcast_chats
                WHERE bot_username = ? AND enabled = 1
                ORDER BY added_at ASC
                """,
                (bot_username,),
            ) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    # ------------------------------------------------------------------

    async def run_maintenance(self) -> None:
        """
        Run optional periodic database maintenance (VACUUM + WAL checkpoint).

        This method is intended for use by a scheduled background job — e.g. a
        nightly cron or a long-running asyncio task that fires once per day.
        It must NOT be called automatically on every delete or insert, as VACUUM
        is an exclusive operation that blocks all readers and writers for its
        duration.

        Safe to skip entirely if free-page reuse via WAL mode is sufficient.
        """
        async with self.get_connection() as db:
            await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            await db.execute("VACUUM")
            logger.info("Database maintenance complete (WAL checkpoint + VACUUM)")


async def clear_database_caches():
    """Clear all in-memory database caches. Useful for testing or after bulk updates."""
    global _user_language_cache, _order_contact_cache, _order_link_cache, _order_links_cache

    async with _cache_lock:
        _user_language_cache.clear()
        _order_contact_cache = None
        _order_link_cache = None
        _order_links_cache = None

    logger.info("Database caches cleared")



    
