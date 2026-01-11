"""
Async database access and schema management using aiosqlite.
Provides functions for CRUD operations, deduplication, and migrations.
"""

import aiosqlite
import asyncio
from typing import Any, Dict, List, Optional
from config.config import DB_PATH

DB_SCHEMA_VERSION = 1

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER UNIQUE NOT NULL,
    is_admin INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS media_group_meta (
    media_group_id TEXT PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    status TEXT DEFAULT 'pending',
    category TEXT,
    subcategory TEXT,
    previewed INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    media_group_id TEXT NOT NULL,
    file_unique_id TEXT NOT NULL,
    file_id TEXT NOT NULL,
    media_type TEXT NOT NULL,
    caption TEXT,
    category TEXT,
    subcategory TEXT,
    is_deleted INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(media_group_id) REFERENCES media_group_meta(media_group_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_products_file_unique_id ON products(file_unique_id);

CREATE TABLE IF NOT EXISTS ignored_messages (
    message_id INTEGER NOT NULL,
    chat_id INTEGER NOT NULL,
    reason TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (message_id, chat_id)
);

CREATE TABLE IF NOT EXISTS pagination_state (
    user_id INTEGER PRIMARY KEY,
    state_json TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);
"""

async def init_db():
    """
    Initialize the database, create tables, and set schema version.
    """
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript(SCHEMA)
        # Set schema version if not present
        async with db.execute("SELECT version FROM schema_version") as cursor:
            row = await cursor.fetchone()
            if not row:
                await db.execute("INSERT INTO schema_version (version) VALUES (?)", (DB_SCHEMA_VERSION,))
        await db.commit()

async def get_db():
    """
    Context manager for database connection.
    """
    db = await aiosqlite.connect(DB_PATH)
    db.row_factory = aiosqlite.Row
    return db

# Example CRUD and deduplication functions

async def add_user(telegram_id: int, is_admin: bool = False):
    async with await get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (telegram_id, is_admin) VALUES (?, ?)",
            (telegram_id, int(is_admin))
        )
        await db.commit()

async def is_admin(telegram_id: int) -> bool:
    async with await get_db() as db:
        async with db.execute(
            "SELECT is_admin FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row and row["is_admin"])

async def add_media_group_meta(media_group_id: str, chat_id: int):
    async with await get_db() as db:
        await db.execute(
            "INSERT OR IGNORE INTO media_group_meta (media_group_id, chat_id) VALUES (?, ?)",
            (media_group_id, chat_id)
        )
        await db.commit()

async def mark_media_group_previewed(media_group_id: str):
    async with await get_db() as db:
        await db.execute(
            "UPDATE media_group_meta SET previewed = 1 WHERE media_group_id = ?",
            (media_group_id,)
        )
        await db.commit()

async def add_product(
    media_group_id: str,
    file_unique_id: str,
    file_id: str,
    media_type: str,
    caption: Optional[str],
    category: str,
    subcategory: str
):
    async with await get_db() as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO products
            (media_group_id, file_unique_id, file_id, media_type, caption, category, subcategory)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (media_group_id, file_unique_id, file_id, media_type, caption, category, subcategory)
        )
        await db.commit()

async def product_exists(file_unique_id: str) -> bool:
    async with await get_db() as db:
        async with db.execute(
            "SELECT 1 FROM products WHERE file_unique_id = ? AND is_deleted = 0",
            (file_unique_id,)
        ) as cursor:
            return await cursor.fetchone() is not None

async def get_categories() -> List[str]:
    async with await get_db() as db:
        async with db.execute(
            "SELECT DISTINCT category FROM products WHERE is_deleted = 0"
        ) as cursor:
            return [row["category"] for row in await cursor.fetchall() if row["category"]]

# ... Additional functions for CRUD, deduplication, pagination, and cleanup