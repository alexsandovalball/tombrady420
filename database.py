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
        """Initialize database with schema and ensure media_group_id exists."""
        async with aiosqlite.connect(self.db_path) as db:
            # Ensure WAL mode for better concurrency (optional but helpful)
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
            await db.commit()
            logger.info("Database initialized successfully")

    async def add_product(
        self,
        file_id: str,
        file_type: str,
        caption: Optional[str],
        message_id: int,
        chat_id: int,
        media_group_id: Optional[str] = None
    ) -> int:
        """
        Add a new product to the database in an idempotent way.

        Strategy:
        1. Try to INSERT OR IGNORE the row (so repeated attempts won't raise).
        2. SELECT the id for the row matching file_id and chat_id (most reliable).
        3. If not found by file_id/chat_id, fall back to selecting by message_id/chat_id/file_id.
        Returns the product id (existing or newly created) or -1 on failure.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            try:
                # Use INSERT OR IGNORE to avoid IntegrityError on duplicates
                await db.execute("""
                    INSERT OR IGNORE INTO products
                        (file_id, file_type, caption, message_id, chat_id, media_group_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (file_id, file_type, caption or "", message_id, chat_id, media_group_id, datetime.now()))
                await db.commit()

                # Try to fetch by file_id + chat_id (idempotency key)
                async with db.execute(
                    "SELECT id FROM products WHERE file_id = ? AND chat_id = ? LIMIT 1",
                    (file_id, chat_id)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row:
                        product_id = row["id"]
                        logger.info(
                            "Product ensured (idempotent): ID=%s, message_id=%s, chat_id=%s, file_id=%s",
                            product_id, message_id, chat_id, file_id
                        )
                        return product_id

                # Fallback: try to fetch by message_id + chat_id + file_id
                async with db.execute(
                    "SELECT id FROM products WHERE message_id = ? AND chat_id = ? AND file_id = ? LIMIT 1",
                    (message_id, chat_id, file_id)
                ) as cursor2:
                    row2 = await cursor2.fetchone()
                    if row2:
                        product_id = row2["id"]
                        logger.info(
                            "Product found by message_id fallback: ID=%s, message_id=%s, chat_id=%s",
                            product_id, message_id, chat_id
                        )
                        return product_id

                # If we reach here, insertion didn't create a row and selects failed
                logger.error(
                    "Failed to insert or locate product after INSERT OR IGNORE: file_id=%s chat_id=%s message_id=%s",
                    file_id, chat_id, message_id
                )
                return -1

            except Exception as exc:
                logger.exception("Unexpected error in add_product: %s", exc)
                return -1

    async def get_product(self, product_id: int) -> Optional[Dict[str, Any]]:
        """Get a product by ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM products WHERE id = ?", (product_id,)) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_all_products(self, limit: int = None, offset: int = 0) -> List[Dict[str, Any]]:
        """Get all products with pagination."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM products ORDER BY created_at DESC"
            if limit is not None:
                query += f" LIMIT {int(limit)} OFFSET {int(offset)}"
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
        """Search products by caption (LIKE)."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            sql = "SELECT * FROM products WHERE caption LIKE ? ORDER BY created_at DESC"
            if limit is not None:
                sql += f" LIMIT {int(limit)} OFFSET {int(offset)}"
            async with db.execute(sql, (f"%{query}%",)) as cursor:
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

    async def get_product_by_message(self, message_id: int, chat_id: int