"""
Unit tests for SecurityMiddleware and GlobalRateLimiter.

These tests use lightweight ASGI test-client helpers from starlette so we
can exercise the middleware stack without spinning up a full FastAPI app with
database connections.
"""

import pytest

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

# ---------------------------------------------------------------------------
# WordPress / CMS path patterns (mirror production constants from webhook_server)
# ---------------------------------------------------------------------------

_WP_PREFIX_PATTERNS = (
    "/wp-admin",
    "/wp-includes",
    "/wp-content",
    "/wp-login.php",
    "/wp-config.php",
    "/wp-json",
    "/wordpress",
    "/wp1",
    "/wp2",
    "/xmlrpc.php",
    "/setup-config.php",
    "/.well-known/wp-",
    "/blog/wp-",
    "/web/wp-",
    "/website/wp-",
    "/news/wp-",
    "/shop/wp-",
    "/site/wp-",
    "/cms/wp-",
    "/sito/wp-",
    "/test/wp-",
    "/2019/wp-",
    "/2020/wp-",
    "/2021/wp-",
    "/2022/wp-",
)

_WP_SUBSTR_PATTERNS = (
    "wlwmanifest.xml",
)


# ---------------------------------------------------------------------------
# Helpers to build minimal ASGI apps with a single middleware layer
# ---------------------------------------------------------------------------

async def _ok_endpoint(request: Request):
    return PlainTextResponse("OK")


class _SecurityMiddlewareOnly(BaseHTTPMiddleware):
    """Standalone copy of SecurityMiddleware logic for isolated testing."""
    async def dispatch(self, request, call_next):
        path = request.url.path
        path_lower = path.lower()
        blocked = path_lower.startswith(_WP_PREFIX_PATTERNS) or any(
            s in path_lower for s in _WP_SUBSTR_PATTERNS
        )
        if blocked:
            return Response(status_code=410)
        return await call_next(request)


def _make_security_app():
    app = Starlette(routes=[Route("/{path:path}", _ok_endpoint)])
    app.add_middleware(_SecurityMiddlewareOnly)
    return app


def _make_rate_limiter_app(
    *,
    max_requests: int = 5,
    window_seconds: int = 60,
    block_duration: int = 60,
    assets_max_requests: int = 10,
    auto_block_threshold: int = 3,
    auto_block_window: int = 600,
    auto_block_duration: int = 3600,
):
    """Return a Starlette app wrapped with GlobalRateLimiter (short limits for testing)."""
    from middleware.rate_limiter import GlobalRateLimiter

    app = Starlette(routes=[Route("/{path:path}", _ok_endpoint)])
    app.add_middleware(
        GlobalRateLimiter,
        max_requests=max_requests,
        window_seconds=window_seconds,
        block_duration=block_duration,
        assets_max_requests=assets_max_requests,
        auto_block_threshold=auto_block_threshold,
        auto_block_window=auto_block_window,
        auto_block_duration=auto_block_duration,
    )
    return app


# ---------------------------------------------------------------------------
# SecurityMiddleware tests
# ---------------------------------------------------------------------------

class TestSecurityMiddleware:

    def test_normal_path_passes_through(self):
        client = TestClient(_make_security_app(), raise_server_exceptions=False)
        resp = client.get("/")
        assert resp.status_code == 200

    def test_api_path_passes_through(self):
        client = TestClient(_make_security_app(), raise_server_exceptions=False)
        resp = client.get("/api/products")
        assert resp.status_code == 200

    def test_webhook_path_passes_through(self):
        client = TestClient(_make_security_app(), raise_server_exceptions=False)
        resp = client.get("/webhook")
        assert resp.status_code == 200

    @pytest.mark.parametrize("path", [
        "/wp-admin/",
        "/wp-admin/setup-config.php",
        "/wp-includes/wlwmanifest.xml",
        "/wp-login.php",
        "/wp-content/themes/",
        "/wp-config.php",
        "/xmlrpc.php",
        "/xmlrpc.php?rsd",
        "/wordpress/wp-admin/setup-config.php",
        "/setup-config.php",
        "/wp-json/wp/v2/users",
        "/blog/wp-includes/wlwmanifest.xml",
        "/web/wp-includes/wlwmanifest.xml",
        "/shop/wp-login.php",
        "/2020/wp-includes/wlwmanifest.xml",
        "/cms/wp-admin/",
        "/sito/wp-login.php",
        # substring match
        "/some/path/wlwmanifest.xml",
    ])
    def test_wordpress_paths_return_410(self, path):
        client = TestClient(_make_security_app(), raise_server_exceptions=False)
        resp = client.get(path)
        assert resp.status_code == 410, f"Expected 410 for {path!r}, got {resp.status_code}"

    def test_wp_path_case_insensitive(self):
        client = TestClient(_make_security_app(), raise_server_exceptions=False)
        resp = client.get("/WP-ADMIN/setup-config.php")
        assert resp.status_code == 410


