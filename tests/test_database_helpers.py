import asyncio
from datetime import datetime, timedelta

import aiosqlite
import pytest

from database import Database


def test_normalize_categories_filters_and_uppercases():
    categories = [" datedproofs ", "ANNOUNCEMENTS", None, "  ", 123, "clientTouchdowns"]
    normalized = Database._normalize_categories(categories)
    assert normalized == ["DATEDPROOFS", "ANNOUNCEMENTS", "CLIENTTOUCHDOWNS"]


def test_count_and_fetch_helpers_return_all_products(tmp_path):
    async def _run():
        db_path = tmp_path / "helpers.db"
        database = Database(str(db_path))
        await database.init_db()

        first_created = datetime.now() - timedelta(days=1)
        second_created = datetime.now()

        async with aiosqlite.connect(str(db_path)) as conn:
            await conn.execute(
                """
                INSERT INTO products (file_id, file_type, caption, message_id, chat_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("file1", "photo", "first", 1, 100, first_created),
            )
            await conn.execute(
                """
                INSERT INTO products (file_id, file_type, caption, message_id, chat_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("file2", "photo", "second", 2, 100, second_created),
            )
            await conn.commit()

            count = await Database._count_all_products(conn)

        async with aiosqlite.connect(str(db_path)) as conn:
            conn.row_factory = aiosqlite.Row
            products = await Database._get_all_products_ordered(conn)

        return count, [p["caption"] for p in products]

    count, captions = asyncio.run(_run())

    assert count == 2
    assert captions == ["second", "first"]
