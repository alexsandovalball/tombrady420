"""
Regression tests for configuration loading and search-query preparation.
"""


class TestBotTokensConfig:
    def test_bot_tokens_does_not_skip_other_config(self, monkeypatch):
        from configs.config import ConfigClass

        monkeypatch.setenv("BOT_TOKENS", "111:AAA,222:BBB")
        monkeypatch.setenv("ADMIN_IDS", "123,456")
        monkeypatch.setenv("CHANNEL_ID", "-1001234567890")
        monkeypatch.setenv("USE_WEBHOOK", "true")
        monkeypatch.setenv("WEBHOOK_URL", "https://example.test")
        monkeypatch.setenv("DB_PATH", "custom.db")

        cfg = ConfigClass()

        assert cfg.BOT_TOKEN == "111:AAA"
        assert cfg.BOT_TOKENS == ["111:AAA", "222:BBB"]
        assert cfg.ADMIN_IDS == [123, 456]
        assert cfg.CHANNEL_ID == -1001234567890
        assert cfg.USE_WEBHOOK is True
        assert cfg.WEBHOOK_URL == "https://example.test"
        assert cfg.WEB_APP_URL == "https://example.test/webapp/index.html"
        assert cfg.DB_PATH == "custom.db"

    def test_bot_tokens_excludes_stale_individual_token_vars(self, monkeypatch):
        from configs.config import ConfigClass

        monkeypatch.setenv("BOT_TOKENS", "111:AAA,222:BBB")
        monkeypatch.setenv("BOT_TOKEN", "stale-primary")
        monkeypatch.setenv("BOT_TOKEN_", "stale-trailing")
        monkeypatch.setenv("BOT_TOKEN_1", "stale-one")
        monkeypatch.setenv("BOT_TOKEN_9", "stale-nine")
        monkeypatch.setenv("ADMIN_IDS", "123")
        monkeypatch.setenv("CHANNEL_ID", "-1001234567890")

        cfg = ConfigClass()

        assert cfg.BOT_TOKENS == ["111:AAA", "222:BBB"]
        assert cfg.BOT_TOKEN == "111:AAA"


class TestSearchQueryBuilder:
    def test_builds_prefix_terms_from_user_query(self):
        from database import Database

        assert Database._build_fts_query("live resin!") == "live* resin*"

    def test_empty_search_query_returns_none(self):
        from database import Database

        assert Database._build_fts_query("!!!") is None
