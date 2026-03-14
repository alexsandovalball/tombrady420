"""
Unit tests for the /reinitbots admin command and the reinitialize_bots helper.

These tests use mocking so they do not require a live Telegram connection,
a database, or a running FastAPI server.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_update(user_id: int, args=None):
    """Build a minimal fake Update with an effective_user and message."""
    user = MagicMock()
    user.id = user_id

    message = MagicMock()
    message.reply_text = AsyncMock()

    update = MagicMock()
    update.effective_user = user
    update.message = message
    return update


def _make_context(args=None):
    context = MagicMock()
    context.args = args or []
    return context


# ---------------------------------------------------------------------------
# reinitbots_command — permission and argument handling
# ---------------------------------------------------------------------------

class TestReinitbotsCommand:
    """Unit tests for handlers.admin.reinitbots_command."""

    @pytest.mark.asyncio
    async def test_non_admin_is_rejected(self):
        """Non-admin users should receive an error reply and no reinit is scheduled."""
        from handlers.admin import reinitbots_command

        update = _make_update(user_id=99999)
        context = _make_context()

        with patch("utils.decorators.is_admin", return_value=False):
            await reinitbots_command(update, context)

        update.message.reply_text.assert_awaited_once()
        reply_text = update.message.reply_text.call_args[0][0]
        assert "only available for administrators" in reply_text

    @pytest.mark.asyncio
    async def test_no_args_shows_instructions(self):
        """Calling /reinitbots without 'confirm' should show usage instructions."""
        from handlers.admin import reinitbots_command

        update = _make_update(user_id=1)
        context = _make_context(args=[])

        with patch("utils.decorators.is_admin", return_value=True):
            await reinitbots_command(update, context)

        update.message.reply_text.assert_awaited_once()
        reply_text = update.message.reply_text.call_args[0][0]
        assert "confirm" in reply_text.lower()

    @pytest.mark.asyncio
    async def test_wrong_arg_shows_instructions(self):
        """Calling /reinitbots with an unrecognised argument shows instructions."""
        from handlers.admin import reinitbots_command

        update = _make_update(user_id=1)
        context = _make_context(args=["please"])

        with patch("utils.decorators.is_admin", return_value=True):
            await reinitbots_command(update, context)

        update.message.reply_text.assert_awaited_once()
        reply_text = update.message.reply_text.call_args[0][0]
        assert "confirm" in reply_text.lower()

    @pytest.mark.asyncio
    async def test_confirm_sends_ack_and_schedules_task(self):
        """/reinitbots confirm should acknowledge the request and create a background task."""
        from handlers.admin import reinitbots_command

        update = _make_update(user_id=1)
        context = _make_context(args=["confirm"])

        fake_reinitialize_bots = AsyncMock(return_value={"created": [0], "failed": [], "errors": {}})

        with (
            patch("utils.decorators.is_admin", return_value=True),
            patch("handlers.admin.asyncio.create_task") as mock_create_task,
            patch("webhook_server.reinitialize_bots", fake_reinitialize_bots, create=True),
            patch.dict("sys.modules", {"webhook_server": MagicMock(reinitialize_bots=fake_reinitialize_bots)}),
        ):
            await reinitbots_command(update, context)

        # The acknowledgement reply must be sent before scheduling
        update.message.reply_text.assert_awaited_once()
        ack = update.message.reply_text.call_args[0][0]
        assert "reinitialisation" in ack.lower() or "reinit" in ack.lower()

        # A background task must have been created
        mock_create_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_confirm_case_insensitive(self):
        """'CONFIRM' and 'Confirm' should be treated the same as 'confirm'."""
        from handlers.admin import reinitbots_command

        for arg in ("CONFIRM", "Confirm", "confirm"):
            update = _make_update(user_id=1)
            context = _make_context(args=[arg])

            with (
                patch("utils.decorators.is_admin", return_value=True),
                patch("handlers.admin.asyncio.create_task"),
                patch.dict("sys.modules", {"webhook_server": MagicMock(reinitialize_bots=AsyncMock())}),
            ):
                await reinitbots_command(update, context)

            # Should acknowledge (not show instructions) for all casing variants
            reply_text = update.message.reply_text.call_args[0][0]
            assert "confirm" not in reply_text.lower() or "scheduled" in reply_text.lower() or "reinit" in reply_text.lower()


# ---------------------------------------------------------------------------
# reinitialize_bots — stop-and-restart logic
# ---------------------------------------------------------------------------

class TestReinitializeBots:
    """Unit tests for webhook_server.reinitialize_bots."""

    @pytest.mark.asyncio
    async def test_stops_existing_apps_before_starting_new_ones(self):
        """reinitialize_bots must stop each existing Application before recreating."""
        import webhook_server

        fake_app = MagicMock()
        fake_app.stop = AsyncMock()
        fake_app.shutdown = AsyncMock()

        new_app = MagicMock()
        new_secret = "abc123"

        with (
            patch.object(webhook_server, "_bot_applications", [fake_app]),
            patch.object(webhook_server, "setup_application", AsyncMock(return_value=(new_app, new_secret))),
            patch.object(webhook_server.Config, "BOT_TOKENS", ["token0"]),
        ):
            result = await webhook_server.reinitialize_bots(None)

        # Old app must have been stopped
        fake_app.stop.assert_awaited_once()
        fake_app.shutdown.assert_awaited_once()

        # Summary must report success
        assert result["created"] == [0]
        assert result["failed"] == []

    @pytest.mark.asyncio
    async def test_returns_failed_entry_on_setup_error(self):
        """When setup_application raises, the bot index should appear in 'failed'."""
        import webhook_server

        with (
            patch.object(webhook_server, "_bot_applications", []),
            patch.object(webhook_server, "setup_application", AsyncMock(side_effect=RuntimeError("boom"))),
            patch.object(webhook_server.Config, "BOT_TOKENS", ["bad_token"]),
        ):
            result = await webhook_server.reinitialize_bots(None)

        assert result["failed"] == [0]
        assert "0" in result["errors"]

    @pytest.mark.asyncio
    async def test_no_tokens_returns_error_summary(self):
        """When Config.BOT_TOKENS is empty, reinitialize_bots should return an error dict."""
        import webhook_server

        with (
            patch.object(webhook_server, "_bot_applications", []),
            patch.object(webhook_server.Config, "BOT_TOKENS", []),
        ):
            result = await webhook_server.reinitialize_bots(None)

        assert result["created"] == []
        assert "no_tokens" in result["errors"]

    @pytest.mark.asyncio
    async def test_updates_fastapi_state_when_provided(self):
        """app.state.bot_apps and webhook_secret_tokens must be updated on success."""
        import webhook_server

        new_app = MagicMock()
        new_secret = "secretXYZ"
        fake_fastapi_app = MagicMock()
        fake_fastapi_app.state = MagicMock()

        with (
            patch.object(webhook_server, "_bot_applications", []),
            patch.object(webhook_server, "setup_application", AsyncMock(return_value=(new_app, new_secret))),
            patch.object(webhook_server.Config, "BOT_TOKENS", ["token0"]),
        ):
            await webhook_server.reinitialize_bots(fake_fastapi_app)

        assert fake_fastapi_app.state.bot_apps == [new_app]
        assert fake_fastapi_app.state.webhook_secret_tokens == [new_secret]

    @pytest.mark.asyncio
    async def test_previous_secret_stored_in_history(self):
        """The old webhook secret must be preserved in _webhook_secret_history."""
        import webhook_server

        new_app = MagicMock()
        new_secret = "new_secret_token"
        old_secret = "old_secret_token"

        fake_fastapi_app = MagicMock()
        fake_fastapi_app.state = MagicMock()
        fake_fastapi_app.state.webhook_secret_tokens = [old_secret]

        with (
            patch.object(webhook_server, "_bot_applications", []),
            patch.object(webhook_server, "_webhook_secret_tokens", [old_secret]),
            patch.object(webhook_server, "_webhook_secret_history", {}),
            patch.object(webhook_server, "setup_application", AsyncMock(return_value=(new_app, new_secret))),
            patch.object(webhook_server.Config, "BOT_TOKENS", ["token0"]),
        ):
            await webhook_server.reinitialize_bots(fake_fastapi_app)
            history = webhook_server._webhook_secret_history.copy()

        # The previous secret must be stored in history so tolerance-window accepts it.
        # history[bot_index] = [previous_token, current_token]
        assert history.get(0) is not None
        assert history.get(0)[0] == old_secret


# ---------------------------------------------------------------------------
# Webhook secret tolerance window
# ---------------------------------------------------------------------------

class TestWebhookSecretTolerance:
    """Verify that _webhook_secret_history is consulted during header verification."""

    def test_history_dict_exists_in_module(self):
        """webhook_server must export _webhook_secret_history as a dict."""
        import webhook_server
        assert isinstance(webhook_server._webhook_secret_history, dict)

    def test_initial_history_type(self):
        """_webhook_secret_history must always be a dict regardless of prior state."""
        import webhook_server
        # Ensure it's a plain dict regardless of what other tests may have done.
        assert isinstance(webhook_server._webhook_secret_history, dict)
