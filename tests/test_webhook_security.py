"""
Tests for webhook security improvements:
- WebhookIPRateLimiter (token-bucket, per-IP, non-blocking)
- Secret token verification in process_webhook_request
- Rate limiting middleware in process_webhook_request
"""
import asyncio
import secrets
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from webhook_server import WebhookIPRateLimiter


# ---------------------------------------------------------------------------
# WebhookIPRateLimiter unit tests
# ---------------------------------------------------------------------------

class TestWebhookIPRateLimiter:
    def test_invalid_rate_raises(self):
        with pytest.raises(ValueError):
            WebhookIPRateLimiter(max_requests=0, window_seconds=60)

    def test_invalid_window_raises(self):
        with pytest.raises(ValueError):
            WebhookIPRateLimiter(max_requests=100, window_seconds=0)

    @pytest.mark.asyncio
    async def test_first_request_always_allowed(self):
        limiter = WebhookIPRateLimiter(max_requests=5, window_seconds=60)
        assert await limiter.is_allowed("1.2.3.4") is True

    @pytest.mark.asyncio
    async def test_burst_requests_all_allowed(self):
        burst = 5
        limiter = WebhookIPRateLimiter(max_requests=burst, window_seconds=60)
        results = [await limiter.is_allowed("10.0.0.1") for _ in range(burst)]
        assert all(results), "All burst requests should be allowed"

    @pytest.mark.asyncio
    async def test_request_exceeding_burst_denied(self):
        burst = 3
        limiter = WebhookIPRateLimiter(max_requests=burst, window_seconds=60)
        # Drain the burst
        for _ in range(burst):
            await limiter.is_allowed("10.0.0.2")
        # Next request should be denied
        assert await limiter.is_allowed("10.0.0.2") is False

    @pytest.mark.asyncio
    async def test_different_ips_have_independent_buckets(self):
        limiter = WebhookIPRateLimiter(max_requests=1, window_seconds=60)
        # Drain bucket for IP A
        await limiter.is_allowed("192.168.1.1")
        assert await limiter.is_allowed("192.168.1.1") is False
        # IP B should still be allowed
        assert await limiter.is_allowed("192.168.1.2") is True

    @pytest.mark.asyncio
    async def test_tokens_refill_over_time(self):
        # 1 request per 1-second window => 1 token/sec refill
        limiter = WebhookIPRateLimiter(max_requests=1, window_seconds=1)
        await limiter.is_allowed("10.0.0.3")  # drain
        assert await limiter.is_allowed("10.0.0.3") is False
        await asyncio.sleep(1.1)  # wait just over 1 second for refill
        assert await limiter.is_allowed("10.0.0.3") is True

    @pytest.mark.asyncio
    async def test_concurrent_requests_same_ip(self):
        """Concurrent requests from the same IP must not bypass the burst cap."""
        burst = 10
        limiter = WebhookIPRateLimiter(max_requests=burst, window_seconds=60)
        results = await asyncio.gather(
            *[limiter.is_allowed("10.0.0.4") for _ in range(burst * 2)]
        )
        allowed = sum(1 for r in results if r)
        # The lock serialises all coroutines so exactly `burst` tokens are consumed
        assert allowed == burst

    @pytest.mark.asyncio
    async def test_lru_eviction_at_max_buckets(self):
        """When _MAX_BUCKETS is hit, the oldest entry is evicted."""
        limiter = WebhookIPRateLimiter(max_requests=100, window_seconds=60)
        limiter._MAX_BUCKETS = 3
        for i in range(3):
            await limiter.is_allowed(f"10.0.0.{i}")
        assert len(limiter._buckets) == 3
        # Adding a 4th IP should evict the oldest
        await limiter.is_allowed("10.0.0.99")
        assert len(limiter._buckets) == 3
        assert "10.0.0.0" not in limiter._buckets


# ---------------------------------------------------------------------------
# process_webhook_request integration tests (mocked FastAPI app)
# ---------------------------------------------------------------------------

def _make_mock_request(
    ip: str = "127.0.0.1",
    secret_token_header: str = None,
    body: dict = None,
    bot_apps=None,
    secret_tokens=None,
    rate_limiter=None,
    forwarded_for: str = None,
    real_ip: str = None,
):
    """Build a minimal mock Request suitable for process_webhook_request."""
    request = MagicMock()
    request.client = MagicMock()
    request.client.host = ip

    # Headers — plain dict supports .get() out of the box
    headers = {}
    if secret_token_header is not None:
        headers["X-Telegram-Bot-Api-Secret-Token"] = secret_token_header
    if forwarded_for is not None:
        headers["X-Forwarded-For"] = forwarded_for
    if real_ip is not None:
        headers["X-Real-IP"] = real_ip
    request.headers = headers

    # JSON body
    request.json = AsyncMock(return_value=body or {"update_id": 1})

    # app.state
    state = MagicMock()
    state.bot_apps = bot_apps if bot_apps is not None else []
    state.webhook_secret_tokens = secret_tokens if secret_tokens is not None else []
    state.webhook_rate_limiter = rate_limiter
    request.app = MagicMock()
    request.app.state = state

    return request


@pytest.mark.asyncio
async def test_rate_limit_returns_429():
    """When the rate limiter denies the request, the endpoint returns 429."""
    from webhook_server import process_webhook_request

    limiter = MagicMock()
    limiter.is_allowed = AsyncMock(return_value=False)

    request = _make_mock_request(rate_limiter=limiter)
    response = await process_webhook_request(request, bot_index=0)
    assert response.status_code == 429


