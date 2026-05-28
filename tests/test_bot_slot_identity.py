from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.resource_manager import clear_bot_temp_dir, get_bot_temp_dir
from utils.webhook_identity import (
    build_bot_slot,
    build_identity_hash,
    build_token_hash,
    build_webhook_secret,
)


def test_webhook_secret_generation_is_deterministic():
    first = build_webhook_secret(9, "999:TOKEN", 2, "salt")
    second = build_webhook_secret(9, "999:TOKEN", 2, "salt")
    assert first == second
    assert first != build_webhook_secret(8, "999:TOKEN", 2, "salt")


def test_identity_hash_regenerates_when_token_changes():
    old_hash = build_identity_hash(9, "old-token", 1, "salt")
    new_hash = build_identity_hash(9, "new-token", 2, "salt")
    assert old_hash != new_hash


def test_bot_slot_uses_permanent_slot_name_not_username():
    slot = build_bot_slot(9, "999:TOKEN", telegram_username="FlowersBot", identity_version=3)
    assert slot.slot_id == 9
    assert slot.slot_name == "bot_token_9"
    assert slot.telegram_username == "FlowersBot"
    assert slot.token_hash == build_token_hash("999:TOKEN")


def test_webhook_bot_index_maps_to_same_slot_id():
    import webhook_server

    assert webhook_server._slot_id_for_bot_index(0) == 0
    assert webhook_server._slot_id_for_bot_index(9) == 9


@pytest.mark.asyncio
async def test_per_slot_cache_invalidation_leaves_other_slots(tmp_path):
    from database import Database

    db = Database(str(tmp_path / "slot_cache.db"))
    await db.init_db()
    await db.cache_bot_file_id(-100, 1, 0, "photo", "bot_a", "A", slot_id=9, identity_hash="id9")
    await db.cache_bot_file_id(-100, 1, 0, "photo", "bot_b", "B", slot_id=10, identity_hash="id10")

    deleted = await db.clear_bot_file_id_cache_for_slot(9)

    assert deleted == 1
    assert await db.get_bot_file_id(-100, 1, 0, "bot_a", slot_id=9, identity_hash="id9") is None
    assert await db.get_bot_file_id(-100, 1, 0, "bot_b", slot_id=10, identity_hash="id10") == "B"


@pytest.mark.asyncio
async def test_slot_lock_allows_only_one_reconciler(tmp_path):
    from database import Database

    db = Database(str(tmp_path / "slot_lock.db"))
    await db.init_db()

    assert await db.acquire_bot_slot_lock(9, "worker-a", ttl_seconds=60) is True
    assert await db.acquire_bot_slot_lock(9, "worker-b", ttl_seconds=60) is False
    await db.release_bot_slot_lock(9, "worker-a")
    assert await db.acquire_bot_slot_lock(9, "worker-b", ttl_seconds=60) is True


def test_scoped_temp_cleanup_only_removes_requested_slot(tmp_path, monkeypatch):
    monkeypatch.setattr("utils.resource_manager.BOT_TMP_ROOT", tmp_path)
    slot_9 = get_bot_temp_dir(9)
    slot_10 = get_bot_temp_dir(10)
    slot_9.mkdir(parents=True)
    slot_10.mkdir(parents=True)
    (slot_9 / "stale.bin").write_bytes(b"old")
    (slot_10 / "keep.bin").write_bytes(b"new")

    removed, freed = clear_bot_temp_dir(9)

    assert removed == 1
    assert freed == 3
    assert not (slot_9 / "stale.bin").exists()
    assert (slot_10 / "keep.bin").exists()


@pytest.mark.asyncio
async def test_webhook_mismatch_auto_repairs_with_slot_lock(tmp_path):
    import webhook_server
    from database import Database

    db = Database(str(tmp_path / "webhook_lock.db"))
    await db.init_db()
    bot = MagicMock()
    bot.username = "FlowersBot"
    bot.get_webhook_info = AsyncMock(return_value=MagicMock(url="https://old.example/webhook", pending_update_count=0))
    bot.set_webhook = AsyncMock()
    slot = build_bot_slot(9, "999:TOKEN", telegram_username="FlowersBot")

    with patch.object(webhook_server, "Database", return_value=db):
        await webhook_server._validate_or_repair_webhook(
            bot,
            8,
            "https://new.example",
            "/webhook/8",
            slot.webhook_secret,
            slot=slot,
        )

    bot.set_webhook.assert_awaited_once()
    assert bot.set_webhook.await_args.kwargs["url"] == "https://new.example/webhook/8"
    assert bot.set_webhook.await_args.kwargs["secret_token"] == slot.webhook_secret


@pytest.mark.asyncio
async def test_webhook_repair_skips_when_lock_is_held(tmp_path):
    import webhook_server
    from database import Database

    db = Database(str(tmp_path / "webhook_lock_held.db"))
    await db.init_db()
    await db.acquire_bot_slot_lock(9, "other-worker", ttl_seconds=60)
    bot = MagicMock()
    bot.username = "FlowersBot"
    bot.get_webhook_info = AsyncMock(return_value=MagicMock(url="https://old.example/webhook", pending_update_count=0))
    bot.set_webhook = AsyncMock()
    slot = build_bot_slot(9, "999:TOKEN", telegram_username="FlowersBot")

    with patch.object(webhook_server, "Database", return_value=db):
        await webhook_server._validate_or_repair_webhook(
            bot,
            8,
            "https://new.example",
            "/webhook/8",
            slot.webhook_secret,
            slot=slot,
        )

    bot.set_webhook.assert_not_awaited()
