# database.py
from __future__ import annotations

import aiosqlite
import os
import logging
from datetime import datetime
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
        # Set WAL for concurrency
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
    def _normalize_ts(ts: Optional[Any]) -> str:
        if ts is None:
            return datetime.utcnow().isoformat()
        if isinstance(ts, str):
            return ts
        if isinstance(ts, (int, float)):
            return datetime.utcfromtimestamp(float(ts)).isoformat()
        try:
            return ts.isoformat()
        except Exception:
            return datetime.utcnow().isoformat()

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

            # ensure file_unique_id index if column exists
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

    # Example of using the shared connection in methods:
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
        try:
            conn = self._ensure_conn()
            mg = media_group_id.strip() if media_group_id and media_group_id.strip() else None
            msg_id_val = int(message_id) if message_id is not None else None
            cat = category.strip().upper() if category else None
            sub = subcategory.strip().upper() if subcategory else None
            created_at = datetime.utcnow().isoformat()

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

            # propagate media_group_id
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

    # Implement other methods similarly using self._ensure_conn() and the shared connection.
    # For brevity, keep the rest of your methods but replace per-call connects with self._ensure_conn().

# Module-level DB instance and init helper
db = Database(DB_PATH)


async def init_db() -> None:
    # Ensure connection is open and schema initialized
    await db.connect()
    await db.init_db()