"""
Tests for notification deduplication and claim/lease semantics.

Uses an in-memory SQLite database to exercise the real Database methods without
disk I/O or external dependencies.
"""
import asyncio
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio


# ---------------------------------------------------------------------------
# Fixture: ephemeral in-memory Database instance
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    """Return a Database instance backed by a temporary file."""
    from database import Database
    db_file = tmp_path / "test_notifications.db"
    return Database(str(db_file))


@pytest_asyncio.fixture
async def initialized_db(db):
    """Return an initialized Database with all tables created."""
    await db.init_db()
    return db


# ---------------------------------------------------------------------------
# Notification deduplication tests
# ---------------------------------------------------------------------------

class TestNotificationDedupe:
    """
    Verify that queue_notification prevents duplicate entries for the same
    (user_id, product_id) pair.
    """

    @pytest.mark.asyncio
    async def test_single_notification_queued(self, initialized_db):
        """Queueing one notification should insert exactly one row."""
        await initialized_db.queue_notification(user_id=1, product_id=10)
        pending = await initialized_db.get_pending_notifications(limit=100)
        assert len(pending) == 1
        assert pending[0]["user_id"] == 1
        assert pending[0]["product_id"] == 10

    @pytest.mark.asyncio
    async def test_duplicate_not_inserted(self, initialized_db):
        """Queueing the same (user_id, product_id) twice must result in one row."""
        await initialized_db.queue_notification(user_id=1, product_id=10)
        await initialized_db.queue_notification(user_id=1, product_id=10)

        pending = await initialized_db.get_pending_notifications(limit=100)
        assert len(pending) == 1, "Duplicate notification should be silently ignored"

    @pytest.mark.asyncio
    async def test_different_users_get_separate_rows(self, initialized_db):
        """Same product_id for different user_ids must produce separate rows."""
        await initialized_db.queue_notification(user_id=1, product_id=10)
        await initialized_db.queue_notification(user_id=2, product_id=10)

        pending = await initialized_db.get_pending_notifications(limit=100)
        assert len(pending) == 2

    @pytest.mark.asyncio
    async def test_different_products_get_separate_rows(self, initialized_db):
        """Same user_id for different product_ids must produce separate rows."""
        await initialized_db.queue_notification(user_id=1, product_id=10)
        await initialized_db.queue_notification(user_id=1, product_id=20)

        pending = await initialized_db.get_pending_notifications(limit=100)
        assert len(pending) == 2

    @pytest.mark.asyncio
    async def test_sent_notification_can_be_re_queued(self, initialized_db):
        """After marking a notification as sent, the same pair can be queued again."""
        await initialized_db.queue_notification(user_id=1, product_id=10)
        pending = await initialized_db.get_pending_notifications(limit=100)
        notif_id = pending[0]["id"]

        # Mark as sent
        await initialized_db.mark_notification_sent(notif_id)

        # Should now be allowed again (sent_at is set, so it's no longer "pending")
        await initialized_db.queue_notification(user_id=1, product_id=10)
        new_pending = await initialized_db.get_pending_notifications(limit=100)
        assert len(new_pending) == 1, "Re-queue after sent should produce a new row"


# ---------------------------------------------------------------------------
# Claim / lease semantics tests
# ---------------------------------------------------------------------------

class TestClaimLeaseSemantics:
    """
    Verify that claim_pending_notifications and release_notification_claim
    implement correct lease/exclusion semantics.
    """

    @pytest.mark.asyncio
    async def test_claim_returns_correct_count(self, initialized_db):
        """Claiming should return up to *limit* rows."""
        for uid in range(5):
            await initialized_db.queue_notification(user_id=uid, product_id=99)

        token = str(uuid.uuid4())
        claimed = await initialized_db.claim_pending_notifications(
            limit=3, claim_token=token, lease_seconds=300
        )
        assert len(claimed) == 3

    @pytest.mark.asyncio
    async def test_claimed_rows_have_token(self, initialized_db):
        """Each claimed row must carry the provided claim_token."""
        await initialized_db.queue_notification(user_id=1, product_id=99)

        token = str(uuid.uuid4())
        claimed = await initialized_db.claim_pending_notifications(
            limit=10, claim_token=token
        )
        assert len(claimed) == 1
        assert claimed[0]["claim_token"] == token

    @pytest.mark.asyncio
    async def test_second_claim_excludes_already_claimed(self, initialized_db):
        """
        A second claim call (with a different token) must not return rows
        that are still within their lease window.
        """
        for uid in range(3):
            await initialized_db.queue_notification(user_id=uid, product_id=99)

        token_a = str(uuid.uuid4())
        token_b = str(uuid.uuid4())

        # First worker claims all 3
        claimed_a = await initialized_db.claim_pending_notifications(
            limit=10, claim_token=token_a, lease_seconds=300
        )
        assert len(claimed_a) == 3

        # Second worker should get nothing (lease still valid)
        claimed_b = await initialized_db.claim_pending_notifications(
            limit=10, claim_token=token_b, lease_seconds=300
        )
        assert len(claimed_b) == 0, "Active claims must not be double-claimed"

    @pytest.mark.asyncio
    async def test_expired_lease_can_be_reclaimed(self, initialized_db):
        """
        After a claim's lease expires, the notification must be available
        for a new worker to claim.
        """
        await initialized_db.queue_notification(user_id=1, product_id=99)

        token_a = str(uuid.uuid4())
        # Claim with a very short lease (1 second in the second call)
        await initialized_db.claim_pending_notifications(
            limit=10, claim_token=token_a, lease_seconds=300
        )

        # Simulate lease expiry by directly manipulating the claimed_at
        # to a time well in the past (60 seconds ago), so a 30-second lease
        # threshold will consider the row re-claimable.
        import aiosqlite
        expired_time = (datetime.now() - timedelta(seconds=60)).isoformat()
        async with aiosqlite.connect(initialized_db.db_path) as conn:
            await conn.execute(
                "UPDATE notification_queue SET claimed_at = ? WHERE claim_token = ?",
                (expired_time, token_a),
            )
            await conn.commit()

        # A new worker with a 30-second lease should be able to claim it,
        # because the row was last claimed 60s ago (> 30s threshold).
        token_b = str(uuid.uuid4())
        claimed_b = await initialized_db.claim_pending_notifications(
            limit=10, claim_token=token_b, lease_seconds=30
        )
        assert len(claimed_b) == 1, "Expired lease must allow re-claim"
        assert claimed_b[0]["claim_token"] == token_b

    @pytest.mark.asyncio
    async def test_release_makes_row_reclaimable(self, initialized_db):
        """Releasing a claim must make the row available again immediately."""
        await initialized_db.queue_notification(user_id=1, product_id=99)

        token_a = str(uuid.uuid4())
        claimed = await initialized_db.claim_pending_notifications(
            limit=10, claim_token=token_a, lease_seconds=300
        )
        notif_id = claimed[0]["id"]

        # Release the claim
        await initialized_db.release_notification_claim(notification_id=notif_id)

        # Should be available to claim again
        token_b = str(uuid.uuid4())
        claimed_b = await initialized_db.claim_pending_notifications(
            limit=10, claim_token=token_b, lease_seconds=300
        )
        assert len(claimed_b) == 1
        assert claimed_b[0]["claim_token"] == token_b

    @pytest.mark.asyncio
    async def test_mark_sent_removes_from_pending(self, initialized_db):
        """After marking a notification sent, it must not appear in pending queries."""
        await initialized_db.queue_notification(user_id=1, product_id=99)
        pending_before = await initialized_db.get_pending_notifications(limit=100)
        notif_id = pending_before[0]["id"]

        await initialized_db.mark_notification_sent(notif_id)

        pending_after = await initialized_db.get_pending_notifications(limit=100)
        ids_after = [n["id"] for n in pending_after]
        assert notif_id not in ids_after