@pytest.mark.asyncio
async def test_valid_secret_token_passes():
    """A request with the correct secret token is processed normally."""
    from webhook_server import process_webhook_request
    from fastapi import status

    token = secrets.token_hex(16)

    # Build a minimal mock bot_app that successfully processes the update
    mock_bot_app = MagicMock()
    mock_bot_app.process_update = AsyncMock()

    limiter = MagicMock()
    limiter.is_allowed = AsyncMock(return_value=True)

    with patch("webhook_server.Update") as mock_update_cls:
        mock_update = MagicMock()
        mock_update.message = None
        mock_update.callback_query = None
        mock_update.channel_post = None
        mock_update.update_id = 1
        mock_update_cls.de_json.return_value = mock_update

        request = _make_mock_request(
            secret_token_header=token,
            bot_apps=[mock_bot_app],
            secret_tokens=[token],
            rate_limiter=limiter,
        )
        response = await process_webhook_request(request, bot_index=0)

    assert response.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_missing_secret_token_returns_401():
    """A request with no secret token header is rejected with 401."""
    from webhook_server import process_webhook_request

    token = secrets.token_hex(16)
    limiter = MagicMock()
    limiter.is_allowed = AsyncMock(return_value=True)

    request = _make_mock_request(
        secret_token_header=None,  # header absent
        bot_apps=[MagicMock()],
        secret_tokens=[token],
        rate_limiter=limiter,
    )
    response = await process_webhook_request(request, bot_index=0)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_wrong_secret_token_returns_401():
    """A request with an incorrect secret token is rejected with 401."""
    from webhook_server import process_webhook_request

    token = secrets.token_hex(16)
    limiter = MagicMock()
    limiter.is_allowed = AsyncMock(return_value=True)

    request = _make_mock_request(
        secret_token_header="wrong_token",
        bot_apps=[MagicMock()],
        secret_tokens=[token],
        rate_limiter=limiter,
    )
    response = await process_webhook_request(request, bot_index=0)
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_no_secret_token_configured_skips_check():
    """If no secret token is set for a bot, verification is skipped (backward compat)."""
    from webhook_server import process_webhook_request
    from fastapi import status

    mock_bot_app = MagicMock()
    mock_bot_app.process_update = AsyncMock()

    limiter = MagicMock()
    limiter.is_allowed = AsyncMock(return_value=True)

    with patch("webhook_server.Update") as mock_update_cls:
        mock_update = MagicMock()
        mock_update.message = None
        mock_update.callback_query = None
        mock_update.channel_post = None
        mock_update.update_id = 1
        mock_update_cls.de_json.return_value = mock_update

        request = _make_mock_request(
            secret_token_header=None,  # no header
            bot_apps=[mock_bot_app],
            secret_tokens=[None],  # no token registered
            rate_limiter=limiter,
        )
        response = await process_webhook_request(request, bot_index=0)

    assert response.status_code == status.HTTP_200_OK


# ---------------------------------------------------------------------------
# Proxy / custom-domain IP forwarding tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_rate_limit_uses_x_forwarded_for():
    """Rate limiter should key on the X-Forwarded-For IP, not the proxy IP."""
    from webhook_server import process_webhook_request

    real_ip = "5.6.7.8"
    proxy_ip = "10.0.0.1"  # the IP seen in request.client.host

    # Limiter that only allows real_ip
    async def ip_checker(ip):
        return ip == real_ip

    limiter = MagicMock()
    limiter.is_allowed = AsyncMock(side_effect=ip_checker)

    # Request arrives via proxy; X-Forwarded-For carries the real Telegram IP
    request = _make_mock_request(
        ip=proxy_ip,
        forwarded_for=real_ip,
        rate_limiter=limiter,
    )
    await process_webhook_request(request, bot_index=0)
    limiter.is_allowed.assert_called_once_with(real_ip)


@pytest.mark.asyncio
async def test_rate_limit_uses_x_real_ip_when_no_forwarded_for():
    """Falls back to X-Real-IP when X-Forwarded-For is absent."""
    from webhook_server import process_webhook_request

    real_ip = "9.10.11.12"
    proxy_ip = "10.0.0.2"

    async def ip_checker(ip):
        return ip == real_ip

    limiter = MagicMock()
    limiter.is_allowed = AsyncMock(side_effect=ip_checker)

    request = _make_mock_request(
        ip=proxy_ip,
        real_ip=real_ip,
        rate_limiter=limiter,
    )
    await process_webhook_request(request, bot_index=0)
    limiter.is_allowed.assert_called_once_with(real_ip)


@pytest.mark.asyncio
async def test_rate_limit_uses_first_ip_in_forwarded_for_chain():
    """With a multi-hop X-Forwarded-For, only the leftmost (original) IP is used."""
    from webhook_server import process_webhook_request

    original_ip = "1.2.3.4"
    forwarded_for = f"{original_ip}, 10.0.0.1, 10.0.0.2"

    async def ip_checker(ip):
        return ip == original_ip

    limiter = MagicMock()
    limiter.is_allowed = AsyncMock(side_effect=ip_checker)

    request = _make_mock_request(
        ip="10.0.0.2",
        forwarded_for=forwarded_for,
        rate_limiter=limiter,
    )
    await process_webhook_request(request, bot_index=0)
    limiter.is_allowed.assert_called_once_with(original_ip)

