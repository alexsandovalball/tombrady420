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
async def test_delete_product_preserves_public_redirect_metadata(db):
    await db.init_db()

    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute(
            """
            INSERT INTO products (
                id, file_id, file_type, caption, message_id, chat_id,
                category, subcategory
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (977, "file", "photo", "redirect me", 123, -100, "FLOWER", "EXOTICS"),
        )
        await conn.commit()

    assert await db.delete_product(977) is True

    redirect = await db.get_deleted_product_redirect(977)
    assert redirect is not None
    assert redirect["caption"] == "redirect me"
    assert redirect["category"] == "FLOWER"
    assert redirect["subcategory"] == "EXOTICS"


@pytest.mark.asyncio
async def test_get_related_products_excludes_current_and_prefers_same_subcategory(db):
    await db.init_db()

    async with aiosqlite.connect(db.db_path) as conn:
        rows = [
            (10, "current", "photo", "Current", 10, -100, "FLOWER", "EXOTICS", "2026-06-08 12:00:00"),
            (11, "same", "photo", "Same Subcategory", 11, -100, " flower ", "exotics", "2026-06-08 12:01:00"),
            (12, "other", "photo", "Other Flower", 12, -100, "FLOWER", "LIGHTDEPS", "2026-06-08 12:02:00"),
            (13, "wrong", "photo", "Wrong Category", 13, -100, "VAPES", "EXOTICS", "2026-06-08 12:03:00"),
        ]
        await conn.executemany(
            """
            INSERT INTO products (
                id, file_id, file_type, caption, message_id, chat_id,
                category, subcategory, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await conn.commit()

    related = await db.get_related_products(10, "FLOWER", subcategory="EXOTICS", limit=6)

    assert [item["id"] for item in related] == [11, 12]


@pytest.mark.asyncio
async def test_seo_query_mappings_and_search_events_are_persisted(db):
    await db.init_db()

    imported = await db.upsert_seo_query_mappings(
        [{"term": "hash rosin", "category": "CONCENTRATES", "subcategory": "SOLVENTLESS"}],
        source="test",
        approved=True,
    )
    assert imported == 1

    mappings = await db.get_approved_seo_query_mappings()
    assert mappings[0]["term"] == "hash rosin"
    assert mappings[0]["category"] == "CONCENTRATES"

    await db.log_search_query(query="rare query", expanded_query="rare query", matched_count=0)
    unmapped = await db.get_unmapped_search_queries()
    assert unmapped[0]["query"] == "rare query"


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
async def test_add_product_does_not_reuse_deleted_product_ids(db):
    await db.init_db()

    first_id = await db.add_product(
        file_id="file-a",
        file_type="photo",
        caption="first",
        message_id=1,
        chat_id=-100,
    )
    second_id = await db.add_product(
        file_id="file-b",
        file_type="photo",
        caption="second",
        message_id=2,
        chat_id=-100,
    )

    assert await db.delete_product(first_id) is True

    third_id = await db.add_product(
        file_id="file-c",
        file_type="photo",
        caption="third",
        message_id=3,
        chat_id=-100,
    )

    assert third_id > second_id
    assert third_id != first_id


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
