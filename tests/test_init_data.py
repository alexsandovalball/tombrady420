"""
Tests for Telegram WebApp initData verification.

Validates that:
- verify_init_data correctly accepts valid HMAC-signed payloads
- verify_init_data correctly rejects tampered, missing-hash, or empty payloads
- parse_init_data correctly parses valid initData strings
- _get_validated_init_data (indirectly via mocked request) rejects missing data
  on auth-required endpoints
"""

import hashlib
import hmac
import json
import urllib.parse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers — build a valid Telegram WebApp initData string
# ---------------------------------------------------------------------------

def _build_init_data(params: dict, bot_token: str) -> str:
    """
    Produce a valid Telegram WebApp initData string signed with *bot_token*.
    """
    # Sort params, exclude hash
    items = sorted((k, v) for k, v in params.items() if k != "hash")
    data_check_string = "\n".join(f"{k}={v}" for k, v in items)

    secret_key = hmac.new(
        key=b"WebAppData",
        msg=bot_token.encode(),
        digestmod=hashlib.sha256,
    ).digest()

    computed_hash = hmac.new(
        key=secret_key,
        msg=data_check_string.encode(),
        digestmod=hashlib.sha256,
    ).hexdigest()

    all_params = dict(items)
    all_params["hash"] = computed_hash
    return urllib.parse.urlencode(all_params)


_FAKE_TOKEN = "123456:FAKE_BOT_TOKEN_FOR_TESTS"

_SAMPLE_USER = {
    "id": 111111,
    "first_name": "Test",
    "last_name": "User",
    "username": "testuser",
    "language_code": "en",
}

_VALID_INIT_DATA_PARAMS = {
    "query_id": "AAHdF6IQAAAAAN0XohDhrOrc",
    "user": json.dumps(_SAMPLE_USER, separators=(",", ":")),
    "auth_date": "1700000000",
}


# ---------------------------------------------------------------------------
# Unit tests for verify_init_data
# ---------------------------------------------------------------------------

class TestVerifyInitData:
    """Unit tests for utils.webapp_security.verify_init_data."""

    def test_valid_init_data_accepted(self):
        """Correctly signed initData must return True."""
        from utils.webapp_security import verify_init_data

        init_data = _build_init_data(_VALID_INIT_DATA_PARAMS, _FAKE_TOKEN)
        assert verify_init_data(init_data, _FAKE_TOKEN) is True

    def test_wrong_token_rejected(self):
        """initData signed with a different token must be rejected."""
        from utils.webapp_security import verify_init_data

        init_data = _build_init_data(_VALID_INIT_DATA_PARAMS, _FAKE_TOKEN)
        assert verify_init_data(init_data, "999999:WRONG_TOKEN") is False

    def test_tampered_data_rejected(self):
        """Modifying any field after signing must invalidate the payload."""
        from utils.webapp_security import verify_init_data

        init_data = _build_init_data(_VALID_INIT_DATA_PARAMS, _FAKE_TOKEN)
        # Append a tampered field
        tampered = init_data + "&extra=injected"
        assert verify_init_data(tampered, _FAKE_TOKEN) is False

    def test_missing_hash_rejected(self):
        """initData with no 'hash' field must return False."""
        from utils.webapp_security import verify_init_data

        no_hash = urllib.parse.urlencode(
            {k: v for k, v in _VALID_INIT_DATA_PARAMS.items()}
        )
        assert verify_init_data(no_hash, _FAKE_TOKEN) is False

    def test_empty_string_rejected(self):
        """An empty initData string must return False."""
        from utils.webapp_security import verify_init_data

        assert verify_init_data("", _FAKE_TOKEN) is False

    def test_garbage_input_rejected(self):
        """Garbage input must be handled gracefully and return False."""
        from utils.webapp_security import verify_init_data

        assert verify_init_data("not_init_data_at_all", _FAKE_TOKEN) is False

    def test_hash_alone_rejected(self):
        """Providing only a hash field (no data to check against) must fail."""
        from utils.webapp_security import verify_init_data

        only_hash = "hash=deadbeef" * 1 + "0" * 56  # wrong length/value
        assert verify_init_data(only_hash, _FAKE_TOKEN) is False


# ---------------------------------------------------------------------------
# Unit tests for parse_init_data
# ---------------------------------------------------------------------------

