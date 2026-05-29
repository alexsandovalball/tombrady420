from datetime import datetime, timedelta

import aiosqlite
import pytest

from database import Database


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "products.db"))


@pytest.mark.asyncio
async def test_delete_product_removes_dependent_rows(db):
    await db.init_db()

    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute(
            """
            INSERT INTO products (id, file_id, file_type, caption, message_id, chat_id, category)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (976, "file", "photo", "caption", 123, -100, "FLOWER"),
        )
        await conn.execute(
            "INSERT INTO media_groups (media_group_id, chat_id, product_id) VALUES (?, ?, ?)",
            ("group-a", -100, 976),
        )
        await conn.execute(
            "INSERT INTO media_groups (media_group_id, chat_id, product_id) VALUES (?, ?, ?)",
            ("group-b", -100, 976),
        )
        await conn.execute(
            "INSERT INTO pending_categorization (product_id) VALUES (?)",
            (976,),
        )
        await conn.execute(
            "INSERT INTO notification_queue (user_id, product_id) VALUES (?, ?)",
            (1, 976),
        )
        await conn.execute(
            "INSERT INTO notification_queue (user_id, product_id) VALUES (?, ?)",
            (2, 976),
        )
        await conn.commit()

    assert await db.delete_product(976) is True

    async with aiosqlite.connect(db.db_path) as conn:
        for table in ("products", "media_groups", "pending_categorization", "notification_queue"):
            count = (await (await conn.execute(f"SELECT count(*) FROM {table} WHERE product_id = 976" if table != "products" else "SELECT count(*) FROM products WHERE id = 976")).fetchone())[0]
            assert count == 0


@pytest.mark.asyncio
async def test_delete_missing_product_leaves_related_rows(db):
    await db.init_db()

    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute(
            "INSERT INTO notification_queue (user_id, product_id) VALUES (?, ?)",
            (1, 999),
        )
        await conn.commit()

    assert await db.delete_product(123) is False

    async with aiosqlite.connect(db.db_path) as conn:
        count = (await (await conn.execute("SELECT count(*) FROM notification_queue WHERE product_id = 999")).fetchone())[0]
        assert count == 1


def test_cache_touch_throttles_recent_hits():
    assert Database._should_touch_cache_entry(datetime.now().isoformat()) is False
    assert Database._should_touch_cache_entry((datetime.now() - timedelta(hours=2)).isoformat()) is True
    assert Database._should_touch_cache_entry(None) is True


def test_media_registry_write_cache_deduplicates_repeated_range_requests():
    import webhook_server

    webhook_server._media_registry_write_cache.clear()
    assert webhook_server._has_media_registry_write("tombrady007bot", "unique-file", "file-id") is False

    webhook_server._remember_media_registry_write("tombrady007bot", "unique-file", "file-id")

    assert webhook_server._has_media_registry_write("tombrady007bot", "unique-file", "file-id") is True
    assert webhook_server._has_media_registry_write("tombrady007bot", "unique-file", "other-file-id") is False

    webhook_server._media_registry_write_cache.clear()


@pytest.mark.asyncio
async def test_get_products_page_matches_existing_filters(db):
    await db.init_db()

    rows = [
        (1, "a", "photo", "first", 1, -100, "FLOWER", "EXOTICS", "2026-01-01 00:00:00"),
        (2, "b", "photo", "second", 2, -100, "FLOWER", "INDOOR", "2026-01-02 00:00:00"),
        (3, "c", "photo", "hidden", 3, -100, "DATEDPROOFS", None, "2026-01-03 00:00:00"),
    ]
    async with aiosqlite.connect(db.db_path) as conn:
        for row in rows:
            await conn.execute(
                """
                INSERT INTO products (id, file_id, file_type, caption, message_id, chat_id, category, subcategory, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
        await conn.commit()

    products, total = await db.get_products_page(
        excluded_categories=["DATEDPROOFS"],
        limit=10,
        offset=0,
    )
    assert total == 2
    assert [product["id"] for product in products] == [2, 1]

    products, total = await db.get_products_page(
        category="FLOWER",
        subcategory="EXOTICS",
        excluded_categories=["DATEDPROOFS"],
        limit=10,
        offset=0,
    )
    assert total == 1
    assert products[0]["id"] == 1


@pytest.mark.asyncio
async def test_get_category_summary_matches_existing_counts(db):
    await db.init_db()

    rows = [
        (1, "a", "photo", "first", 1, -100, "FLOWER", "EXOTICS"),
        (2, "b", "photo", "second", 2, -100, "FLOWER", "EXOTICS"),
        (3, "c", "photo", "third", 3, -100, "FLOWER", "INDOOR"),
        (4, "d", "photo", "hidden", 4, -100, "DATEDPROOFS", None),
    ]
    async with aiosqlite.connect(db.db_path) as conn:
        for row in rows:
            await conn.execute(
                """
                INSERT INTO products (id, file_id, file_type, caption, message_id, chat_id, category, subcategory)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
        await conn.commit()

    category_counts, subcategory_counts, all_products_count = await db.get_category_summary(["DATEDPROOFS"])

    assert category_counts["FLOWER"] == 3
    assert category_counts["DATEDPROOFS"] == 1
    assert all_products_count == 3
    flower_counts = {item["subcategory"]: item["count"] for item in subcategory_counts["FLOWER"]}
    assert flower_counts == {"EXOTICS": 2, "INDOOR": 1}


@pytest.mark.asyncio
async def test_get_subcategory_summary_uses_one_category_scope(db):
    await db.init_db()

    rows = [
        (1, "a", "photo", "first", 1, -100, "FLOWER", "EXOTICS"),
        (2, "b", "photo", "second", 2, -100, "FLOWER", "EXOTICS"),
        (3, "c", "photo", "third", 3, -100, "FLOWER", "INDOOR"),
        (4, "d", "photo", "other", 4, -100, "CONCENTRATES", "DIAMONDS AND SAUCE"),
    ]
    async with aiosqlite.connect(db.db_path) as conn:
        for row in rows:
            await conn.execute(
                """
                INSERT INTO products (id, file_id, file_type, caption, message_id, chat_id, category, subcategory)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                row,
            )
        await conn.commit()

    subcategory_counts, total = await db.get_subcategory_summary("FLOWER")

    assert total == 3
    assert {item["subcategory"]: item["count"] for item in subcategory_counts} == {
        "EXOTICS": 2,
        "INDOOR": 1,
    }
