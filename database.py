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

DB_PATH = os.getenv("DB_PATH", "/data/catalog.db")
_db_dir = os.path.dirname(DB_PATH) or "."
os.makedirs(_db_dir, exist_ok=True)
logger.info("Using persistent database path: %s", DB_PATH)


class Database:
    """Database manager for product catalog."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    async def init_db(self):
        """Initialize database schema."""
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute("PRAGMA journal_mode=WAL;")
            except Exception:
                pass

            await db.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    caption TEXT,
                    message_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    media_group_id TEXT,
                    category TEXT,
                    subcategory TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(message_id, chat_id, file_id)
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
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    last_seen TIMESTAMP
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
        chat_id: int,
        media_group_id: Optional[str] = None,
        category: Optional[str] = None,
        subcategory: Optional[str] = None
    ) -> int:
        """Add a new product to the database."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            try:
                await db.execute("""
                    INSERT OR IGNORE INTO products
                        (file_id, file_type, caption, message_id, chat_id, media_group_id, category, subcategory, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (file_id, file_type, caption or "", message_id, chat_id, media_group_id, category, subcategory, datetime.now()))
                await db.commit()

                async with db.execute(
                    "SELECT id FROM products WHERE file_id = ? AND chat_id = ? LIMIT 1",
                    (file_id, chat_id)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        return row["id"]

                async with db.execute(
                    "SELECT id FROM products WHERE message_id = ? AND chat_id = ? AND file_id = ? LIMIT 1",
                    (message_id, chat_id, file_id)
                ) as cursor2:
                    row2 = await cursor2.fetchone()
                    if row2:
                        return row2["id"]

                return -1
            except Exception as exc:
                logger.exception("Unexpected error in add_product: %s", exc)
                return -1

    async def get_catalog_items(self, limit: int = None, offset: int = 0) -> List[Dict[str, Any]]:
        """Return catalog items for menu display."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            base_query = """
            SELECT * FROM (
                SELECT * FROM products WHERE media_group_id IS NULL
                UNION
                SELECT * FROM products WHERE id IN (
                    SELECT MIN(id) FROM products WHERE media_group_id IS NOT NULL GROUP BY media_group_id
                )
            ) AS catalog_items
            ORDER BY created_at DESC
            """
            params: tuple = ()
            if limit is not None:
                base_query += " LIMIT ? OFFSET ?"
                params = (int(limit), int(offset))
            async with db.execute(base_query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def set_product_category(self, product_id: int, category: str, subcategory: Optional[str] = None) -> bool:
        """Update category/subcategory for a product."""
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute(
                    "UPDATE products SET category = ?, subcategory = ? WHERE id = ?",
                    (category, subcategory, product_id)
                )
                await db.commit()
                return True
            except Exception as e:
                logger.exception("Failed to set category: %s", e)
                return False

    async def get_products_by_category(self, category: str, subcategory: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fetch products filtered by category/subcategory."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if subcategory:
                query = "SELECT * FROM products WHERE category = ? AND subcategory = ? ORDER BY created_at DESC"
                params = (category, subcategory)
            else:
                query = "SELECT * FROM products WHERE category = ? ORDER BY created_at DESC"
                params = (category,)
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_stats(self) -> Dict[str, Any]:
        """Get statistics for admin."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM products") as cursor:
                total_row = await cursor.fetchone()
                total = total_row[0] if total_row else 0
            async with db.execute("SELECT COUNT(*) FROM products WHERE DATE(created_at) = DATE('now')") as cursor:
                today_row = await cursor.fetchone()
                today = today_row[0] if today_row else 0
            return {"total": total, "today": today}

    async def save_user(self, user_id: int, username: Optional[str] = None,
                        first_name: Optional[str] = None, last_name: Optional[str] = None,
                        last_seen: Optional[datetime] = None):
        """Insert or update a user record."""
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute("""
                    INSERT INTO users (user_id, username, first_name, last_name, last_seen)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        username=excluded.username,
                        first_name=excluded.first_name,
                        last_name=excluded.last_name,
                        last_seen=excluded.last_seen
                """, (user_id, username, first_name, last_name, last_seen or datetime.now()))
                await db.commit()
            except Exception as e:
                logger.exception("Failed to save user %s: %s", user_id, e)

    async def get_all_users(self) -> List[Dict[str, Any]]:
        """Return all users ordered by most recent interaction."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT user_id, username, first_name, last_name, last_seen
                FROM users
                ORDER BY COALESCE(last_seen, '1970-01-01') DESC
            """) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]


# Expose a module-level Database instance
db = Database(DB_PATH)

async def init_db():
    logger.info("Initializing database via module-level init_db() at %s", DB_PATH)
    await db.init_db()