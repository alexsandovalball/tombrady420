"""
Database models and operations for the media catalog bot.
Uses SQLite with aiosqlite for async operations.

This module provides:
- Database class with async init_db() that creates schema and performs safe migrations
- Product CRUD with category/subcategory support
- Helper queries used by handlers (catalog listing, category listing, stats, users)
- A module-level `db` instance and `init_db()` convenience function
"""

from __future__ import annotations

import aiosqlite
import os
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

logger = logging.getLogger(__name__)

DB_PATH = os.getenv("DB_PATH", "data/catalog.db")
_db_dir = os.path.dirname(DB_PATH) or "."
try:
    os.makedirs(_db_dir, exist_ok=True)
    logger.info("Using persistent database path: %s", DB_PATH)
except Exception:
    logger.exception("Failed to ensure database directory exists for path: %s", DB_PATH)

__all__ = ["db", "DB_PATH", "init_db"]


class Database:
    """Async SQLite database wrapper for the media catalog."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    # -------------------------
    # Initialization and migration
    # -------------------------
    async def init_db(self) -> None:
        """
        Initialize database schema and run idempotent migrations.
        This method is safe to call on every startup.
        """
        logger.info("Initializing database at %s", self.db_path)
        async with aiosqlite.connect(self.db_path) as db:
            try:
                await db.execute("PRAGMA journal_mode=WAL;")
            except Exception:
                logger.debug("PRAGMA journal_mode=WAL failed or not supported")

            await db.execute(
                """
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
                """
            )

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS pagination_state (
                    user_id INTEGER NOT NULL,
                    state_type TEXT NOT NULL,
                    query TEXT,
                    page INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (user_id, state_type, query)
                )
                """
            )

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS user_last_search (
                    user_id INTEGER PRIMARY KEY,
                    query TEXT NOT NULL,
                    page INTEGER NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS ignored_messages (
                    message_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (message_id, chat_id)
                )
                """
            )

            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    last_seen TIMESTAMP
                )
                """
            )

            await db.commit()
            logger.info("Base schema ensured")

            await self._ensure_category_columns(db)

            logger.info("Database initialized successfully")

    async def _ensure_category_columns(self, db: aiosqlite.Connection) -> None:
        """
        Ensure 'category' and 'subcategory' columns exist in products table.
        This is idempotent and safe to call repeatedly.
        """
        try:
            async with db.execute("PRAGMA table_info(products);") as cur:
                rows = await cur.fetchall()
                existing_cols = {r[1] for r in rows}
            to_add: List[str] = []
            if "category" not in existing_cols:
                to_add.append("ALTER TABLE products ADD COLUMN category TEXT;")
            if "subcategory" not in existing_cols:
                to_add.append("ALTER TABLE products ADD COLUMN subcategory TEXT;")
            if to_add:
                logger.info("Applying migrations to add product category columns")
                for stmt in to_add:
                    try:
                        await db.execute(stmt)
                    except Exception as e:
                        logger.warning("Migration statement failed (ignored): %s -> %s", stmt, e)
                await db.commit()
                logger.info("Category columns added (if they were missing)")
            else:
                logger.debug("Category columns already present; no migration needed")
        except Exception:
            logger.exception("Failed to ensure category columns")

    # -------------------------
    # Product operations
    # -------------------------
    async def add_product(
        self,
        file_id: str,
        file_type: str,
        caption: Optional[str],
        message_id: int,
        chat_id: int,
        media_group_id: Optional[str] = None,
        category: Optional[str] = None,
        subcategory: Optional[str] = None,
    ) -> int:
        """
        Add a new product to the database.
        Returns the product id on success, -1 on failure.
        If a matching product already exists (by unique constraint), returns its id.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cat = category.strip().upper() if category else None
                sub = subcategory.strip().upper() if subcategory else None

                created_at = datetime.utcnow().isoformat()
                cursor = await db.execute(
                    """
                    INSERT OR IGNORE INTO products
                        (file_id, file_type, caption, message_id, chat_id, media_group_id, category, subcategory, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        file_id,
                        file_type,
                        caption or "",
                        int(message_id),
                        int(chat_id),
                        media_group_id,
                        cat,
                        sub,
                        created_at,
                    ),
                )
                await db.commit()

                last_id = getattr(cursor, "lastrowid", None)
                if last_id:
                    return int(last_id)

                async with db.execute(
                    "SELECT id FROM products WHERE message_id = ? AND chat_id = ? AND file_id = ? LIMIT 1",
                    (int(message_id), int(chat_id), file_id),
                ) as cur2:
                    row2 = await cur2.fetchone()
                    if row2:
                        return int(row2["id"])

                async with db.execute(
                    "SELECT id FROM products WHERE file_id = ? AND chat_id = ? LIMIT 1",
                    (file_id, int(chat_id)),
                ) as cur3:
                    row3 = await cur3.fetchone()
                    if row3:
                        return int(row3["id"])

                return -1
        except Exception:
            logger.exception("Unexpected error in add_product")
            return -1

    async def delete_product(self, product_id: int) -> bool:
        """Delete a product by id. Returns True if a row was deleted."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute("DELETE FROM products WHERE id = ?", (int(product_id),))
                await db.commit()
                if getattr(cursor, "rowcount", None) not in (None, -1):
                    return cursor.rowcount > 0
                async with db.execute("SELECT 1 FROM products WHERE id = ? LIMIT 1", (int(product_id),)) as cur:
                    row = await cur.fetchone()
                    return row is None
        except Exception:
            logger.exception("Failed to delete product id=%s", product_id)
            return False

    async def get_all_products(self) -> List[Dict[str, Any]]:
        """Return all products (useful for admin operations like nuke)."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM products ORDER BY created_at DESC") as cur:
                    rows = await cur.fetchall()
                    return [dict(r) for r in rows]
        except Exception:
            logger.exception("Failed to fetch all products")
            return []

    async def get_catalog_items(self, limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, Any]]:
        """
        Return catalog items for menu display.
        For media groups, return only one representative row per media_group_id.
        """
        try:
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
                params: Tuple = ()
                if limit is not None:
                    base_query += " LIMIT ? OFFSET ?"
                    params = (int(limit), int(offset))
                async with db.execute(base_query, params) as cur:
                    rows = await cur.fetchall()
                    return [dict(r) for r in rows]
        except Exception:
            logger.exception("Failed to fetch catalog items")
            return []

    async def search_products(self, query: str, limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, Any]]:
        """
        Search products by caption, category, or subcategory (case-insensitive).
        Returns a list of product dicts ordered by created_at DESC.
        """
        try:
            if not query:
                return []

            q_norm = f"%{query.strip().upper()}%"

            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                sql = """
                    SELECT * FROM products
                    WHERE UPPER(TRIM(caption)) LIKE ?
                       OR UPPER(TRIM(category)) LIKE ?
                       OR UPPER(TRIM(subcategory)) LIKE ?
                    ORDER BY created_at DESC
                """
                params: Tuple = (q_norm, q_norm, q_norm)
                if limit is not None:
                    sql += " LIMIT ? OFFSET ?"
                    params = tuple(list(params) + [int(limit), int(offset)])

                async with db.execute(sql, params) as cur:
                    rows = await cur.fetchall()
                    return [dict(r) for r in rows]
        except Exception:
            logger.exception("search_products failed for query=%s", query)
            return []

    # -------------------------
    # Category operations
    # -------------------------
    async def set_product_category(self, product_id: int, category: Optional[str], subcategory: Optional[str] = None) -> bool:
        return await self.update_product_category(product_id, category, subcategory)

    async def update_product_category(self, product_id: int, category: Optional[str], subcategory: Optional[str] = None) -> bool:
        try:
            cat = category.strip().upper() if category else None
            sub = subcategory.strip().upper() if subcategory else None
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE products SET category = ?, subcategory = ? WHERE id = ?",
                    (cat, sub, int(product_id)),
                )
                await db.commit()
            logger.info("Updated product %s -> category=%s subcategory=%s", product_id, cat, sub)
            return True
        except Exception:
            logger.exception("update_product_category failed for id=%s", product_id)
            return False

    async def get_products_by_category(self, category: str, subcategory: Optional[str] = None, limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, Any]]:
        try:
            if not category:
                return []
            cat = category.strip().upper()
            sub = subcategory.strip().upper() if subcategory else None

            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                if sub:
                    q = """
                        SELECT * FROM products
                        WHERE UPPER(TRIM(category)) = ? AND UPPER(TRIM(subcategory)) = ?
                        ORDER BY created_at DESC
                    """
                    params: Tuple = (cat, sub)
                else:
                    q = """
                        SELECT * FROM products
                        WHERE UPPER(TRIM(category)) = ?
                        ORDER BY created_at DESC
                    """
                    params = (cat,)

                if limit is not None:
                    q += " LIMIT ? OFFSET ?"
                    params = tuple(list(params) + [int(limit), int(offset)])

                async with db.execute(q, params) as cur:
                    rows = await cur.fetchall()
                    return [dict(r) for r in rows]
        except Exception:
            logger.exception("get_products_by_category failed for %s/%s", category, subcategory)
            return []

    async def update_products_by_media_group(self, media_group_id: str, chat_id: int, category: Optional[str], subcategory: Optional[str] = None) -> bool:
        try:
            cat = category.strip().upper() if category else None
            sub = subcategory.strip().upper() if subcategory else None
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    "UPDATE products SET category = ?, subcategory = ? WHERE media_group_id = ? AND chat_id = ?",
                    (cat, sub, media_group_id, int(chat_id)),
                )
                await db.commit()
            logger.info("Updated media_group %s (chat=%s) -> category=%s subcategory=%s", media_group_id, chat_id, cat, sub)
            return True
        except Exception:
            logger.exception("update_products_by_media_group failed for %s/%s", media_group_id, chat_id)
            return False

    async def get_all_categories(self) -> List[Dict[str, Optional[str]]]:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT DISTINCT category, subcategory FROM products WHERE category IS NOT NULL ORDER BY category, subcategory"
                ) as cur:
                    rows = await cur.fetchall()
                    return [{"category": r["category"], "subcategory": r["subcategory"]} for r in rows]
        except Exception:
            logger.exception("get_all_categories failed")
            return []

    # -------------------------
    # New helper methods required by handlers/product_view
    # -------------------------
    async def get_product(self, product_id: int) -> Optional[Dict[str, Any]]:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM products WHERE id = ? LIMIT 1", (int(product_id),)) as cur:
                    row = await cur.fetchone()
                    return dict(row) if row else None
        except Exception:
            logger.exception("get_product failed for id=%s", product_id)
            return None

    async def get_products_by_media_group(self, media_group_id: str, chat_id: int) -> List[Dict[str, Any]]:
        """Return all products that belong to a media_group in a chat, ordered by id."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT * FROM products WHERE media_group_id = ? AND chat_id = ? ORDER BY id ASC",
                    (media_group_id, int(chat_id)),
                ) as cur:
                    rows = await cur.fetchall()
                    return [dict(r) for r in rows]
        except Exception:
            logger.exception("get_products_by_media_group failed for %s/%s", media_group_id, chat_id)
            return []

    async def delete_products_by_media_group(self, media_group_id: str, chat_id: int) -> List[int]:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT id FROM products WHERE media_group_id = ? AND chat_id = ?",
                    (media_group_id, int(chat_id)),
                ) as cur:
                    rows = await cur.fetchall()
                    ids = [int(r["id"]) for r in rows] if rows else []
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    await db.execute(f"DELETE FROM products WHERE id IN ({placeholders})", tuple(ids))
                    await db.commit()
                return ids
        except Exception:
            logger.exception("delete_products_by_media_group failed for %s/%s", media_group_id, chat_id)
            return []

    async def delete_products_by_message(self, message_id: int, chat_id: int) -> List[int]:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT id FROM products WHERE message_id = ? AND chat_id = ?",
                    (int(message_id), int(chat_id)),
                ) as cur:
                    rows = await cur.fetchall()
                    ids = [int(r["id"]) for r in rows] if rows else []
                if ids:
                    placeholders = ",".join("?" for _ in ids)
                    await db.execute(f"DELETE FROM products WHERE id IN ({placeholders})", tuple(ids))
                    await db.commit()
                return ids
        except Exception:
            logger.exception("delete_products_by_message failed for %s/%s", message_id, chat_id)
            return []

    # -------------------------
    # Stats and users
    # -------------------------
    async def get_stats(self) -> Dict[str, Any]:
        """Get statistics for admin: total products and products added today."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT COUNT(*) AS cnt FROM products") as cur:
                    total_row = await cur.fetchone()
                    total = int(total_row["cnt"]) if total_row and "cnt" in total_row.keys() else 0
                async with db.execute("SELECT COUNT(*) AS cnt FROM products WHERE DATE(created_at) = DATE('now')") as cur2:
                    today_row = await cur2.fetchone()
                    today = int(today_row["cnt"]) if today_row and "cnt" in today_row.keys() else 0
                return {"total": int(total), "today": int(today)}
        except Exception:
            logger.exception("get_stats failed")
            return {"total": 0, "today": 0}

    async def save_user(self, user_id: int, username: Optional[str] = None, first_name: Optional[str] = None, last_name: Optional[str] = None, last_seen: Optional[datetime] = None) -> None:
        """Insert or update a user record."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    INSERT INTO users (user_id, username, first_name, last_name, last_seen)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        username=excluded.username,
                        first_name=excluded.first_name,
                        last_name=excluded.last_name,
                        last_seen=excluded.last_seen
                    """,
                    (int(user_id), username, first_name, last_name, last_seen or datetime.utcnow()),
                )
                await db.commit()
        except Exception:
            logger.exception("Failed to save user %s", user_id)

    async def get_all_users(self) -> List[Dict[str, Any]]:
        """Return all users ordered by most recent interaction."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    """
                    SELECT user_id, username, first_name, last_name, last_seen
                    FROM users
                    ORDER BY COALESCE(last_seen, '1970-01-01') DESC
                    """
                ) as cur:
                    rows = await cur.fetchall()
                    return [dict(r) for r in rows]
        except Exception:
            logger.exception("Failed to fetch users")
            return []

    # -------------------------
    # Ignored messages helper
    # -------------------------
    async def is_message_ignored(self, message_id: int, chat_id: int) -> bool:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                async with db.execute("SELECT 1 FROM ignored_messages WHERE message_id = ? AND chat_id = ? LIMIT 1", (int(message_id), int(chat_id))) as cur:
                    row = await cur.fetchone()
                    return row is not None
        except Exception:
            logger.exception("is_message_ignored failed for %s/%s", message_id, chat_id)
            return False

    async def add_ignored_message(self, message_id: int, chat_id: int) -> bool:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("INSERT OR IGNORE INTO ignored_messages (message_id, chat_id) VALUES (?, ?)", (int(message_id), int(chat_id)))
                await db.commit()
                return True
        except Exception:
            logger.exception("add_ignored_message failed for %s/%s", message_id, chat_id)
            return False


# Module-level instance and convenience init
db = Database(DB_PATH)


async def init_db() -> None:
    """
    Convenience wrapper to initialize the module-level db instance.
    """
    await db.init_db()