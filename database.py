"""
Database models and operations for the media catalog bot.
Uses SQLite with aiosqlite for async operations.
"""
import aiosqlite
import os
from datetime import datetime
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "catalog.db")


class Database:
    """Database manager for product catalog."""
    
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
    
    async def init_db(self):
        """Initialize database with schema."""
        async with aiosqlite.connect(self.db_path) as db:
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
                    interaction_count INTEGER DEFAULT 1
                )
            """)
            
            # Add columns if they don't exist (for existing databases)
            try:
                await db.execute("ALTER TABLE products ADD COLUMN media_group_id TEXT")
            except:
                pass  # Column already exists
            
            try:
                await db.execute("ALTER TABLE products ADD COLUMN additional_file_ids TEXT")
            except:
                pass
            
            try:
                await db.execute("ALTER TABLE products ADD COLUMN category TEXT")
            except:
                pass
            
            try:
                await db.execute("ALTER TABLE products ADD COLUMN subcategory TEXT")
            except:
                pass
            
            await db.commit()
            logger.info("Database initialized successfully")
    
    async def add_product(
        self,
        file_id: str,
        file_type: str,
        caption: Optional[str],
        message_id: int,
        chat_id: int,
        media_group_id: Optional[str] = None,
        additional_file_ids: Optional[str] = None,
        category: Optional[str] = None,
        subcategory: Optional[str] = None
    ) -> int:
        """Add a new product to the database."""
        async with aiosqlite.connect(self.db_path) as db:
            try:
                cursor = await db.execute("""
                    INSERT INTO products (file_id, file_type, caption, message_id, chat_id, 
                                        media_group_id, additional_file_ids, category, subcategory, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (file_id, file_type, caption or "", message_id, chat_id, 
                      media_group_id, additional_file_ids, category, subcategory, datetime.now()))
                await db.commit()
                product_id = cursor.lastrowid
                logger.info(f"Product added: ID={product_id}, message_id={message_id}, category={category}")
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
    
    async def get_stats(self) -> Dict[str, Any]:
        """Get statistics for admin."""
        async with aiosqlite.connect(self.db_path) as db:
            # Total products
            async with db.execute("SELECT COUNT(*) FROM products") as cursor:
                total = (await cursor.fetchone())[0]
            
            # Products added today
            async with db.execute("""
                SELECT COUNT(*) FROM products 
                WHERE DATE(created_at) = DATE('now')
            """) as cursor:
                today = (await cursor.fetchone())[0]
            
            return {
                "total": total,
                "today": today
            }
    
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
    
    async def update_product_media(self, product_id: int, additional_file_ids: str):
        """Update a product's additional file IDs for media groups."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                UPDATE products SET additional_file_ids = ? WHERE id = ?
            """, (additional_file_ids, product_id))
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
                        first_name: Optional[str] = None, last_name: Optional[str] = None):
        """Track or update a bot user."""
        async with aiosqlite.connect(self.db_path) as db:
            # Check if user exists
            async with db.execute("SELECT user_id FROM bot_users WHERE user_id = ?", (user_id,)) as cursor:
                exists = await cursor.fetchone()
            
            if exists:
                # Update existing user
                await db.execute("""
                    UPDATE bot_users 
                    SET username = ?, first_name = ?, last_name = ?, 
                        last_seen = ?, interaction_count = interaction_count + 1
                    WHERE user_id = ?
                """, (username, first_name, last_name, datetime.now(), user_id))
            else:
                # Insert new user
                await db.execute("""
                    INSERT INTO bot_users (user_id, username, first_name, last_name, first_seen, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (user_id, username, first_name, last_name, datetime.now(), datetime.now()))
            
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
    
    async def count_users(self) -> int:
        """Get total number of users."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM bot_users") as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0

