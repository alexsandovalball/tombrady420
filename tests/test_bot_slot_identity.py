from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from utils.resource_manager import clear_bot_temp_dir, get_bot_temp_dir
from utils.webhook_identity import (
    build_bot_slot,
    build_identity_hash,
    resolve_bot_identity,
    build_token_hash,
    build_webhook_secret,
)


def test_webhook_secret_generation_is_deterministic():
    first = build_webhook_secret(9, "999:TOKEN", 2, "salt")
    second = build_webhook_secret(9, "999:TOKEN", 2, "salt")
    assert first == second
    assert first == build_webhook_secret(8, "999:TOKEN", 2, "salt")
    assert first != build_webhook_secret(9, "888:TOKEN", 2, "salt")


def test_bot_identity_prefers_telegram_bot_id_over_runtime_slot():
    first = build_bot_slot(1, "old-token", telegram_bot_id=12345)
    second = build_bot_slot(9, "new-token", telegram_bot_id=12345)

    assert first.identity_hash == second.identity_hash
    assert first.webhook_secret == second.webhook_secret


def test_token_identity_is_stable_when_environment_order_changes():
    token_a = "111:AAA"
    token_b = "222:BBB"
    original_order = [build_webhook_secret(idx, token) for idx, token in enumerate([token_a, token_b])]
    swapped_order = [build_webhook_secret(idx, token) for idx, token in enumerate([token_b, token_a])]

    assert original_order[0] == swapped_order[1]
    assert original_order[1] == swapped_order[0]
    assert resolve_bot_identity(token_a) == resolve_bot_identity(token_a)


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


@pytest.mark.asyncio
async def test_bot_registry_identity_survives_token_reorder_and_rotation(tmp_path):
    from database import Database

    db = Database(str(tmp_path / "registry.db"))
    await db.init_db()

    first = await db.resolve_bot_id_from_registry(111, "alpha_bot", "hash-a", lambda: "stored-secret-a")
    other = await db.resolve_bot_id_from_registry(222, "beta_bot", "hash-b", lambda: "stored-secret-b")
    rotated = await db.resolve_bot_id_from_registry(111, "alpha_bot", "hash-a2", lambda: "new-secret")

    assert rotated["bot_id"] == first["bot_id"]
    assert rotated["webhook_secret"] == "new-secret"
    assert other["bot_id"] != first["bot_id"]


@pytest.mark.asyncio
async def test_webhook_expected_secret_is_loaded_from_registry(tmp_path, monkeypatch):
    import webhook_server
    from database import Database

    db = Database(str(tmp_path / "webhook_registry.db"))
    await db.init_db()
    row = await db.resolve_bot_id_from_registry(111, "alpha_bot", "hash-a", lambda: "stored-secret")

    monkeypatch.setattr(webhook_server, "_catalog_db", db)

    assert await webhook_server._get_expected_secret_token_for_registry_bot(row["bot_id"]) == "stored-secret"


@pytest.mark.asyncio
async def test_legacy_webhook_path_resolves_owner_from_incoming_secret(tmp_path, monkeypatch):
    import webhook_server
    from database import Database

    class Request:
        headers = {"X-Telegram-Bot-Api-Secret-Token": "owner-secret"}

    db = Database(str(tmp_path / "legacy_secret_owner.db"))
    await db.init_db()
    row = await db.resolve_bot_id_from_registry(999, "bot9", "hash", lambda: "owner-secret")

    monkeypatch.setattr(webhook_server, "_catalog_db", db)

    assert await webhook_server._resolve_registry_bot_from_incoming_secret(Request()) == row["bot_id"]


@pytest.mark.asyncio
async def test_legacy_numeric_webhook_routes_known_secret_to_owner(tmp_path, monkeypatch):
    import webhook_server
    from database import Database

    db = Database(str(tmp_path / "legacy_route_known.db"))
    await db.init_db()
    row = await db.resolve_bot_id_from_registry(999, "bot9", "hash", lambda: "owner-secret")
    request = SimpleNamespace(
        headers={"X-Telegram-Bot-Api-Secret-Token": "owner-secret"},
        client=SimpleNamespace(host="149.154.167.220"),
    )
    processed = AsyncMock(return_value=webhook_server.Response(status_code=202))

    monkeypatch.setattr(webhook_server, "_catalog_db", db)
    monkeypatch.setattr(webhook_server, "_bot_applications_by_registry_id", {})
    monkeypatch.setattr(webhook_server, "process_webhook_request", processed)

    response = await webhook_server.webhook_indexed(request, "9")

    assert response.status_code == 202
    processed.assert_awaited_once_with(request, row["bot_id"])


