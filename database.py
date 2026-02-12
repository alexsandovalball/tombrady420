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
from functools import lru_cache

from configs.config import Config

logger = logging.getLogger(__name__)

# Default values
DEFAULT_ORDER_CONTACT = '@FLYAWAYPEP'

# In-memory caches for frequently accessed data
_user_language_cache: Dict[int, Tuple[str, datetime]] = {}
_order_contact_cache: Optional[Tuple[str, datetime]] = None
_cache_lock = asyncio.Lock()
CACHE_TTL_SECONDS = 300  # 5 minutes


class Database:
    """Database manager for product catalog."""
    
    def __init__(self, db_path: str = None):
        self.db_path = db_path or Config.DB_PATH
    
    def get_connection(self):
        """Get a database connection context manager."""
        return aiosqlite.connect(self.db_path)
    
    @staticmethod
    def normalize_bot_username(bot_username: Optional[str]) -> Optional[str]:
        """Normalize bot username to lowercase for consistent database lookups."""
        return bot_username.lower() if bot_username else None
    
    async def init_db(self):
        """Initialize database with schema."""
        async with aiosqlite.connect(self.db_path) as db:
            # Enable WAL mode for better concurrent read/write performance
            await db.execute("PRAGMA journal_mode=WAL")
            logger.info("Enabled WAL mode for database")
            
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
            
            # Set default order contact if not exists
            await db.execute("""
                INSERT OR IGNORE INTO bot_settings (key, value)
                VALUES ('order_contact', ?)
            """, (DEFAULT_ORDER_CONTACT,))
            
            # Add columns if they don't exist (for existing databases)
            try:
                await db.execute("ALTER TABLE products ADD COLUMN media_group_id TEXT")
            except aiosqlite.OperationalError:
                pass  # Column already exists
            
            try:
                await db.execute("ALTER TABLE products ADD COLUMN additional_file_ids TEXT")
            except aiosqlite.OperationalError:
                pass
            
            try:
                await db.execute("ALTER TABLE products ADD COLUMN category TEXT")
            except aiosqlite.OperationalError:
                pass
            
            try:
                await db.execute("ALTER TABLE products ADD COLUMN subcategory TEXT")
            except aiosqlite.OperationalError:
                pass
            
            try:
                await db.execute("ALTER TABLE bot_users ADD COLUMN notifications_enabled INTEGER DEFAULT 1")
            except aiosqlite.OperationalError:
                pass
            
            try:
                await db.execute("ALTER TABLE bot_users ADD COLUMN bot_username TEXT")
            except aiosqlite.OperationalError:
                pass
            
            try:
                await db.execute("ALTER TABLE bot_users ADD COLUMN is_blocked INTEGER DEFAULT 0")
            except aiosqlite.OperationalError:
                pass
            
            try:
                await db.execute("ALTER TABLE products ADD COLUMN bot_username TEXT")
            except aiosqlite.OperationalError:
                pass
            
            try:
                await db.execute("ALTER TABLE bot_users ADD COLUMN language TEXT DEFAULT 'en'")
                logger.info("Added 'language' column to bot_users table")
            except aiosqlite.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    logger.debug("Column 'language' already exists in bot_users table")
                else:
                    logger.warning(f"Could not add 'language' column to bot_users: {e}")
            
            try:
                await db.execute("ALTER TABLE products ADD COLUMN additional_message_ids TEXT")
                logger.info("Added 'additional_message_ids' column to products table")
            except aiosqlite.OperationalError as e:
                if "duplicate column" in str(e).lower():
                    logger.debug("Column 'additional_message_ids' already exists in products table")
                else:
                    logger.warning(f"Could not add 'additional_message_ids' column to products: {e}")
            
            # Migrate existing media groups to populate additional_message_ids
            # For media groups created before this fix, infer message IDs from sequential ordering
            logger.info("Checking for media groups needing message ID migration...")
            await self._migrate_existing_media_groups(db)
            
            # Check for products without bot_username
            logger.info("Checking for products needing bot_username migration...")
            await self._migrate_bot_username_for_existing_products(db)
            
            # Create performance-critical indexes
            logger.info("Creating database indexes...")
            
            # Products table indexes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_products_category ON products(category)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_products_category_subcategory ON products(category, subcategory)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_products_created_at ON products(created_at DESC)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_products_media_group_id ON products(media_group_id)")
            
            # Bot users indexes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_bot_users_notifications ON bot_users(notifications_enabled) WHERE notifications_enabled = 1")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_bot_users_last_seen ON bot_users(last_seen DESC)")
            
            # Notification queue indexes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_notification_queue_user_sent ON notification_queue(user_id, sent_at)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_notification_queue_created ON notification_queue(created_at)")
            
            # Pagination state indexes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_pagination_state_user ON pagination_state(user_id, created_at DESC)")
            
            # Custom message queue indexes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_custom_message_queue_user_sent ON custom_message_queue(user_id, sent_at)")
            
            # Translation cache indexes
            await db.execute("CREATE INDEX IF NOT EXISTS idx_translation_cache_lookup ON translation_cache(source_text, source_lang, target_lang)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_translation_cache_last_used ON translation_cache(last_used DESC)")
            
            logger.info("Database indexes created successfully")
            
            await db.commit()
            logger.info("Database initialized successfully")
    
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
        """Add a new product to the database."""
        async with aiosqlite.connect(self.db_path) as db:
            try:
                cursor = await db.execute("""
                    INSERT INTO products (file_id, file_type, caption, message_id, chat_id, 
                                        media_group_id, additional_file_ids, additional_message_ids, category, subcategory, bot_username, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (file_id, file_type, caption or "", message_id, chat_id, 
                      media_group_id, additional_file_ids, additional_message_ids, category, subcategory, 
                      self.normalize_bot_username(bot_username), datetime.now()))
                await db.commit()
                product_id = cursor.lastrowid
                logger.info(f"Product added: ID={product_id}, message_id={message_id}, category={category}, bot={bot_username}")
                return product_id
            except aiosqlite.IntegrityError:
                logger.warning(f"Product already exists: message_id={message_id}, chat_id={chat_id}")
                return -1
    
    async def get_product(self, product_id: int) -> Optional[Dict[str, Any]]:
        """Get a product by ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM products WHERE id = ?
            """, (product_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
                return None
    
    async def get_all_products(self, limit: int = None, offset: int = 0) -> List[Dict[str, Any]]:
        """Get all products with pagination."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM products ORDER BY created_at DESC"
            if limit:
                query += f" LIMIT {limit} OFFSET {offset}"
            async with db.execute(query) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    
    async def count_products(self) -> int:
        """Get total number of products."""
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
        """Search products by caption (for fuzzy search filtering)."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            sql_query = """
                SELECT * FROM products 
                WHERE caption LIKE ? 
                ORDER BY created_at DESC
            """
            if limit:
                sql_query += f" LIMIT {limit} OFFSET {offset}"
            async with db.execute(sql_query, (f"%{query}%",)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    
    async def get_all_products_for_search(self) -> List[Dict[str, Any]]:
        """Get all products for fuzzy search (no pagination)."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM products ORDER BY created_at DESC") as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    
    async def get_all_products_excluding_categories(self, excluded_categories: List[str]) -> List[Dict[str, Any]]:
        """
        Get all products excluding those in specified categories.
        Products with no category assigned (NULL or empty string) are included.
        
        Args:
            excluded_categories: List of category names to exclude (e.g., ['DATEDPROOFS', 'CLIENTTOUCHDOWNS'])
        
        Returns:
            List of product dictionaries excluding products from specified categories
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            if not excluded_categories:
                # If no categories to exclude, return all products
                async with db.execute("SELECT * FROM products ORDER BY created_at DESC") as cursor:
                    rows = await cursor.fetchall()
                    return [dict(row) for row in rows]
            
            # Build placeholders for SQL query
            placeholders = ','.join('?' * len(excluded_categories))
            query = f"""
                SELECT * FROM products 
                WHERE (category IS NULL OR category = '') OR category NOT IN ({placeholders})
                ORDER BY created_at DESC
            """
            
            async with db.execute(query, excluded_categories) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    
    async def delete_product(self, product_id: int) -> bool:
        """Delete a product by ID."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM products WHERE id = ?", (product_id,))
            await db.commit()
            deleted = cursor.rowcount > 0
            if deleted:
                logger.info(f"Product deleted: ID={product_id}")
            return deleted
    
    async def get_product_by_message(self, message_id: int, chat_id: int) -> Optional[Dict[str, Any]]:
        """Get product by message_id and chat_id."""
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                DELETE FROM pagination_state 
                WHERE datetime(created_at) < datetime('now', '-' || ? || ' minutes')
            """, (minutes,))
            await db.commit()
    
    async def save_last_search(self, user_id: int, query: str, page: int):
        """Save user's last search query and page."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO user_last_search (user_id, query, page, updated_at)
                VALUES (?, ?, ?, ?)
            """, (user_id, query, page, datetime.now()))
            await db.commit()
    
    async def get_last_search(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user's last search query and page."""
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT 1 FROM ignored_messages 
                WHERE message_id = ? AND chat_id = ?
            """, (message_id, chat_id)) as cursor:
                row = await cursor.fetchone()
                return row is not None
    
    async def get_or_create_media_group_product(self, media_group_id: str, chat_id: int) -> Optional[int]:
        """Get existing product_id for a media group or return None if it doesn't exist."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT product_id FROM media_groups 
                WHERE media_group_id = ? AND chat_id = ?
            """, (media_group_id, chat_id)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None
    
    async def register_media_group(self, media_group_id: str, chat_id: int, product_id: int):
        """Register a media group with its product."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO media_groups (media_group_id, chat_id, product_id, created_at)
                VALUES (?, ?, ?, ?)
            """, (media_group_id, chat_id, product_id, datetime.now()))
            await db.commit()
    
    async def update_product_media(self, product_id: int, additional_file_ids: str, additional_message_ids: str = None):
        """Update a product's additional file IDs and message IDs for media groups."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE products SET additional_file_ids = ?, additional_message_ids = ? WHERE id = ?
            """, (additional_file_ids, additional_message_ids, product_id))
            await db.commit()
    
    async def get_products_by_category(self, category: str, limit: int = None, offset: int = 0) -> List[Dict[str, Any]]:
        """Get products filtered by category."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM products WHERE category = ? ORDER BY created_at DESC"
            if limit:
                query += f" LIMIT {limit} OFFSET {offset}"
            async with db.execute(query, (category,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    
    async def count_products_by_category(self, category: str) -> int:
        """Count products in a category."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM products WHERE category = ?", (category,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
    
    async def get_all_category_counts(self) -> Dict[str, int]:
        """Get product counts for all categories in a single query (optimized)."""
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM products WHERE category = ? AND subcategory = ? ORDER BY created_at DESC"
            if limit:
                query += f" LIMIT {limit} OFFSET {offset}"
            async with db.execute(query, (category, subcategory)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
    
    async def count_products_by_category_and_subcategory(self, category: str, subcategory: str) -> int:
        """Count products in a category and subcategory."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT COUNT(*) FROM products WHERE category = ? AND subcategory = ?",
                (category, subcategory)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
    
    async def get_subcategories_with_counts(self, category: str) -> List[Dict[str, Any]]:
        """Get subcategories for a category with product counts."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT subcategory, COUNT(*) as count
                FROM products
                WHERE category = ? AND subcategory IS NOT NULL
                GROUP BY subcategory
                ORDER BY subcategory
            """, (category,)) as cursor:
                rows = await cursor.fetchall()
                return [{"subcategory": row[0], "count": row[1]} for row in rows]
    
    async def update_product_category(self, product_id: int, category: str, subcategory: Optional[str] = None):
        """Update a product's category and subcategory."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE products SET category = ?, subcategory = ? WHERE id = ?
            """, (category, subcategory, product_id))
            await db.commit()
            
            # Remove from pending categorization if it exists
            await db.execute("""
                DELETE FROM pending_categorization WHERE product_id = ?
            """, (product_id,))
            await db.commit()
    
    async def add_pending_categorization(self, product_id: int):
        """Mark a product as needing categorization."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR IGNORE INTO pending_categorization (product_id, notified_at)
                VALUES (?, ?)
            """, (product_id, datetime.now()))
            await db.commit()
    
    async def remove_pending_categorization(self, product_id: int):
        """Remove a product from pending categorization."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                DELETE FROM pending_categorization WHERE product_id = ?
            """, (product_id,))
            await db.commit()
    
    async def get_pending_categorizations(self) -> List[int]:
        """Get list of product IDs that need categorization."""
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM bot_users") as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
    
    async def get_user_by_id(self, user_id: int) -> Optional[Dict[str, Any]]:
        """Get user details by user ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM bot_users WHERE user_id = ?
            """, (user_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
    
    async def user_exists(self, user_id: int) -> bool:
        """Check if a user exists in the database."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT user_id FROM bot_users WHERE user_id = ?
            """, (user_id,)) as cursor:
                row = await cursor.fetchone()
                return row is not None
    
    async def set_user_notifications(self, user_id: int, enabled: bool):
        """Enable or disable notifications for a user."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE bot_users SET notifications_enabled = ? WHERE user_id = ?
            """, (1 if enabled else 0, user_id))
            await db.commit()
    
    async def is_user_subscribed(self, user_id: int) -> bool:
        """Check if user has notifications enabled."""
        async with aiosqlite.connect(self.db_path) as db:
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
            async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
            async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
        """Add a notification to the queue."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO notification_queue (user_id, product_id, created_at)
                VALUES (?, ?, ?)
            """, (user_id, product_id, datetime.now()))
            await db.commit()
    
    async def mark_notification_sent(self, notification_id: int):
        """Mark a notification as sent."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE notification_queue SET sent_at = ? WHERE id = ?
            """, (datetime.now(), notification_id))
            await db.commit()
    
    async def get_pending_notifications(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get pending notifications from queue."""
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT COUNT(*) FROM notification_queue 
                WHERE user_id = ? AND sent_at IS NOT NULL
                AND datetime(sent_at) > datetime('now', '-' || ? || ' minutes')
            """, (user_id, minutes)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
    
    async def block_user(self, user_id: int):
        """Block a user from using the bot."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE bot_users SET is_blocked = 1 WHERE user_id = ?
            """, (user_id,))
            await db.commit()
    
    async def unblock_user(self, user_id: int):
        """Unblock a user."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE bot_users SET is_blocked = 0 WHERE user_id = ?
            """, (user_id,))
            await db.commit()
    
    async def is_user_blocked(self, user_id: int) -> bool:
        """Check if a user is blocked."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT is_blocked FROM bot_users WHERE user_id = ?
            """, (user_id,)) as cursor:
                row = await cursor.fetchone()
                return row[0] == 1 if row else False
    
    async def delete_user(self, user_id: int):
        """
        Delete a user completely from the database.
        This is different from blocking - the user can restart the bot and be re-added.
        Deletes user record and all associated data (notifications, etc.).
        """
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
        """Get list of unique bot usernames that have users (normalized to lowercase)."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT DISTINCT LOWER(bot_username) FROM bot_users 
                WHERE bot_username IS NOT NULL AND bot_username != ''
                ORDER BY 1
            """) as cursor:
                rows = await cursor.fetchall()
                return [row[0] for row in rows]
    
    async def get_users_count_by_bot(self, bot_username: Optional[str] = None) -> int:
        """
        Get count of users for a specific bot or all users if bot_username is None.
        Use bot_username="_untracked_" to get count of users without bot_username.
        """
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT INTO custom_message_queue (user_id, message_text, created_at)
                VALUES (?, ?, ?)
            """, (user_id, message_text, datetime.now()))
            await db.commit()
    
    async def get_pending_custom_messages(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get pending custom messages from queue."""
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE custom_message_queue SET sent_at = ? WHERE id = ?
            """, (datetime.now(), message_id))
            await db.commit()
    
    async def get_recent_custom_messages_count(self, user_id: int, minutes: int = 60) -> int:
        """Count custom messages sent to user in the last N minutes."""
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
        
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
        async with aiosqlite.connect(self.db_path) as db:
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
        
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO bot_settings (key, value, updated_at)
                VALUES ('order_contact', ?, ?)
            """, (contact, datetime.now()))
            await db.commit()
            logger.info(f"Order contact updated to: {contact}")
        
        # Invalidate cache
        async with _cache_lock:
            _order_contact_cache = (contact, datetime.now())
    
    async def get_bot_file_id(
        self,
        source_chat_id: int,
        source_message_id: int,
        file_index: int,
        bot_username: str
    ) -> Optional[str]:
        """Get cached bot-specific file ID from database."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT file_id FROM bot_file_id_cache
                WHERE source_chat_id = ? AND source_message_id = ? 
                  AND file_index = ? AND bot_username = ?
            """, (source_chat_id, source_message_id, file_index, bot_username.lower())) as cursor:
                row = await cursor.fetchone()
                if row:
                    # Update usage stats
                    await db.execute("""
                        UPDATE bot_file_id_cache
                        SET last_used = ?, use_count = use_count + 1
                        WHERE source_chat_id = ? AND source_message_id = ? 
                          AND file_index = ? AND bot_username = ?
                    """, (datetime.now(), source_chat_id, source_message_id, file_index, bot_username.lower()))
                    await db.commit()
                    return row[0]
                return None
    
    async def cache_bot_file_id(
        self,
        source_chat_id: int,
        source_message_id: int,
        file_index: int,
        file_type: str,
        bot_username: str,
        file_id: str
    ):
        """Cache a bot-specific file ID in database."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO bot_file_id_cache 
                (source_chat_id, source_message_id, file_index, file_type, bot_username, file_id, created_at, last_used, use_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (source_chat_id, source_message_id, file_index, file_type, 
                  self.normalize_bot_username(bot_username), file_id, datetime.now(), datetime.now()))
            await db.commit()
            logger.debug(f"Cached file ID for bot {bot_username}: msg={source_message_id}, idx={file_index}")
    
    async def clear_bot_file_id_cache(self):
        """Clear all bot-specific file ID caches (for admin /clearcache command)."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM bot_file_id_cache")
            deleted = cursor.rowcount
            await db.commit()
            logger.info(f"Cleared {deleted} bot file ID cache entries")
            return deleted
    
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
            async with aiosqlite.connect(self.db_path) as db:
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
        
        async with aiosqlite.connect(self.db_path) as db:
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
            async with aiosqlite.connect(self.db_path) as db:
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
        
        async with aiosqlite.connect(self.db_path) as db:
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
            async with aiosqlite.connect(self.db_path) as db:
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
            async with aiosqlite.connect(self.db_path) as db:
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


async def clear_database_caches():
    """Clear all in-memory database caches. Useful for testing or after bulk updates."""
    global _user_language_cache, _order_contact_cache
    
    async with _cache_lock:
        _user_language_cache.clear()
        _order_contact_cache = None
    
    logger.info("Database caches cleared")



    


