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
            
            await db.commit()
            logger.info("Database initialized successfully")
    
    async def add_product(
        self,
        file_id: str,
        file_type: str,
        caption: Optional[str],
        message_id: int,
        chat_id: int
    ) -> int:
        """Add a new product to the database."""
        async with aiosqlite.connect(self.db_path) as db:
            try:
                cursor = await db.execute("""
                    INSERT INTO products (file_id, file_type, caption, message_id, chat_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (file_id, file_type, caption or "", message_id, chat_id, datetime.now()))
                await db.commit()
                product_id = cursor.lastrowid
                logger.info(f"Product added: ID={product_id}, message_id={message_id}")
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

