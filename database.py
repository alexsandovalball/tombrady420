# database.py
"""
Async SQLite wrapper and DB operations for the media catalog.
Provides schema creation, migrations, and helper methods used by handlers.
"""

from __future__ import annotations

import aiosqlite
import os
import logging
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple

from config import DB_PATH

logger = logging.getLogger(__name__)

# Ensure DB directory exists
_db_dir = os.path.dirname(DB_PATH) or "."
os.makedirs(_db_dir, exist_ok=True)


class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    async def init_db(self) -> None:
        """Create tables and indexes if missing."""
        logger.info("Initializing DB at %s", self.db_path)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL;")
            await db.execute(
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
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS media_group_meta (
                    media_group_id TEXT NOT NULL,
                    chat_id INTEGER NOT NULL,
                    preview_sent_at TIMESTAMP,
                    PRIMARY KEY (media_group_id, chat_id)
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
            await db.commit()
            # ensure file_unique_id index if column exists
            try:
                async with db.execute("PRAGMA table_info(products);") as cur:
                    cols = await cur.fetchall()
                    col_names = {r[1] for r in cols}
                if "file_unique_id" in col_names:
                    await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_products_file_unique_id ON products(file_unique_id)")
                    await db.commit()
            except Exception:
                logger.exception("Failed to ensure indexes (non-fatal)")
            logger.info("DB initialized")

    # -------------------------
    # Product ingestion and dedupe
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
        Insert product row with deduplication. Returns product id or -1 on failure.
        Normalizes media_group_id (empty -> NULL) and propagates it to other rows when possible.
        """
        try:
            mg = media_group_id.strip() if media_group_id and media_group_id.strip() else None
            msg_id_val = int(message_id) if message_id is not None else None
            cat = category.strip().upper() if category else None
            sub = subcategory.strip().upper() if subcategory else None
            created_at = datetime.utcnow().isoformat()

            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    """
                    INSERT OR IGNORE INTO products
                        (file_id, file_type, caption, message_id, chat_id, media_group_id, category, subcategory, created_at, file_unique_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (file_id, file_type, caption or "", msg_id_val, int(chat_id), mg, cat, sub, created_at, file_unique_id),
                )
                await db.commit()
                last_id = getattr(cursor, "lastrowid", None)
                if last_id:
                    new_id = int(last_id)
                else:
                    new_id = None

                # If insert ignored, try to find existing row by file_unique_id, message_id+file_id, or file_id
                if new_id is None:
                    if file_unique_id:
                        async with db.execute("SELECT id FROM products WHERE file_unique_id = ? AND chat_id = ? LIMIT 1", (file_unique_id, int(chat_id))) as cur:
                            row = await cur.fetchone()
                            if row:
                                return int(row["id"])
                    if msg_id_val is not None:
                        async with db.execute("SELECT id FROM products WHERE message_id = ? AND chat_id = ? AND file_id = ? LIMIT 1", (msg_id_val, int(chat_id), file_id)) as cur:
                            row = await cur.fetchone()
                            if row:
                                return int(row["id"])
                    async with db.execute("SELECT id FROM products WHERE file_id = ? AND chat_id = ? LIMIT 1", (file_id, int(chat_id))) as cur:
                        row = await cur.fetchone()
                        if row:
                            return int(row["id"])
                    return -1

                # Propagate media_group_id to other rows that missed it
                if new_id and mg:
                    try:
                        if msg_id_val is not None:
                            await db.execute(
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
                            await db.execute(
                                """
                                UPDATE products
                                SET media_group_id = ?
                                WHERE chat_id = ?
                                  AND (media_group_id IS NULL OR TRIM(COALESCE(media_group_id, '')) = '')
                                  AND file_unique_id = ?
                                """,
                                (mg, int(chat_id), file_unique_id),
                            )
                        await db.execute(
                            """
                            UPDATE products
                            SET media_group_id = ?
                            WHERE chat_id = ?
                              AND (media_group_id IS NULL OR TRIM(COALESCE(media_group_id, '')) = '')
                              AND file_id = ?
                            """,
                            (mg, int(chat_id), file_id),
                        )
                        await db.commit()
                    except Exception:
                        logger.exception("Failed to propagate media_group_id for %s/%s", mg, chat_id)

                return new_id
        except Exception:
            logger.exception("add_product failed")
            return -1

    # -------------------------
    # Catalog query (representative selection)
    # -------------------------
    async def get_catalog_items(self, limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, Any]]:
        """
        Return catalog items for menu display.
        For media groups, return one representative row per media_group_id preferring captioned rows.
        Standalone items (no media_group_id) are returned as-is.
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                base_query = """
                WITH rep AS (
                  SELECT * FROM (
                    SELECT *,
                      ROW_NUMBER() OVER (
                        PARTITION BY media_group_id, chat_id
                        ORDER BY CASE WHEN TRIM(COALESCE(caption,'')) <> '' THEN 0 ELSE 1 END, id
                      ) AS rn
                    FROM products
                    WHERE media_group_id IS NOT NULL AND TRIM(COALESCE(media_group_id,'')) <> ''
                  ) WHERE rn = 1
                ),
                standalone AS (
                  SELECT * FROM products WHERE media_group_id IS NULL OR TRIM(COALESCE(media_group_id,'')) = ''
                )
                SELECT * FROM (
                  SELECT * FROM standalone
                  UNION ALL
                  SELECT * FROM rep
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
            logger.exception("get_catalog_items failed")
            return []

    # -------------------------
    # Media group preview guard
    # -------------------------
    async def try_mark_media_group_preview_sent(self, media_group_id: str, chat_id: int, ts: Optional[Any] = None) -> bool:
        """
        Atomically insert a row into media_group_meta to mark preview sent.
        Returns True if this call inserted the row (i.e., caller should send preview),
        False if a row already existed.
        """
        try:
            if ts is None:
                ts_iso = datetime.utcnow().isoformat()
            else:
                ts_iso = ts if isinstance(ts, str) else (datetime.utcfromtimestamp(float(ts)).isoformat() if isinstance(ts, (int, float)) else ts.isoformat())
            async with aiosqlite.connect(self.db_path) as db:
                # Try to insert; if conflict, do nothing and check whether we inserted
                await db.execute(
                    """
                    INSERT OR IGNORE INTO media_group_meta (media_group_id, chat_id, preview_sent_at)
                    VALUES (?, ?, ?)
                    """,
                    (media_group_id, int(chat_id), ts_iso),
                )
                await db.commit()
                # Check whether row exists and preview_sent_at equals ts_iso (means we inserted)
                async with db.execute("SELECT preview_sent_at FROM media_group_meta WHERE media_group_id = ? AND chat_id = ? LIMIT 1", (media_group_id, int(chat_id))) as cur:
                    row = await cur.fetchone()
                    if row and row["preview_sent_at"] == ts_iso:
                        return True
                    return False
        except Exception:
            logger.exception("try_mark_media_group_preview_sent failed for %s/%s", media_group_id, chat_id)
            return False

    async def mark_media_group_preview_sent(self, media_group_id: str, chat_id: int, ts: Optional[Any] = None) -> None:
        """Non-atomic mark (update or insert)."""
        try:
            if ts is None:
                ts_iso = datetime.utcnow().isoformat()
            else:
                ts_iso = ts if isinstance(ts, str) else (datetime.utcfromtimestamp(float(ts)).isoformat() if isinstance(ts, (int, float)) else ts.isoformat())
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(
                    """
                    INSERT INTO media_group_meta (media_group_id, chat_id, preview_sent_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(media_group_id, chat_id) DO UPDATE SET preview_sent_at = excluded.preview_sent_at
                    """,
                    (media_group_id, int(chat_id), ts_iso),
                )
                await db.commit()
        except Exception:
            logger.exception("mark_media_group_preview_sent failed for %s/%s", media_group_id, chat_id)

    # -------------------------
    # Utility methods
    # -------------------------
    async def get_products_by_media_group(self, media_group_id: str, chat_id: int) -> List[Dict[str, Any]]:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT * FROM products WHERE media_group_id = ? AND chat_id = ? ORDER BY id ASC", (media_group_id, int(chat_id))) as cur:
                    rows = await cur.fetchall()
                    return [dict(r) for r in rows]
        except Exception:
            logger.exception("get_products_by_media_group failed for %s/%s", media_group_id, chat_id)
            return []

    async def update_products_by_media_group(self, media_group_id: str, chat_id: int, category: Optional[str], subcategory: Optional[str] = None) -> bool:
        try:
            cat = category.strip().upper() if category else None
            sub = subcategory.strip().upper() if subcategory else None
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("UPDATE products SET category = ?, subcategory = ? WHERE media_group_id = ? AND chat_id = ?", (cat, sub, media_group_id, int(chat_id)))
                await db.commit()
            return True
        except Exception:
            logger.exception("update_products_by_media_group failed for %s/%s", media_group_id, chat_id)
            return False

    async def delete_products_by_media_group(self, media_group_id: str, chat_id: int) -> List[int]:
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute("SELECT id FROM products WHERE media_group_id = ? AND chat_id = ?", (media_group_id, int(chat_id))) as cur:
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


# Module-level DB instance and init helper
db = Database(DB_PATH)


async def init_db() -> None:
    await db.init_db()