class TestParseInitData:
    """Unit tests for utils.webapp_security.parse_init_data."""

    def test_parses_user_json(self):
        """User JSON embedded in initData must be decoded and validated."""
        from utils.webapp_security import parse_init_data, TelegramUser

        init_data = _build_init_data(_VALID_INIT_DATA_PARAMS, _FAKE_TOKEN)
        parsed = parse_init_data(init_data)

        assert isinstance(parsed.get("user"), TelegramUser)
        assert parsed["user"].id == _SAMPLE_USER["id"]
        assert parsed["user"].first_name == _SAMPLE_USER["first_name"]

    def test_parses_auth_date(self):
        """auth_date field must be present as a string after parsing."""
        from utils.webapp_security import parse_init_data

        init_data = _build_init_data(_VALID_INIT_DATA_PARAMS, _FAKE_TOKEN)
        parsed = parse_init_data(init_data)
        assert "auth_date" in parsed

    def test_empty_returns_empty_dict(self):
        """Empty input must yield an empty dict without raising."""
        from utils.webapp_security import parse_init_data

        result = parse_init_data("")
        assert result == {}

    def test_invalid_user_json_removed(self):
        """If the embedded 'user' field is not valid JSON, it is dropped."""
        from utils.webapp_security import parse_init_data

        params = dict(_VALID_INIT_DATA_PARAMS)
        params["user"] = "NOT_VALID_JSON"
        init_data = _build_init_data(params, _FAKE_TOKEN)
        parsed = parse_init_data(init_data)
        # 'user' must be absent, not a raw string
        assert "user" not in parsed


# ---------------------------------------------------------------------------
# Integration-style tests for _get_validated_init_data behaviour
# ---------------------------------------------------------------------------

class TestGetValidatedInitData:
    """
    Lightweight tests for the _get_validated_init_data helper in webhook_server.

    We build a minimal mock Request instead of spinning up a full FastAPI app
    so the tests remain fast and dependency-free.
    """

    def _make_request(self, init_data: str = "", use_header: bool = True):
        """Return a MagicMock that looks like a FastAPI Request."""
        req = MagicMock()
        req.headers = {"X-Telegram-Init-Data": init_data} if (use_header and init_data) else {}
        req.query_params = MagicMock()
        req.query_params.get = MagicMock(return_value=init_data if not use_header else None)
        req.url = MagicMock()
        req.url.query = ""
        return req

    def test_valid_init_data_accepted(self):
        """A correctly signed initData in the request header must be accepted."""
        from webhook_server import _get_validated_init_data

        init_data = _build_init_data(_VALID_INIT_DATA_PARAMS, _FAKE_TOKEN)
        req = self._make_request(init_data)

        with patch("webhook_server.Config") as mock_cfg:
            mock_cfg.BOT_TOKENS = [_FAKE_TOKEN]
            result_data, bot_idx = _get_validated_init_data(req, allow_preview=False)

        assert result_data == init_data
        assert bot_idx == 0

    def test_missing_init_data_rejected_when_required(self):
        """No initData on a non-preview endpoint must raise HTTPException 401."""
        from fastapi import HTTPException
        from webhook_server import _get_validated_init_data

        req = self._make_request("")  # no initData

        with (
            patch("webhook_server.Config") as mock_cfg,
            pytest.raises(HTTPException) as exc_info,
        ):
            mock_cfg.BOT_TOKENS = [_FAKE_TOKEN]
            _get_validated_init_data(req, allow_preview=False)

        assert exc_info.value.status_code == 401

    def test_missing_init_data_allowed_in_preview_mode(self):
        """Missing initData on a preview endpoint must return ("", -1) without raising."""
        from webhook_server import _get_validated_init_data

        req = self._make_request("")  # no initData

        with patch("webhook_server.Config") as mock_cfg:
            mock_cfg.BOT_TOKENS = [_FAKE_TOKEN]
            result_data, bot_idx = _get_validated_init_data(req, allow_preview=True)

        assert result_data == ""
        assert bot_idx == -1

    def test_invalid_init_data_rejected(self):
        """Tampered initData must be rejected even when allow_preview=True."""
        from fastapi import HTTPException
        from webhook_server import _get_validated_init_data

        req = self._make_request("query_id=x&hash=badhash")

        with (
            patch("webhook_server.Config") as mock_cfg,
            pytest.raises(HTTPException) as exc_info,
        ):
            mock_cfg.BOT_TOKENS = [_FAKE_TOKEN]
            _get_validated_init_data(req, allow_preview=False)

        assert exc_info.value.status_code == 401

    def test_authenticated_user_helper_returns_initdata_user(self):
        """User-specific API helpers must bind identity to signed initData."""
        from webhook_server import _get_authenticated_webapp_user

        init_data = _build_init_data(_VALID_INIT_DATA_PARAMS, _FAKE_TOKEN)
        req = self._make_request(init_data)

        with patch("webhook_server.Config") as mock_cfg:
            mock_cfg.BOT_TOKENS = [_FAKE_TOKEN]
            user, bot_idx, returned_init_data = _get_authenticated_webapp_user(req)

        assert user.id == _SAMPLE_USER["id"]
        assert bot_idx == 0
        assert returned_init_data == init_data

    def test_same_user_guard_rejects_other_user_id(self):
        """Authenticated WebApp users must not mutate another user's state."""
        from fastapi import HTTPException
        from utils.webapp_security import TelegramUser
        from webhook_server import _require_same_webapp_user

        user = TelegramUser.model_validate(_SAMPLE_USER)

        with pytest.raises(HTTPException) as exc_info:
            _require_same_webapp_user(user.id + 1, user)

        assert exc_info.value.status_code == 403
