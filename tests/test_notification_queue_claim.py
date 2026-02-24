"""
Tests for notification queue claim/lease semantics.

These tests verify that the atomic claim mechanism prevents duplicate
processing across concurrent workers and that lease expiry works correctly.
"""
import asyncio
import pytest
import pytest_asyncio
import tempfile
import os
from datetime import datetime, timedelta
from typing import List

# Import the Database class
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Database


@pytest_asyncio.fixture
async def test_db():
    """Create a temporary test database."""
    # Create temporary database file
    fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)

    # Initialize database
    db = Database(db_path)
    await db.init_db()

    yield db

    # Cleanup
    try:
        os.unlink(db_path)
    except:
        pass


@pytest_asyncio.fixture
async def populated_db(test_db):
    """Create a database with test data."""
    db = test_db

    # Add test users
    await db.track_user(user_id=1001, bot_username="testbot1")
    await db.track_user(user_id=1002, bot_username="testbot1")
    await db.track_user(user_id=1003, bot_username="testbot2")

    # Add test products
    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute(
            """INSERT INTO products (id, file_id, file_type, caption, chat_id, message_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (101, "file_id_1", "photo", "Test product 1", 123, 456, datetime.now())
        )
        await conn.execute(
            """INSERT INTO products (id, file_id, file_type, caption, chat_id, message_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (102, "file_id_2", "photo", "Test product 2", 123, 457, datetime.now())
        )
        await conn.commit()

    # Queue notifications
    await db.queue_notification(1001, 101)
    await db.queue_notification(1001, 102)
    await db.queue_notification(1002, 101)
    await db.queue_notification(1003, 102)

    yield db


@pytest.mark.asyncio
async def test_claim_basic_functionality(populated_db):
    """Test basic claim functionality."""
    db = populated_db

    # Claim notifications
    claimed = await db.claim_pending_notifications(limit=2, claim_token="token1")

    assert len(claimed) == 2
    assert claimed[0]["claimed_at"] is not None
    assert claimed[0]["claim_token"] == "token1"

    # Verify claimed notifications are not returned again
    claimed_again = await db.claim_pending_notifications(limit=10, claim_token="token2")
    assert len(claimed_again) == 2  # Should get the remaining 2 unclaimed notifications

    # Verify none of the previously claimed IDs are in the new claim
    claimed_ids = {n["id"] for n in claimed}
    claimed_again_ids = {n["id"] for n in claimed_again}
    assert len(claimed_ids & claimed_again_ids) == 0


@pytest.mark.asyncio
async def test_concurrent_claiming(populated_db):
    """Test that concurrent claims don't return duplicate notifications."""
    db = populated_db

    # Simulate two workers claiming simultaneously
    results = await asyncio.gather(
        db.claim_pending_notifications(limit=10, claim_token="worker1"),
        db.claim_pending_notifications(limit=10, claim_token="worker2")
    )

    claimed_worker1 = results[0]
    claimed_worker2 = results[1]

    # Extract notification IDs
    ids_worker1 = {n["id"] for n in claimed_worker1}
    ids_worker2 = {n["id"] for n in claimed_worker2}

    # Verify no overlap (no notification was claimed by both workers)
    assert len(ids_worker1 & ids_worker2) == 0

    # Verify all notifications were claimed
    total_claimed = len(ids_worker1) + len(ids_worker2)
    assert total_claimed == 4  # We queued 4 notifications in populated_db


@pytest.mark.asyncio
async def test_lease_expiry(test_db):
    """Test that expired leases allow re-claiming."""
    db = test_db

    # Add test data
    await db.track_user(user_id=2001, bot_username="testbot")

    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute(
            """INSERT INTO products (id, file_id, file_type, caption, chat_id, message_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (201, "file_id_1", "photo", "Test product", 123, 456, datetime.now())
        )
        await conn.commit()

    await db.queue_notification(2001, 201)

    # Claim with a very short lease (1 second)
    claimed = await db.claim_pending_notifications(
        limit=10,
        claim_token="token1",
        lease_seconds=1  # 1 second lease
    )
    assert len(claimed) == 1

    # Immediately try to claim again with same short lease - should get nothing
    claimed_again = await db.claim_pending_notifications(
        limit=10,
        claim_token="token2",
        lease_seconds=1  # Using same short lease
    )
    assert len(claimed_again) == 0

    # Wait for lease to expire (wait longer than the lease duration)
    await asyncio.sleep(2.0)  # Wait 2 seconds to ensure 1 second lease has expired

    # Now should be able to claim the expired notification
    # Use the same lease_seconds (1) or any value - the claim should succeed
    # because claimed_at is now > 2 seconds old, older than (now - 1 second)
    claimed_after_expiry = await db.claim_pending_notifications(
        limit=10,
        claim_token="token3",
        lease_seconds=1  # Use same lease duration - claimed_at is older than this
    )
    assert len(claimed_after_expiry) == 1
    assert claimed_after_expiry[0]["id"] == claimed[0]["id"]


@pytest.mark.asyncio
async def test_release_claim_by_notification_id(populated_db):
    """Test releasing a claim on a specific notification."""
    db = populated_db

    # Claim notifications
    claimed = await db.claim_pending_notifications(limit=2, claim_token="token1")
    assert len(claimed) == 2

    # Release one notification
    notification_id = claimed[0]["id"]
    released = await db.release_notification_claim(notification_id=notification_id)
    assert released == 1

    # The released notification should now be claimable
    claimed_again = await db.claim_pending_notifications(limit=10, claim_token="token2")
    released_ids = {n["id"] for n in claimed_again}
    assert notification_id in released_ids


@pytest.mark.asyncio
async def test_release_claim_by_token(populated_db):
    """Test releasing all claims with a specific token."""
    db = populated_db

    # Claim notifications with token1
    claimed = await db.claim_pending_notifications(limit=2, claim_token="token1")
    assert len(claimed) == 2

    # Release all notifications with token1
    released = await db.release_notification_claim(claim_token="token1")
    assert released == 2

    # All released notifications should now be claimable
    claimed_again = await db.claim_pending_notifications(limit=10, claim_token="token2")
    assert len(claimed_again) == 4  # All 4 should be available again


@pytest.mark.asyncio
async def test_claimed_by_field(test_db):
    """Test that claimed_by field is set correctly."""
    db = test_db

    # Add test data
    await db.track_user(user_id=3001, bot_username="testbot")

    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute(
            """INSERT INTO products (id, file_id, file_type, caption, chat_id, message_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (301, "file_id_1", "photo", "Test product", 123, 456, datetime.now())
        )
        await conn.commit()

    await db.queue_notification(3001, 301)

    # Claim with claimed_by
    claimed = await db.claim_pending_notifications(
        limit=10,
        claim_token="token1",
        claimed_by="worker-instance-1"
    )

    assert len(claimed) == 1

    # Verify claimed_by is stored (need to query directly since claim returns old data)
    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT claimed_by FROM notification_queue WHERE id = ?",
            (claimed[0]["id"],)
        ) as cursor:
            row = await cursor.fetchone()
            assert row["claimed_by"] == "worker-instance-1"


@pytest.mark.asyncio
async def test_mark_sent_does_not_affect_claims(populated_db):
    """Test that marking as sent prevents future claims."""
    db = populated_db

    # Claim notifications
    claimed = await db.claim_pending_notifications(limit=2, claim_token="token1")
    assert len(claimed) == 2

    # Mark one as sent
    await db.mark_notification_sent(claimed[0]["id"])

    # Release all claims
    await db.release_notification_claim(claim_token="token1")

    # Try to claim again - should not get the sent notification
    claimed_again = await db.claim_pending_notifications(limit=10, claim_token="token2")
    sent_id = claimed[0]["id"]
    claimed_again_ids = {n["id"] for n in claimed_again}

    assert sent_id not in claimed_again_ids
    assert len(claimed_again) == 3  # 4 total - 1 marked sent = 3


@pytest.mark.asyncio
async def test_auto_generated_claim_token(populated_db):
    """Test that claim token is auto-generated if not provided."""
    import aiosqlite

    db = populated_db

    # Claim without providing token
    claimed = await db.claim_pending_notifications(limit=2)

    assert len(claimed) == 2

    # Token should be auto-generated (UUID format)
    async with aiosqlite.connect(db.db_path) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(
            "SELECT claim_token FROM notification_queue WHERE id = ?",
            (claimed[0]["id"],)
        ) as cursor:
            row = await cursor.fetchone()
            token = row["claim_token"]
            assert token is not None
            assert len(token) > 0


@pytest.mark.asyncio
async def test_release_requires_parameter(populated_db):
    """Test that release_notification_claim requires at least one parameter."""
    db = populated_db

    with pytest.raises(ValueError):
        await db.release_notification_claim()


@pytest.mark.asyncio
async def test_queue_deduplication_with_claims(test_db):
    """Test that the unique index prevents duplicate queuing even with claims."""
    db = test_db

    # Add test data
    await db.track_user(user_id=4001, bot_username="testbot")

    import aiosqlite
    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute(
            """INSERT INTO products (id, file_id, file_type, caption, chat_id, message_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (401, "file_id_1", "photo", "Test product", 123, 456, datetime.now())
        )
        await conn.commit()

    # Queue notification
    await db.queue_notification(4001, 401)

    # Try to queue duplicate - should be prevented
    await db.queue_notification(4001, 401)

    # Should only have one notification
    claimed = await db.claim_pending_notifications(limit=10)
    assert len(claimed) == 1


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