# ---------------------------------------------------------------------------
# GlobalRateLimiter tests
# ---------------------------------------------------------------------------

class TestGlobalRateLimiter:

    def test_allows_unlimited_normal_traffic(self):
        """Normal traffic is no longer rate limited - unlimited requests allowed."""
        app = _make_rate_limiter_app(max_requests=5, window_seconds=60)
        client = TestClient(app, raise_server_exceptions=False)
        # Send many requests - all should pass since we removed token bucket limiting
        for _ in range(100):
            resp = client.get("/api/products", headers={"X-Real-IP": "1.2.3.4"})
            assert resp.status_code == 200

    def test_blocks_malicious_probes_immediately(self):
        """Malicious probe paths are immediately auto-blocked."""
        app = _make_rate_limiter_app(auto_block_duration=3600)
        client = TestClient(app, raise_server_exceptions=False)

        # Request for wp-login should be immediately blocked
        resp = client.get("/wp-login.php", headers={"X-Real-IP": "10.0.0.1"})
        assert resp.status_code == 429
        assert "Retry-After" in resp.headers
        # Should have long retry-after due to auto-block
        assert int(resp.headers["Retry-After"]) >= 3600

    @pytest.mark.parametrize("malicious_path", [
        "/wp-admin/",
        "/wp-login.php",
        "/.env",
        "/.git/config",
        "/config.json",
        "/setup.php",
        "/phpinfo.php",
        "/admin.php",
        "/xmlrpc.php",
        "/wp-config.php",
        "/some/path/to/.env",  # substring match
        "/api/config.json",     # substring match
    ])
    def test_malicious_probes_blocked(self, malicious_path):
        """All malicious probe patterns should trigger auto-block."""
        app = _make_rate_limiter_app(auto_block_duration=3600)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(malicious_path, headers={"X-Real-IP": "99.99.99.99"})
        assert resp.status_code == 429, f"Expected 429 for {malicious_path!r}, got {resp.status_code}"
        assert int(resp.headers.get("Retry-After", 0)) >= 3600

    def test_auto_blocked_ip_stays_blocked_for_all_requests(self):
        """Once auto-blocked by a malicious probe, IP is blocked for ALL subsequent requests."""
        app = _make_rate_limiter_app(auto_block_duration=3600)
        client = TestClient(app, raise_server_exceptions=False)
        ip = "5.5.5.5"
        headers = {"X-Real-IP": ip}

        # First: trigger auto-block with malicious probe
        resp = client.get("/.env", headers=headers)
        assert resp.status_code == 429

        # Now try normal requests - should still be blocked
        resp = client.get("/api/products", headers=headers)
        assert resp.status_code == 429

        resp = client.get("/", headers=headers)
        assert resp.status_code == 429

    def test_webhook_endpoint_exempt(self):
        app = _make_rate_limiter_app(max_requests=1, window_seconds=60)
        client = TestClient(app, raise_server_exceptions=False)
        # Send many requests to /webhook – should never be rate limited
        for _ in range(10):
            resp = client.get("/webhook", headers={"X-Real-IP": "2.3.4.5"})
            assert resp.status_code == 200

    def test_webhook_sub_path_exempt(self):
        app = _make_rate_limiter_app(max_requests=1, window_seconds=60)
        client = TestClient(app, raise_server_exceptions=False)
        for _ in range(5):
            resp = client.get("/webhook/0", headers={"X-Real-IP": "2.3.4.6"})
            assert resp.status_code == 200

    def test_normal_assets_not_rate_limited(self):
        """Assets are no longer rate limited - independent buckets removed."""
        app = _make_rate_limiter_app(
            max_requests=1, window_seconds=60,
            assets_max_requests=5,
        )
        client = TestClient(app, raise_server_exceptions=False)
        ip = "3.3.3.3"

        # Both API and assets should allow unlimited requests
        for _ in range(20):
            resp = client.get("/api/x", headers={"X-Real-IP": ip})
            assert resp.status_code == 200

        for _ in range(20):
            resp = client.get("/assets/app.js", headers={"X-Real-IP": ip})
            assert resp.status_code == 200

    def test_different_ips_independent_auto_blocks(self):
        """Different IPs have independent auto-block status."""
        app = _make_rate_limiter_app(auto_block_duration=3600)
        client = TestClient(app, raise_server_exceptions=False)

        # IP A triggers auto-block with malicious probe
        resp_a = client.get("/.env", headers={"X-Real-IP": "6.6.6.6"})
        assert resp_a.status_code == 429

        # IP B should still be allowed for normal requests
        resp_b = client.get("/api/x", headers={"X-Real-IP": "7.7.7.7"})
        assert resp_b.status_code == 200

        # But IP B trying malicious probe also gets blocked
        resp_b2 = client.get("/.git/config", headers={"X-Real-IP": "7.7.7.7"})
        assert resp_b2.status_code == 429

    def test_disabled_when_max_requests_zero(self):
        app = _make_rate_limiter_app(max_requests=0, window_seconds=60)
        client = TestClient(app, raise_server_exceptions=False)
        for _ in range(100):
            resp = client.get("/api/x", headers={"X-Real-IP": "8.8.8.8"})
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# TelegramIPWhitelist tests
# ---------------------------------------------------------------------------