# ---------------------------------------------------------------------------
# is_primary_instance tests
# ---------------------------------------------------------------------------

class TestIsPrimaryInstance:
    """
    Verify the unified is_primary_instance() utility in utils/primary_instance.py.
    """

    def _make_context(self, token: str):
        ctx = MagicMock()
        ctx.bot = MagicMock()
        ctx.bot.token = token
        return ctx

    def test_first_token_is_primary(self):
        from utils.primary_instance import is_primary_instance

        ctx = self._make_context("token_A")
        with patch("utils.primary_instance.Config") as cfg:
            cfg.BOT_TOKENS = ["token_A", "token_B"]
            assert is_primary_instance(ctx) is True

    def test_secondary_token_is_not_primary(self):
        from utils.primary_instance import is_primary_instance

        ctx = self._make_context("token_B")
        with patch("utils.primary_instance.Config") as cfg:
            cfg.BOT_TOKENS = ["token_A", "token_B"]
            assert is_primary_instance(ctx) is False

    def test_no_tokens_defaults_to_primary(self):
        """When BOT_TOKENS is empty, instance should default to primary."""
        from utils.primary_instance import is_primary_instance

        ctx = self._make_context("any_token")
        with patch("utils.primary_instance.Config") as cfg:
            cfg.BOT_TOKENS = []
            assert is_primary_instance(ctx) is True

    def test_missing_bot_attr_defaults_to_primary(self):
        """Context without bot attribute should default to primary."""
        from utils.primary_instance import is_primary_instance

        ctx = MagicMock(spec=[])  # no attributes
        with patch("utils.primary_instance.Config") as cfg:
            cfg.BOT_TOKENS = ["token_A"]
            assert is_primary_instance(ctx) is True

    def test_notification_service_delegates_to_util(self):
        """NotificationService._is_primary_instance must use the shared utility."""
        from utils.notifications import NotificationService

        db_mock = MagicMock()
        service = NotificationService(db_mock)
        ctx = self._make_context("token_B")

        with patch("utils.primary_instance.Config") as cfg:
            cfg.BOT_TOKENS = ["token_A", "token_B"]
            result = service._is_primary_instance(ctx)

        assert result is False, "Secondary bot must not be treated as primary"


class TestProductWebAppButtons:
    def test_private_notification_keyboard_links_to_product_webapp(self, monkeypatch):
        from utils.notifications import NotificationService

        monkeypatch.setattr(
            "utils.notifications.Config.WEB_APP_URL",
            "https://example.test/webapp/index.html",
        )

        markup = NotificationService._build_product_notification_keyboard(976)
        rows = markup.inline_keyboard

        assert rows[0][0].callback_data == "product|976"
        assert rows[1][0].text == "🛍️ View in WebApp"
        assert rows[1][0].url == "https://example.test/webapp/index.html?product_id=976"
        assert rows[2][0].callback_data == "categories"

    def test_broadcast_keyboard_links_to_product_webapp(self, monkeypatch):
        from utils.notifications import NotificationService

        monkeypatch.setattr(
            "utils.notifications.Config.WEB_APP_URL",
            "https://example.test/webapp/index.html?lang=en",
        )

        markup, details = NotificationService._build_product_broadcast_keyboard(
            976,
            "TomBrady007bot",
        )
        buttons = markup.inline_keyboard[0]

        assert buttons[0].url == "https://t.me/TomBrady007bot?start=product_976"
        assert buttons[1].text == "🛍️ View in WebApp"
        assert buttons[1].url == "https://example.test/webapp/index.html?lang=en&product_id=976"
        assert any("product_id=976" in detail for detail in details)
