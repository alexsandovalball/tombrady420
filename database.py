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

# Use a persistent path by default; Render persistent disk will be mounted at /data
DB_PATH = os.getenv("DB_PATH", "/data/catalog.db")

# Ensure the directory exists before any DB connections
_db_dir = os.path.dirname(DB_PATH) or "."
os.makedirs(_db_dir, exist_ok=True)
logger.info("Using persistent database path: %s", DB_PATH)


class Database:
    """Database manager for product catalog."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    async def init_db(self):
        """Initialize database with schema and ensure media_group_id exists."""
        async with aiosqlite.connect(self.db_path) as db:
            # Try to enable WAL for better concurrency
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
            # New: store users who interacted with the bot
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
        media_group_id: Optional[str] = None
    ) -> int:
        """
        Add a new product to the database in an idempotent way.

        Strategy:
        - Use INSERT OR IGNORE to avoid IntegrityError on duplicates.
        - Then SELECT the row by file_id+chat_id (idempotency key).
        - Fallback to selecting by message_id+chat_id+file_id.
        Returns the product id (existing or newly created) or -1 on failure.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            try:
                await db.execute("""
                    INSERT OR IGNORE INTO products
                        (file_id, file_type, caption, message_id, chat_id, media_group_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (file_id, file_type, caption or "", message_id, chat_id, media_group_id, datetime.now()))
                await db.commit()

                # Primary idempotency lookup: file_id + chat_id
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

                # Fallback lookup: message_id + chat_id + file_id
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
        """Get all products with pagination (raw rows)."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM products ORDER BY created_at DESC"
            if limit is not None:
                query += f" LIMIT {int(limit)} OFFSET {int(offset)}"
            async with db.execute(query) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_catalog_items(self, limit: int = None, offset: int = 0) -> List[Dict[str, Any]]:
        """
        Return catalog items for menu display:
        - For messages with a media_group_id, return a single representative row per group
          (the row with the smallest id for that group).
        - For messages without a media_group_id, return the row itself.
        This prevents showing the same caption multiple times when a message contains multiple media.
        """
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

    async def count_products(self) -> int:
        """Get total number of products (raw rows)."""
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

    async def delete_products_by_media_group(self, media_group_id: str, chat_id: int) -> List[int]:
        """
        Delete all products that share the same media_group_id in a chat.
        Returns a list of deleted product ids.
        """
        if not media_group_id:
            return []

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id FROM products WHERE media_group_id = ? AND chat_id = ?",
                (media_group_id, chat_id)
            ) as cursor:
                rows = await cursor.fetchall()
                ids = [r["id"] for r in rows] if rows else []

            if not ids:
                return []

            await db.execute(
                "DELETE FROM products WHERE media_group_id = ? AND chat_id = ?",
                (media_group_id, chat_id)
            )
            await db.commit()
            logger.info("Deleted %d products for media_group_id=%s chat_id=%s", len(ids), media_group_id, chat_id)
            return ids

    async def delete_products_by_message(self, message_id: int, chat_id: int) -> List[int]:
        """
        Delete all products that match a specific message_id and chat_id.
        Returns a list of deleted product ids.
        """
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT id FROM products WHERE message_id = ? AND chat_id = ?",
                (message_id, chat_id)
            ) as cursor:
                rows = await cursor.fetchall()
                ids = [r["id"] for r in rows] if rows else []

            if not ids:
                return []

            await db.execute(
                "DELETE FROM products WHERE message_id = ? AND chat_id = ?",
                (message_id, chat_id)
            )
            await db.commit()
            logger.info("Deleted %d products for message_id=%s chat_id=%s", len(ids), message_id, chat_id)
            return ids

    async def get_product_by_message(self, message_id: int, chat_id: int) -> Optional[Dict[str, Any]]:
        """Get product by message_id and chat_id (first match)."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM products WHERE message_id = ? AND chat_id = ? LIMIT 1",
                (message_id, chat_id)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def get_products_by_media_group(self, media_group_id: str, chat_id: int) -> List[Dict[str, Any]]:
        """Get all products that share the same media_group_id in a chat, ordered by created_at."""
        if not media_group_id:
            return []
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("""
                SELECT * FROM products
                WHERE media_group_id = ? AND chat_id = ?
                ORDER BY created_at ASC
            """, (media_group_id, chat_id)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]

    async def get_stats(self) -> Dict[str, Any]:
        """Get statistics for admin."""
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM products") as cursor:
                total = (await cursor.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM products WHERE DATE(created_at) = DATE('now')") as cursor:
                today = (await cursor.fetchone())[0]
            return {"total": total, "today": today}

    async def save_pagination_state(self, user_id: int, state_type: str, query: Optional[str], page: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO pagination_state (user_id, state_type, query, page, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (user_id, state_type, query or "", page, datetime.now()))
            await db.commit()

    async def get_pagination_state(self, user_id: int, state_type: str, query: Optional[str]) -> Optional[int]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("""
                SELECT page FROM pagination_state WHERE user_id = ? AND state_type = ? AND query = ?
            """, (user_id, state_type, query or "")) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def cleanup_old_pagination_states(self, minutes: int = 10):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                DELETE FROM pagination_state WHERE datetime(created_at) < datetime('now', '-' || ? || ' minutes')
            """, (minutes,))
            await db.commit()

    async def save_last_search(self, user_id: int, query: str, page: int):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO user_last_search (user_id, query, page, updated_at)
                VALUES (?, ?, ?, ?)
            """, (user_id, query, page, datetime.now()))
            await db.commit()

    async def get_last_search(self, user_id: int) -> Optional[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT query, page FROM user_last_search WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                return {"query": row["query"], "page": row["page"]} if row else None

    async def add_ignored_message(self, message_id: int, chat_id: int):
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute("""
                    INSERT OR IGNORE INTO ignored_messages (message_id, chat_id, created_at)
                    VALUES (?, ?, ?)
                """, (message_id, chat_id, datetime.now()))
                await db.commit()
            except Exception as e:
                logger.error(f"Error adding to ignore list: {e}")

    async def is_message_ignored(self, message_id: int, chat_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT 1 FROM ignored_messages WHERE message_id = ? AND chat_id = ?", (message_id, chat_id)) as cursor:
                row = await cursor.fetchone()
                return row is not None

    # -------------------------
    # New: users helpers
    # -------------------------
    async def save_user(
        self,
        user_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        last_seen: Optional[datetime] = None,
    ):
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


# Expose a module-level Database instance and a simple init helper
# so other modules can initialize the schema on startup.
db = Database(DB_PATH)


async def init_db():
    """
    Initialize the database schema using the module-level Database instance.

    Call this from your FastAPI startup event before starting the telegram Application:
        await init_db()
    """
    logger.info("Initializing database via module-level init_db() at %s", DB_PATH)
    await db.init_db()