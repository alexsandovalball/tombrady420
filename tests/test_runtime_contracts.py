"""Small regression tests for the highest-risk runtime contracts."""

from webhook_server import _is_bot_active, _is_bot_online


def test_webhook_warning_does_not_make_a_live_bot_offline():
    snapshot = {
        "running": True,
        "telegram_ok": True,
        "webhook_ok": True,
        "telegram_health": "webhook_warning",
    }

    assert _is_bot_active(snapshot) is True
    assert _is_bot_online(snapshot) is True


def test_invalid_or_missing_webhook_is_not_online():
    for health in ("deleted_or_invalid", "webhook_missing", "webhook_mismatch", "app_stopped"):
        snapshot = {
            "running": health != "app_stopped",
            "telegram_ok": health != "deleted_or_invalid",
            "webhook_ok": False,
            "telegram_health": health,
        }
        assert _is_bot_online(snapshot) is False


def test_setup_warning_does_not_override_liveness():
    snapshot = {
        "running": True,
        "telegram_ok": True,
        "webhook_ok": True,
        "telegram_health": "ok",
        "startup_issue_count": 2,
    }

    assert _is_bot_active(snapshot) is True
    assert _is_bot_online(snapshot) is True
