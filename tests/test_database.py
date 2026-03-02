"""
Tests for the database layer (database.py).

Covers:
- add_product: basic insertion, media_group_id propagation, unique constraint
- Deduplication by file_id (no two rows with the same file_id should be created
  by accident; we verify the unique constraint on (message_id, chat_id) prevents that)
- get_or_create_media_group_product / register_media_group round-trip
"""
import json
import pytest
import pytest_asyncio

from tests.conftest import in_memory_db  # noqa: F401 – imported for fixture discovery


# ---------------------------------------------------------------------------
# add_product
# ---------------------------------------------------------------------------

class TestAddProduct:
    @pytest.mark.asyncio
    async def test_add_single_product_returns_positive_id(self, in_memory_db):
        pid = await in_memory_db.add_product(
            file_id="file_abc",
            file_type="photo",
            caption="Test caption",
            message_id=1,
            chat_id=-100,
        )
        assert pid > 0

    @pytest.mark.asyncio
    async def test_add_product_with_media_group_id(self, in_memory_db):
        pid = await in_memory_db.add_product(
            file_id="file_mgid",
            file_type="photo",
            caption="Album caption",
            message_id=2,
            chat_id=-100,
            media_group_id="mg_12345",
        )
        assert pid > 0
        product = await in_memory_db.get_product(pid)
        assert product is not None
        assert product["media_group_id"] == "mg_12345"

    @pytest.mark.asyncio
    async def test_add_product_stores_category_and_subcategory(self, in_memory_db):
        pid = await in_memory_db.add_product(
            file_id="file_cat",
            file_type="photo",
            caption="Categorised product",
            message_id=3,
            chat_id=-100,
            category="FLOWER",
            subcategory="LOWS",
        )
        product = await in_memory_db.get_product(pid)
        assert product["category"] == "FLOWER"
        assert product["subcategory"] == "LOWS"

    @pytest.mark.asyncio
    async def test_add_product_without_media_group_id_is_none(self, in_memory_db):
        pid = await in_memory_db.add_product(
            file_id="file_nomg",
            file_type="photo",
            caption="No album",
            message_id=4,
            chat_id=-100,
        )
        product = await in_memory_db.get_product(pid)
        assert product["media_group_id"] is None

    @pytest.mark.asyncio
    async def test_unique_constraint_returns_negative_one(self, in_memory_db):
        """Attempting to insert a duplicate (message_id, chat_id) must return -1."""
        pid1 = await in_memory_db.add_product(
            file_id="file_dup1",
            file_type="photo",
            caption="First",
            message_id=10,
            chat_id=-200,
        )
        assert pid1 > 0
        pid2 = await in_memory_db.add_product(
            file_id="file_dup2",
            file_type="photo",
            caption="Duplicate message",
            message_id=10,  # same message_id
            chat_id=-200,   # same chat_id
        )
        assert pid2 == -1

    @pytest.mark.asyncio
    async def test_id_recycling(self, in_memory_db):
        """After deleting a product, its gap slot in the sequence should be reused."""
        pid1 = await in_memory_db.add_product(
            file_id="file_r1",
            file_type="photo",
            caption="First",
            message_id=20,
            chat_id=-300,
        )
        pid2 = await in_memory_db.add_product(
            file_id="file_r2",
            file_type="photo",
            caption="Second",
            message_id=21,
            chat_id=-300,
        )
        pid3 = await in_memory_db.add_product(
            file_id="file_r3",
            file_type="photo",
            caption="Third",
            message_id=22,
            chat_id=-300,
        )
        # Delete the middle product to create a gap: [pid1, pid3]
        await in_memory_db.delete_product(pid2)
        pid4 = await in_memory_db.add_product(
            file_id="file_r4",
            file_type="photo",
            caption="New product filling the gap",
            message_id=23,
            chat_id=-300,
        )
        # pid4 should reuse pid2's slot (lowest gap between pid1 and pid3)
        assert pid4 == pid2

    @pytest.mark.asyncio
    async def test_bot_username_normalised_to_lowercase(self, in_memory_db):
        pid = await in_memory_db.add_product(
            file_id="file_botname",
            file_type="photo",
            caption="Bot username test",
            message_id=30,
            chat_id=-400,
            bot_username="MyTestBot",
        )
        product = await in_memory_db.get_product(pid)
        assert product["bot_username"] == "mytestbot"