@pytest.mark.asyncio
async def test_legacy_numeric_webhook_drains_unknown_telegram_orphan(tmp_path, monkeypatch):
    import webhook_server
    from database import Database

    db = Database(str(tmp_path / "legacy_route_orphan.db"))
    await db.init_db()
    request = SimpleNamespace(
        headers={"X-Telegram-Bot-Api-Secret-Token": "old-secret"},
        client=SimpleNamespace(host="149.154.167.220"),
    )
    processed = AsyncMock()

    monkeypatch.setattr(webhook_server, "_catalog_db", db)
    monkeypatch.setattr(webhook_server, "_bot_applications_by_registry_id", {})
    monkeypatch.setattr(webhook_server, "is_telegram_ip", lambda ip: True)
    monkeypatch.setattr(webhook_server, "process_webhook_request", processed)

    response = await webhook_server.webhook_indexed(request, "9")

    assert response.status_code == 200
    processed.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_numeric_webhook_cached_orphan_skips_registry_lookup(tmp_path, monkeypatch):
    import webhook_server
    from database import Database

    db = Database(str(tmp_path / "legacy_route_cached_orphan.db"))
    await db.init_db()
    request = SimpleNamespace(
        headers={"X-Telegram-Bot-Api-Secret-Token": "old-secret"},
        client=SimpleNamespace(host="149.154.167.220"),
    )
    processed = AsyncMock()
    resolve_owner = AsyncMock(return_value=None)

    monkeypatch.setattr(webhook_server, "_catalog_db", db)
    monkeypatch.setattr(webhook_server, "_bot_applications_by_registry_id", {})
    monkeypatch.setattr(webhook_server, "is_telegram_ip", lambda ip: True)
    monkeypatch.setattr(webhook_server, "process_webhook_request", processed)
    monkeypatch.setattr(webhook_server, "_resolve_registry_bot_from_incoming_secret", resolve_owner)
    webhook_server._orphan_legacy_secret_cache.clear()
    webhook_server._orphan_legacy_log_last_seen.clear()

    first = await webhook_server.webhook_indexed(request, "9")
    second = await webhook_server.webhook_indexed(request, "9")

    assert first.status_code == 200
    assert second.status_code == 200
    resolve_owner.assert_awaited_once_with(request)
    processed.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_numeric_webhook_rejects_unknown_non_telegram_orphan(tmp_path, monkeypatch):
    import webhook_server
    from database import Database

    db = Database(str(tmp_path / "legacy_route_non_telegram.db"))
    await db.init_db()
    request = SimpleNamespace(
        headers={"X-Telegram-Bot-Api-Secret-Token": "old-secret"},
        client=SimpleNamespace(host="203.0.113.20"),
    )
    processed = AsyncMock()

    monkeypatch.setattr(webhook_server, "_catalog_db", db)
    monkeypatch.setattr(webhook_server, "_bot_applications_by_registry_id", {})
    monkeypatch.setattr(webhook_server, "is_telegram_ip", lambda ip: False)
    monkeypatch.setattr(webhook_server, "process_webhook_request", processed)

    response = await webhook_server.webhook_indexed(request, "9")

    assert response.status_code == 410
    processed.assert_not_awaited()


@pytest.mark.asyncio
async def test_rebuild_all_webhook_secrets_replaces_stale_registry_secret(tmp_path, monkeypatch):
    import webhook_server
    from database import Database
    from utils.webhook_identity import build_registry_webhook_secret

    db = Database(str(tmp_path / "webhook_rebuild.db"))
    await db.init_db()
    row = await db.resolve_bot_id_from_registry(999, "bot9", "hash-old", lambda: "stale-secret")

    monkeypatch.setattr(webhook_server, "_catalog_db", db)
    monkeypatch.setattr(webhook_server, "_bot_applications_by_registry_id", {row["bot_id"]: object()})

    rebuilt = await webhook_server.rebuild_all_webhook_secrets(force=True)
    refreshed = await db.get_bot_registry_by_id(row["bot_id"])

    assert rebuilt[row["bot_id"]] == build_registry_webhook_secret(999)
    assert refreshed["webhook_secret"] == build_registry_webhook_secret(999)


@pytest.mark.asyncio
async def test_media_gc_worker_deletes_only_db_expired_media(tmp_path, monkeypatch):
    import webhook_server
    from database import Database
    from datetime import datetime, timedelta

    db = Database(str(tmp_path / "media_gc.db"))
    await db.init_db()
    bot = await db.resolve_bot_id_from_registry(111, "alpha_bot", "hash-a", lambda: "stored-secret")
    expired = tmp_path / "expired.bin"
    fresh = tmp_path / "fresh.bin"
    orphan = tmp_path / "orphan.bin"
    expired.write_bytes(b"old")
    fresh.write_bytes(b"new")
    orphan.write_bytes(b"orphan")

    await db.record_media_object(
        "expired",
        bot["bot_id"],
        str(expired),
        ttl_expiry=datetime.now() - timedelta(seconds=1),
    )
    await db.record_media_object(
        "fresh",
        bot["bot_id"],
        str(fresh),
        ttl_expiry=datetime.now() + timedelta(hours=1),
    )
    monkeypatch.setattr(webhook_server, "_catalog_db", db)

    removed, _ = await webhook_server.media_gc_worker()

    assert removed == 1
    assert not expired.exists()
    assert fresh.exists()
    assert orphan.exists()


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
