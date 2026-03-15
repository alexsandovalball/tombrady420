"""
Tests for broadcast_chats DB CRUD methods and enable/disable logic.

Uses an in-memory SQLite database to exercise the real Database methods without
disk I/O or external dependencies.
"""
import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    """Return a Database instance backed by a temporary file."""
    from database import Database
    db_file = tmp_path / "test_broadcast.db"
    return Database(str(db_file))


@pytest_asyncio.fixture
async def initialized_db(db):
    """Return an initialized Database with all tables created."""
    await db.init_db()
    return db


# ---------------------------------------------------------------------------
# broadcast_chats: upsert
# ---------------------------------------------------------------------------

class TestUpsertBroadcastChat:
    """Verify that upsert_broadcast_chat creates and updates rows correctly."""

    @pytest.mark.asyncio
    async def test_insert_creates_row(self, initialized_db):
        await initialized_db.upsert_broadcast_chat(
            bot_username="testbot",
            chat_id=-1001234567890,
            chat_type="supergroup",
            title="My Group",
        )
        chats = await initialized_db.get_broadcast_chats("testbot")
        assert len(chats) == 1
        row = chats[0]
        assert row["chat_id"] == -1001234567890
        assert row["chat_type"] == "supergroup"
        assert row["title"] == "My Group"
        assert row["enabled"] == 0  # disabled by default

    @pytest.mark.asyncio
    async def test_upsert_does_not_reset_enabled_flag(self, initialized_db):
        """Re-upserting an existing chat must not reset enabled to 0."""
        await initialized_db.upsert_broadcast_chat("testbot", -100111, "group", "G1")
        await initialized_db.set_broadcast_chat_enabled("testbot", -100111, True)

        # Re-upsert (simulate bot re-joining)
        await initialized_db.upsert_broadcast_chat("testbot", -100111, "supergroup", "G1 renamed")

        chats = await initialized_db.get_broadcast_chats("testbot")
        assert len(chats) == 1
        row = chats[0]
        assert row["enabled"] == 1, "enabled should not be reset on re-upsert"
        assert row["title"] == "G1 renamed", "title should be updated"

    @pytest.mark.asyncio
    async def test_different_bots_same_chat_are_separate(self, initialized_db):
        await initialized_db.upsert_broadcast_chat("botA", -100111, "group", "G")
        await initialized_db.upsert_broadcast_chat("botB", -100111, "group", "G")

        chats_a = await initialized_db.get_broadcast_chats("botA")
        chats_b = await initialized_db.get_broadcast_chats("botB")
        assert len(chats_a) == 1
        assert len(chats_b) == 1

    @pytest.mark.asyncio
    async def test_bot_username_normalised_to_lowercase(self, initialized_db):
        await initialized_db.upsert_broadcast_chat("TestBot", -100111, "group", "G")
        # Retrieve using mixed case — should still find the row
        chats = await initialized_db.get_broadcast_chats("TESTBOT")
        assert len(chats) == 1


# ---------------------------------------------------------------------------
# broadcast_chats: enable / disable
# ---------------------------------------------------------------------------

class TestSetBroadcastChatEnabled:

    @pytest.mark.asyncio
    async def test_enable_sets_flag_to_true(self, initialized_db):
        await initialized_db.upsert_broadcast_chat("bot", -1, "group", "G")
        updated = await initialized_db.set_broadcast_chat_enabled("bot", -1, True)
        assert updated is True
        chats = await initialized_db.get_broadcast_chats("bot")
        assert chats[0]["enabled"] == 1

    @pytest.mark.asyncio
    async def test_disable_sets_flag_to_false(self, initialized_db):
        await initialized_db.upsert_broadcast_chat("bot", -1, "group", "G")
        await initialized_db.set_broadcast_chat_enabled("bot", -1, True)
        updated = await initialized_db.set_broadcast_chat_enabled("bot", -1, False)
        assert updated is True
        chats = await initialized_db.get_broadcast_chats("bot")
        assert chats[0]["enabled"] == 0

    @pytest.mark.asyncio
    async def test_returns_false_for_unknown_chat(self, initialized_db):
        updated = await initialized_db.set_broadcast_chat_enabled("bot", -9999, True)
        assert updated is False


# ---------------------------------------------------------------------------
# broadcast_chats: get_enabled_broadcast_chats
# ---------------------------------------------------------------------------

class TestGetEnabledBroadcastChats:

    @pytest.mark.asyncio
    async def test_returns_only_enabled_chats(self, initialized_db):
        await initialized_db.upsert_broadcast_chat("bot", -1, "group", "Enabled")
        await initialized_db.upsert_broadcast_chat("bot", -2, "supergroup", "Disabled")
        await initialized_db.set_broadcast_chat_enabled("bot", -1, True)

        enabled = await initialized_db.get_enabled_broadcast_chats("bot")
        assert len(enabled) == 1
        assert enabled[0]["chat_id"] == -1

    @pytest.mark.asyncio
    async def test_empty_when_none_enabled(self, initialized_db):
        await initialized_db.upsert_broadcast_chat("bot", -1, "group", "G")
        enabled = await initialized_db.get_enabled_broadcast_chats("bot")
        assert enabled == []


# ---------------------------------------------------------------------------
# broadcast_chats: delete
# ---------------------------------------------------------------------------

class TestDeleteBroadcastChat:

    @pytest.mark.asyncio
    async def test_delete_removes_row(self, initialized_db):
        await initialized_db.upsert_broadcast_chat("bot", -1, "group", "G")
        deleted = await initialized_db.delete_broadcast_chat("bot", -1)
        assert deleted == 1
        chats = await initialized_db.get_broadcast_chats("bot")
        assert chats == []

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_zero(self, initialized_db):
        deleted = await initialized_db.delete_broadcast_chat("bot", -9999)
        assert deleted == 0


# ---------------------------------------------------------------------------
# get_bot_usernames: now includes bot_instances
# ---------------------------------------------------------------------------

class TestGetBotUsernamesUnion:
    """
    get_bot_usernames must return the union of bots from bot_users AND bot_instances
    so that newly registered bots without any users are still visible.
    """

    @pytest.mark.asyncio
    async def test_returns_bot_with_no_users_from_instances(self, initialized_db):
        # Register a bot instance that has no users yet
        await initialized_db.register_bot_instance("newbot", bot_id=99, token_hash="abc123")
        names = await initialized_db.get_bot_usernames()
        assert "newbot" in names

    @pytest.mark.asyncio
    async def test_union_deduplicates(self, initialized_db):
        """A bot in both tables should appear only once."""
        await initialized_db.register_bot_instance("sharedbot", bot_id=1, token_hash="x")
        # Add a user for the same bot
        async with initialized_db.get_connection() as conn:
            await conn.execute(
                "INSERT INTO bot_users (user_id, bot_username) VALUES (?, ?)",
                (12345, "sharedbot"),
            )
            await conn.commit()

        names = await initialized_db.get_bot_usernames()
        assert names.count("sharedbot") == 1