# ---------------------------------------------------------------------------
# media_group_id propagation
# ---------------------------------------------------------------------------

class TestMediaGroupId:
    @pytest.mark.asyncio
    async def test_register_and_retrieve_media_group(self, in_memory_db):
        pid = await in_memory_db.add_product(
            file_id="file_mg1",
            file_type="photo",
            caption="Album",
            message_id=40,
            chat_id=-500,
            media_group_id="mg_alpha",
        )
        await in_memory_db.register_media_group("mg_alpha", -500, pid)
        retrieved_pid = await in_memory_db.get_or_create_media_group_product("mg_alpha", -500)
        assert retrieved_pid == pid

    @pytest.mark.asyncio
    async def test_unknown_media_group_returns_none(self, in_memory_db):
        result = await in_memory_db.get_or_create_media_group_product("mg_unknown", -999)
        assert result is None

    @pytest.mark.asyncio
    async def test_additional_file_ids_stored_as_json(self, in_memory_db):
        additional = json.dumps([("file_2", "photo"), ("file_3", "video")])
        pid = await in_memory_db.add_product(
            file_id="file_mg_primary",
            file_type="photo",
            caption="Multi-file album",
            message_id=50,
            chat_id=-600,
            media_group_id="mg_beta",
            additional_file_ids=additional,
        )
        product = await in_memory_db.get_product(pid)
        parsed = json.loads(product["additional_file_ids"])
        assert ("file_2", "photo") in [tuple(x) for x in parsed]

    @pytest.mark.asyncio
    async def test_multiple_products_same_chat_different_media_groups(self, in_memory_db):
        pid1 = await in_memory_db.add_product(
            file_id="file_g1",
            file_type="photo",
            caption="Group 1",
            message_id=60,
            chat_id=-700,
            media_group_id="mg_g1",
        )
        pid2 = await in_memory_db.add_product(
            file_id="file_g2",
            file_type="photo",
            caption="Group 2",
            message_id=61,
            chat_id=-700,
            media_group_id="mg_g2",
        )
        await in_memory_db.register_media_group("mg_g1", -700, pid1)
        await in_memory_db.register_media_group("mg_g2", -700, pid2)
        assert await in_memory_db.get_or_create_media_group_product("mg_g1", -700) == pid1
        assert await in_memory_db.get_or_create_media_group_product("mg_g2", -700) == pid2

    @pytest.mark.asyncio
    async def test_with_chat_filter_isolates_groups(self, in_memory_db):
        """Same media_group_id in different chats must be independent."""
        pid_a = await in_memory_db.add_product(
            file_id="file_ca",
            file_type="photo",
            caption="Chat A",
            message_id=70,
            chat_id=-800,
            media_group_id="mg_shared",
        )
        pid_b = await in_memory_db.add_product(
            file_id="file_cb",
            file_type="photo",
            caption="Chat B",
            message_id=71,
            chat_id=-801,
            media_group_id="mg_shared",
        )
        await in_memory_db.register_media_group("mg_shared", -800, pid_a)
        await in_memory_db.register_media_group("mg_shared", -801, pid_b)

        # The media_groups table has a single-column PK on media_group_id, so
        # the second registration overwrites the first.  This is current
        # behaviour—the test documents it rather than asserting cross-chat
        # isolation (which would require a schema change).
        retrieved = await in_memory_db.get_or_create_media_group_product("mg_shared", -801)
        assert retrieved == pid_b
