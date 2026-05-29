import pytest
from fastapi import HTTPException

from database import Database


class DummyRequest:
    def __init__(self, headers=None):
        self.headers = headers or {}


def test_admin_dashboard_auth_requires_configured_token(monkeypatch):
    import webhook_server

    monkeypatch.delenv("ADMIN_DASHBOARD_TOKEN", raising=False)
    monkeypatch.setattr(webhook_server.Config, "ADMIN_DASHBOARD_TOKEN", None)

    with pytest.raises(HTTPException) as exc:
        webhook_server._require_admin_dashboard_auth(DummyRequest())

    assert exc.value.status_code == 503


def test_admin_dashboard_auth_accepts_bearer_token(monkeypatch):
    import webhook_server

    monkeypatch.setenv("ADMIN_DASHBOARD_TOKEN", "secret-token")

    webhook_server._require_admin_dashboard_auth(
        DummyRequest({"Authorization": "Bearer secret-token"})
    )


def test_admin_dashboard_auth_rejects_wrong_token(monkeypatch):
    import webhook_server

    monkeypatch.setenv("ADMIN_DASHBOARD_TOKEN", "secret-token")

    with pytest.raises(HTTPException) as exc:
        webhook_server._require_admin_dashboard_auth(
            DummyRequest({"Authorization": "Bearer wrong-token"})
        )

    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_webapp_events_are_persisted_for_dashboard(tmp_path):
    db = Database(str(tmp_path / "admin_dashboard.db"))
    await db.init_db()

    await db.track_user(
        user_id=12345,
        username="buyer",
        first_name="Buyer",
        last_name="One",
        bot_username="TomBrady007bot",
    )
    persisted = await db.log_webapp_events([
        {
            "timestamp": "2026-05-29T09:32:24.700Z",
            "session_id": "session-1",
            "user_id": 12345,
            "username": "buyer",
            "event_type": "user_interaction",
            "action": "view_product",
            "status": "success",
            "details": {"product_id": 976},
        }
    ])

    snapshot = await db.get_admin_dashboard_snapshot()

    assert persisted == 1
    assert snapshot["summary"]["bot_users"] == 1
    assert snapshot["summary"]["webapp_events_24h"] == 1
    assert snapshot["recent_webapp_events"][0]["action"] == "view_product"
    assert snapshot["webapp_event_counts"][0]["count"] == 1
