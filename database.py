# database.py
from __future__ import annotations

import aiosqlite
import os
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple

from config import DB_PATH

logger = logging.getLogger(__name__)

_db_dir = os.path.dirname(DB_PATH) or "."
os.makedirs(_db_dir, exist_ok=True)


class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None
        self._connected = False

    async def connect(self) -> None:
        """Open a single long-lived aiosqlite connection. Call once on startup."""
        if self._connected and self._conn:
            return
        logger.info("Opening DB connection to %s", self.db_path)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        # Improve concurrency
        await self._conn.execute("PRAGMA journal_mode=WAL;")
        await self._conn.commit()
        self._connected = True

    async def close(self) -> None:
        """Close the long-lived connection on shutdown."""
        if self._conn:
            try:
                await self._conn.close()
            except Exception:
                logger.exception("Error closing DB connection")
            finally:
                self._conn = None
                self._connected = False

    def _ensure_conn(self) -> aiosqlite.Connection:
        if not self._connected or self._conn is None:
            raise RuntimeError("Database connection is not initialized. Call await db.connect() first.")
        return self._conn

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    async def init_db(self) -> None:
        """Create tables and indexes if missing. Requires connect() called first."""
        logger.info("Initializing DB schema at %s", self.db_path)
        conn = self._ensure_conn()
        try:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS products (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_id TEXT NOT NULL,
                    file_type TEXT NOT NULL,
                    caption TEXT,
                    message_id INTEGER,
                    chat_id INTEGER NOT NULL,
                    media_group_id TEXT,
                    category TEXT,
                    subcategory TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    file_unique_id TEXT,
                    UNIQUE(message_id, chat_id, file_id)
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS media_group_meta (
                    media_group_id TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    preview_sent_at TIMESTAMP,
                    PRIMARY KEY (media_group_id, chat_id)
                )
                """
            )
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ignored_messages (
                    message_id INTEGER NOT NULL,
                    chat_id INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (message_id, chat_id)
                )
                """
            )
            await conn.execute(
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
            await conn.execute(
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
            await conn.commit()

            # Ensure index on file_unique_id if present
            try:
                async with conn.execute("PRAGMA table_info(products);") as cur:
                    cols = await cur.fetchall()
                    col_names = {r[1] for r in cols}
                if "file_unique_id" in col_names:
                    await conn.execute(
                        "CREATE UNIQUE INDEX IF NOT EXISTS idx_products_file_unique_id ON products(file_unique_id)"
                    )
                    await conn.commit()
            except Exception:
                logger.exception("Failed to ensure indexes (non-fatal)")

            logger.info("DB initialized")
        except Exception:
            logger.exception("init_db failed")
            raise

    # -------------------------
    # Product CRUD and helpers
    # -------------------------
    async def add_product(
        self,
        file_id: str,
        file_type: str,
        caption: Optional[str],
        message_id: Optional[int],
        chat_id: int,
        media_group_id: Optional[str] = None,
        category: Optional[str] = None,
        subcategory: Optional[str] = None,
        file_unique_id: Optional[str] = None,
    ) -> int:
        """
        Insert a product row. Returns the product id on success, or an existing id if deduped,
        or -1 on failure.
        """
        try:
            conn = self._ensure_conn()
            mg = media_group_id.strip() if media_group_id and media_group_id.strip() else None
            msg_id_val = int(message_id) if message_id is not None else None
            cat = category.strip().upper() if category else None
            sub = subcategory.strip().upper() if subcategory else None
            created_at = self._now_iso()

            cursor = await conn.execute(
                """
                INSERT OR IGNORE INTO products
                    (file_id, file_type, caption, message_id, chat_id, media_group_id, category, subcategory, created_at, file_unique_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (file_id, file_type, caption or "", msg_id_val, int(chat_id), mg, cat, sub, created_at, file_unique_id),
            )
            await conn.commit()
            last_id = getattr(cursor, "lastrowid", None)
            new_id = int(last_id) if last_id else None

            if new_id is None:
                # dedupe lookups
                if file_unique_id:
                    async with conn.execute("SELECT id FROM products WHERE file_unique_id = ? AND chat_id = ? LIMIT 1", (file_unique_id, int(chat_id))) as cur:
                        row = await cur.fetchone()
                        if row:
                            return int(row["id"])
                if msg_id_val is not None:
                    async with conn.execute("SELECT id FROM products WHERE message_id = ? AND chat_id = ? AND file_id = ? LIMIT 1", (msg_id_val, int(chat_id), file_id)) as cur:
                        row = await cur.fetchone()
                        if row:
                            return int(row["id"])
                async with conn.execute("SELECT id FROM products WHERE file_id = ? AND chat_id = ? LIMIT 1", (file_id, int(chat_id))) as cur:
                    row = await cur.fetchone()
                    if row:
                        return int(row["id"])
                return -1

            # propagate media_group_id for related rows if provided
            if new_id and mg:
                try:
                    if msg_id_val is not None:
                        await conn.execute(
                            """
                            UPDATE products
                            SET media_group_id = ?
                            WHERE chat_id = ?
                              AND (media_group_id IS NULL OR TRIM(COALESCE(media_group_id, '')) = '')
                              AND message_id = ?
                            """,
                            (mg, int(chat_id), msg_id_val),
                        )
                    if file_unique_id:
                        await conn.execute(
                            """
                            UPDATE products
                            SET media_group_id = ?
                            WHERE chat_id = ?
                              AND (media_group_id IS NULL OR TRIM(COALESCE(media_group_id, '')) = '')
                              AND file_unique_id = ?
                            """,
                            (mg, int(chat_id), file_unique_id),
                        )
                    await conn.execute(
                        """
                        UPDATE products
                        SET media_group_id = ?
                        WHERE chat_id = ?
                          AND (media_group_id IS NULL OR TRIM(COALESCE(media_group_id, '')) = '')
                          AND file_id = ?
                        """,
                        (mg, int(chat_id), file_id),
                    )
                    await conn.commit()
                except Exception:
                    logger.exception("Failed to propagate media_group_id for %s/%s", mg, chat_id)

            return new_id
        except Exception:
            logger.exception("add_product failed")
            return -1

    async def get_product(self, product_id: int) -> Optional[Dict[str, Any]]:
        """Return a single product row as a dict or None."""
        try:
            conn = self._ensure_conn()
            async with conn.execute("SELECT * FROM products WHERE id = ? LIMIT 1", (int(product_id),)) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None
        except Exception:
            logger.exception("get_product failed for id=%s", product_id)
            return None

    async def get_products_by_media_group(self, media_group_id: str, chat_id: Optional[int]) -> List[Dict[str, Any]]:
        """Return list of product rows for a media_group and chat_id. If chat_id is None, match only media_group_id."""
        try:
            conn = self._ensure_conn()
            if chat_id is None:
                async with conn.execute("SELECT * FROM products WHERE media_group_id = ? ORDER BY id ASC", (media_group_id,)) as cur:
                    rows = await cur.fetchall()
            else:
                async with conn.execute("SELECT * FROM products WHERE media_group_id = ? AND chat_id = ? ORDER BY id ASC", (media_group_id, int(chat_id))) as cur:
                    rows = await cur.fetchall()
            return [dict(r) for r in rows] if rows else []
        except Exception:
            logger.exception("get_products_by_media_group failed for %s/%s", media_group_id, chat_id)
            return []

    async def update_products_by_media_group(self, media_group_id: str, chat_id: int, category: Optional[str], subcategory: Optional[str]) -> bool:
        """
        Atomically update category/subcategory for all products in a media_group.
        Returns True if rows were updated (or operation succeeded), False on failure.
        """
        try:
            conn = self._ensure_conn()
            cat = category.strip().upper() if category else None
            sub = subcategory.strip().upper() if subcategory else None
            async with conn.execute("BEGIN"):
                await conn.execute(
                    """
                    UPDATE products
                    SET category = ?, subcategory = ?
                    WHERE media_group_id = ? AND chat_id = ?
                    """,
                    (cat, sub, media_group_id, int(chat_id)),
                )
                await conn.commit()
            return True
        except Exception:
            logger.exception("update_products_by_media_group failed for %s/%s", media_group_id, chat_id)
            try:
                await conn.rollback()
            except Exception:
                pass
            return False

    async def set_product_category(self, product_id: int, category: Optional[str], subcategory: Optional[str]) -> bool:
        """Set category/subcategory for a single product row. Returns True on success."""
        try:
            conn = self._ensure_conn()
            cat = category.strip().upper() if category else None
            sub = subcategory.strip().upper() if subcategory else None
            await conn.execute(
                "UPDATE products SET category = ?, subcategory = ? WHERE id = ?",
                (cat, sub, int(product_id)),
            )
            await conn.commit()
            return True
        except Exception:
            logger.exception("set_product_category failed for id=%s", product_id)
            return False

    async def try_mark_media_group_preview_sent(self, media_group_id: str, chat_id: int) -> bool:
        """
        Insert a row into media_group_meta to mark that a preview was sent.
        Returns True if this call performed the insert (first caller), False if already present.
        """
        try:
            conn = self._ensure_conn()
            ts = self._now_iso()
            cursor = await conn.execute(
                """
                INSERT OR IGNORE INTO media_group_meta (media_group_id, chat_id, preview_sent_at)
                VALUES (?, ?, ?)
                """,
                (media_group_id, int(chat_id), ts),
            )
            await conn.commit()
            # If lastrowid is non-zero, we inserted; otherwise it already existed
            last_id = getattr(cursor, "lastrowid", None)
            if last_id:
                return True
            # As a fallback, check whether row exists
            async with conn.execute("SELECT preview_sent_at FROM media_group_meta WHERE media_group_id = ? AND chat_id = ? LIMIT 1", (media_group_id, int(chat_id))) as cur:
                row = await cur.fetchone()
                return bool(row)
        except Exception:
            logger.exception("try_mark_media_group_preview_sent failed for %s/%s", media_group_id, chat_id)
            return False

    async def delete_products_by_media_group(self, media_group_id: str, chat_id: int) -> List[int]:
        """Delete products for a media_group and return list of deleted ids."""
        try:
            conn = self._ensure_conn()
            async with conn.execute("SELECT id FROM products WHERE media_group_id = ? AND chat_id = ?", (media_group_id, int(chat_id))) as cur:
                rows = await cur.fetchall()
            ids = [int(r["id"]) for r in rows] if rows else []
            if not ids:
                return []
            # Delete rows
            await conn.execute("DELETE FROM products WHERE media_group_id = ? AND chat_id = ?", (media_group_id, int(chat_id)))
            await conn.commit()
            return ids
        except Exception:
            logger.exception("delete_products_by_media_group failed for %s/%s", media_group_id, chat_id)
            return []

    async def delete_product(self, product_id: int) -> bool:
        """Delete a single product by id. Returns True if deleted, False otherwise."""
        try:
            conn = self._ensure_conn()
            async with conn.execute("SELECT id FROM products WHERE id = ? LIMIT 1", (int(product_id),)) as cur:
                row = await cur.fetchone()
            if not row:
                return False
            await conn.execute("DELETE FROM products WHERE id = ?", (int(product_id),))
            await conn.commit()
            return True
        except Exception:
            logger.exception("delete_product failed for id=%s", product_id)
            return False

    async def get_all_products(self, limit: int = 500) -> List[Dict[str, Any]]:
        """Return recent products ordered by created_at desc (limitable)."""
        try:
            conn = self._ensure_conn()
            async with conn.execute("SELECT * FROM products ORDER BY created_at DESC LIMIT ?", (int(limit),)) as cur:
                rows = await cur.fetchall()
            return [dict(r) for r in rows] if rows else []
        except Exception:
            logger.exception("get_all_products failed")
            return []

    async def get_catalog_items(self, limit: int = 1000) -> List[Dict[str, Any]]:
        """
        Return catalog items used by the UI. By default returns recent items.
        Handlers can filter by category/subcategory as needed.
        """
        try:
            conn = self._ensure_conn()
            async with conn.execute("SELECT * FROM products ORDER BY created_at DESC LIMIT ?", (int(limit),)) as cur:
                rows = await cur.fetchall()
            return [dict(r) for r in rows] if rows else []
        except Exception:
            logger.exception("get_catalog_items failed")
            return []

    async def get_all_users(self, limit: int = 1000) -> List[Dict[str, Any]]:
        try:
            conn = self._ensure_conn()
            async with conn.execute("SELECT * FROM users ORDER BY last_seen DESC LIMIT ?", (int(limit),)) as cur:
                rows = await cur.fetchall()
            return [dict(r) for r in rows] if rows else []
        except Exception:
            logger.exception("get_all_users failed")
            return []

    async def get_stats(self) -> Dict[str, int]:
        """Return simple stats: total products and products added today (UTC)."""
        try:
            conn = self._ensure_conn()
            async with conn.execute("SELECT COUNT(1) as total FROM products") as cur:
                row = await cur.fetchone()
            total = int(row["total"]) if row else 0

            # Count items added since UTC midnight
            today_utc = datetime.now(timezone.utc).date().isoformat()
            async with conn.execute("SELECT COUNT(1) as today FROM products WHERE DATE(created_at) = DATE(?)", (today_utc,)) as cur:
                row2 = await cur.fetchone()
            today = int(row2["today"]) if row2 else 0
            return {"total": total, "today": today}
        except Exception:
            logger.exception("get_stats failed")
            return {"total": 0, "today": 0}

    # -------------------------
    # Backfill / maintenance helpers
    # -------------------------
    async def backfill_normalize(self) -> int:
        """
        Normalize category/subcategory storage (uppercase, remove spaces/underscores for subcategory).
        Returns number of rows changed.
        """
        try:
            conn = self._ensure_conn()
            await conn.execute(
                """
                UPDATE products
                SET category = UPPER(TRIM(category)),
                    subcategory = CASE
                        WHEN subcategory IS NULL OR TRIM(subcategory) = '' THEN NULL
                        ELSE UPPER(REPLACE(REPLACE(TRIM(subcategory), ' ', ''), '_', ''))
                    END
                """
            )
            await conn.commit()
            # aiosqlite does not expose total_changes reliably per-statement; query count instead
            async with conn.execute("SELECT COUNT(1) as cnt FROM products WHERE category IS NOT NULL OR subcategory IS NOT NULL") as cur:
                row = await cur.fetchone()
            return int(row["cnt"]) if row else 0
        except Exception:
            logger.exception("backfill_normalize failed")
            return 0

    async def backfill_propagate_media_group(self) -> int:
        """
        Propagate category/subcategory to products in the same media_group where missing.
        Returns number of rows updated (approximate).
        """
        try:
            conn = self._ensure_conn()
            await conn.execute(
                """
                UPDATE products
                SET category = COALESCE(category, (
                    SELECT category FROM products p2
                    WHERE p2.media_group_id = products.media_group_id AND p2.category IS NOT NULL LIMIT 1
                )),
                    subcategory = COALESCE(subcategory, (
                    SELECT subcategory FROM products p3
                    WHERE p3.media_group_id = products.media_group_id AND p3.subcategory IS NOT NULL LIMIT 1
                ))
                WHERE media_group_id IS NOT NULL
                """
            )
            await conn.commit()
            # Return approximate count of rows with non-null category/subcategory
            async with conn.execute("SELECT COUNT(1) as cnt FROM products WHERE media_group_id IS NOT NULL AND (category IS NOT NULL OR subcategory IS NOT NULL)") as cur:
                row = await cur.fetchone()
            return int(row["cnt"]) if row else 0
        except Exception:
            logger.exception("backfill_propagate_media_group failed")
            return 0

    async def nuke_all_products(self) -> int:
        """Delete all products. Returns number of rows deleted."""
        try:
            conn = self._ensure_conn()
            async with conn.execute("SELECT COUNT(1) as cnt FROM products") as cur:
                row = await cur.fetchone()
            total = int(row["cnt"]) if row else 0
            await conn.execute("DELETE FROM products")
            await conn.execute("DELETE FROM media_group_meta")
            await conn.commit()
            return total
        except Exception:
            logger.exception("nuke_all_products failed")
            return 0


# Module-level DB instance and init helper
db = Database(DB_PATH)


async def init_db() -> None:
    # Ensure connection is open and schema initialized
    await db.connect()
    await db.init_db()