class TestTelegramIPWhitelist:
    """Tests for configs.telegram_ip_whitelist.is_telegram_ip()."""

    def test_known_telegram_ips_match(self):
        from configs.telegram_ip_whitelist import is_telegram_ip
        # 91.108.x.x is a well-known Telegram subnet
        assert is_telegram_ip("91.108.5.11") is True
        assert is_telegram_ip("91.108.5.120") is True
        assert is_telegram_ip("91.108.5.34") is True
        assert is_telegram_ip("149.154.160.1") is True

    def test_non_telegram_ips_excluded(self):
        from configs.telegram_ip_whitelist import is_telegram_ip
        assert is_telegram_ip("8.8.8.8") is False
        assert is_telegram_ip("1.1.1.1") is False
        assert is_telegram_ip("31.13.115.10") is False

    def test_unknown_string_returns_false(self):
        from configs.telegram_ip_whitelist import is_telegram_ip
        assert is_telegram_ip("unknown") is False
        assert is_telegram_ip("") is False
        assert is_telegram_ip("not-an-ip") is False


# ---------------------------------------------------------------------------
# MetricsCollector tests
# ---------------------------------------------------------------------------

class TestMetricsCollector:
    """Tests for utils.metrics.MetricsCollector."""

    def setup_method(self):
        from utils.metrics import MetricsCollector
        self.m = MetricsCollector()

    def test_record_and_snapshot_single(self):
        self.m.record("ep1", 100.0)
        snap = self.m.snapshot()
        assert "ep1" in snap["endpoints"]
        assert snap["endpoints"]["ep1"]["count"] == 1
        assert snap["endpoints"]["ep1"]["avg_ms"] == pytest.approx(100.0)

    def test_avg_computed_correctly(self):
        self.m.record("ep", 100.0)
        self.m.record("ep", 200.0)
        snap = self.m.snapshot()
        assert snap["endpoints"]["ep"]["avg_ms"] == pytest.approx(150.0)

    def test_p95_approximation(self):
        # 20 values: 10 at 100ms, 10 at 900ms → p95 should be in the high bucket
        for _ in range(10):
            self.m.record("ep", 100.0)
        for _ in range(10):
            self.m.record("ep", 900.0)
        snap = self.m.snapshot()
        assert snap["endpoints"]["ep"]["p95_ms"] >= 800.0

    def test_reset_clears_data(self):
        self.m.record("ep", 200.0)
        self.m.reset()
        snap = self.m.snapshot()
        assert snap["endpoints"] == {}

    def test_uptime_positive(self):
        snap = self.m.snapshot()
        assert snap["uptime_seconds"] >= 0.0


# ---------------------------------------------------------------------------
# PIIRedactFilter tests
# ---------------------------------------------------------------------------

class TestPIIRedactFilter:
    """Tests for utils.logging_filters.PIIRedactFilter."""

    def _apply(self, msg: str) -> str:
        import logging
        from utils.logging_filters import PIIRedactFilter
        f = PIIRedactFilter()
        record = logging.LogRecord("t", logging.INFO, "", 0, msg, (), None)
        f.filter(record)
        return record.msg

    def test_user_id_masked(self):
        out = self._apply("user_id=8589411468 event=test")
        assert "8589411468" not in out
        assert "user_id=" in out  # prefix preserved

    def test_username_masked(self):
        out = self._apply("username=caliweed0caliplug0 event=test")
        assert "caliplug0" not in out
        assert "username=c***" in out

    def test_photo_url_masked(self):
        out = self._apply('photo_url="https://t.me/i/userpic/320/xyz.svg"')
        assert "t.me" not in out
        assert "<redacted>" in out

    def test_no_pii_passes_through(self):
        msg = "event=api_call action=load_categories status=success"
        out = self._apply(msg)
        assert out == msg

    def test_multiple_pii_fields_in_one_message(self):
        out = self._apply("user_id=123456 username=alice photo_url=\"http://x.com/a.jpg\"")
        assert "123456" not in out
        assert "alice" not in out  # only first char kept
        assert "x.com" not in out
