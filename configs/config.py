"""
Configuration management using python-decouple for secure environment variable handling.
"""
import logging
import os
import urllib.parse
from pathlib import Path
from typing import List, Optional
from decouple import config, Csv, UndefinedValueError

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Custom exception for configuration errors."""
    pass


class ConfigClass:
    """Centralized configuration class for the bot."""
    
    def __init__(self):
        """Initialize and load configuration from environment variables."""
        self._loaded = False
        self.BOT_TOKEN: Optional[str] = None
        self.BOT_TOKENS: List[str] = []  # Support for multiple bot tokens
        self.ADMIN_IDS: List[int] = []
        self.CHANNEL_ID: Optional[int] = None
        self.CHANNEL_USERNAME: Optional[str] = None
        self.DB_PATH: str = 'catalog.db'
        self.USE_WEBHOOK: bool = False
        self.WEBHOOK_URL: Optional[str] = None
        self.WEB_APP_URL: Optional[str] = None
        self.STOREFRONT_DOMAIN: Optional[str] = None
        self.ORDER_CONTACT: Optional[str] = None  # Configurable order contact
        self.ADMIN_DASHBOARD_ENABLED: bool = False
        self.ADMIN_DASHBOARD_TOKEN: Optional[str] = None
        self.SEO_AUTO_LEARN_ENABLED: bool = True
        self.SEO_AUTO_LEARN_INTERVAL_SECONDS: int = 900
        self.SEO_AUTO_LEARN_LOOKBACK_HOURS: int = 720
        self.SEO_AUTO_LEARN_MIN_COUNT: int = 1
        self.SEO_AUTO_LEARN_SCORE: int = 84
        self.SEO_AUTO_LEARN_MAX_PER_RUN: int = 60
        self.SEO_AUTO_LEARN_MAX_MATCHED_COUNT: int = 5
        self.SEO_EXTERNAL_SIGNAL_SCORE: int = 86
        self.SEO_SIDE_NOTE_LIMIT: int = 10
        self.SEO_TRENDS_ENABLED: bool = True
        self.SEO_TRENDS_INTERVAL_SECONDS: int = 21600
        self.SEO_TRENDS_LIMIT_PER_GEO: int = 10
        self.SEO_TRENDS_MAX_TERMS_PER_RUN: int = 50
        self.SEARCH_CONSOLE_ENABLED: bool = False
        self.SEARCH_CONSOLE_SITE_URL: Optional[str] = None
        self.SEARCH_CONSOLE_SERVICE_ACCOUNT_JSON: Optional[str] = None
        self.SEARCH_CONSOLE_SERVICE_ACCOUNT_FILE: Optional[str] = None
        self.SEARCH_CONSOLE_OAUTH_CLIENT_JSON: Optional[str] = None
        self.SEARCH_CONSOLE_OAUTH_CLIENT_FILE: Optional[str] = None
        self.SEARCH_CONSOLE_OAUTH_TOKEN_JSON: Optional[str] = None
        self.SEARCH_CONSOLE_OAUTH_TOKEN_FILE: Optional[str] = None
        self.SEARCH_CONSOLE_LOOKBACK_DAYS: int = 30
        self.SEARCH_CONSOLE_ROW_LIMIT: int = 250
        self.SEARCH_CONSOLE_SITEMAP_SUBMIT_ENABLED: bool = False
        self.SEARCH_CONSOLE_SITEMAP_URL: Optional[str] = None
        self.SEARCH_CONSOLE_SITEMAP_INTERVAL_SECONDS: int = 21600
        self.SEARCH_CONSOLE_SITEMAP_SUBMIT_ON_STARTUP: bool = True
        self.EXTERNAL_SEO_SIGNALS_FILE: Optional[str] = None
        self.INDEXNOW_ENABLED: bool = True
        self.INDEXNOW_ENDPOINT: str = "https://api.indexnow.org/indexnow"
        self.INDEXNOW_KEY_FILE: Optional[str] = None
        self.INDEXNOW_INTERVAL_SECONDS: int = 900
        self.INDEXNOW_BATCH_SIZE: int = 100
        self.INDEXNOW_BOOTSTRAP_ON_STARTUP: bool = True
        self.INDEXNOW_BOOTSTRAP_LIMIT: int = 500
        self.INDEXNOW_CHANGE_LOOKBACK_HOURS: int = 48
        self._load_config()

    @staticmethod
    def _clean_url(value: Optional[str], *, strip_trailing_slash: bool = True) -> Optional[str]:
        """Trim deployment URLs so reused Render configs do not create // paths."""
        if not value:
            return None
        cleaned = str(value).strip()
        if not cleaned:
            return None
        return cleaned.rstrip('/') if strip_trailing_slash else cleaned
    
    def _load_config(self):
        """Load configuration from environment variables."""
        if self._loaded:
            return
        
        # Load BOT_TOKEN (primary bot)
        try:
            self.BOT_TOKEN = config('BOT_TOKEN')
        except UndefinedValueError:
            self.BOT_TOKEN = None

        # BOT_TOKENS (comma-separated list) takes priority over individual token vars.
        # This is useful when all tokens are known upfront or managed by an orchestrator.
        try:
            bot_tokens_raw = config('BOT_TOKENS')
            self.BOT_TOKENS = [t.strip() for t in bot_tokens_raw.split(',') if t.strip()]
            self.BOT_TOKEN = self.BOT_TOKENS[0] if self.BOT_TOKENS else None
            if not self.BOT_TOKENS:
                logger.warning("BOT_TOKENS is set but contains no valid tokens (check for extra commas or whitespace).")
            self._load_remaining_config()
            return
        except UndefinedValueError:
            pass

        # Build BOT_TOKENS from individual env vars:
        #   index 0 = BOT_TOKEN (primary bot)
        #   index 1 = BOT_TOKEN_1, index 2 = BOT_TOKEN_2, … (additional bots)
        # Gaps in numbering are skipped but do NOT stop scanning; later indices
        # are still collected so the resulting list stays as compact as possible.
        # The startup log (see validate()) prints each index → token-id so operators
        # can confirm the mapping matches what was registered with Telegram.
        bot_tokens = list(self.BOT_TOKENS)
        if self.BOT_TOKEN and self.BOT_TOKEN not in bot_tokens:
            bot_tokens.insert(0, self.BOT_TOKEN)

        # Support BOT_TOKEN_ (trailing underscore, no number) as an additional token
        try:
            token = config('BOT_TOKEN_')
            if token and token not in bot_tokens:
                bot_tokens.append(token)
        except UndefinedValueError:
            pass

        # Scan BOT_TOKEN_1 … BOT_TOKEN_10; continue past gaps (don't break early)
        for i in range(1, 11):
            try:
                token = config(f'BOT_TOKEN_{i}')
                if token and token not in bot_tokens:
                    bot_tokens.append(token)
            except UndefinedValueError:
                continue

        self.BOT_TOKENS = bot_tokens

        self._load_remaining_config()

    def _load_remaining_config(self):
        """Load non-token configuration after the runtime token source is resolved."""
        # Load ADMIN_IDS
        try:
            self.ADMIN_IDS = config('ADMIN_IDS', cast=Csv(int))
        except (UndefinedValueError, ValueError):
            self.ADMIN_IDS = []
        
        # Load CHANNEL_ID
        try:
            self.CHANNEL_ID = config('CHANNEL_ID', default=None, cast=lambda x: int(x) if x else None)
        except (ValueError, TypeError):
            self.CHANNEL_ID = None
        
        # Load optional configurations
        self.CHANNEL_USERNAME = config('CHANNEL_USERNAME', default=None)
        default_db_path = '/data/catalog.db' if Path('/data').exists() else 'catalog.db'
        self.DB_PATH = config('DB_PATH', default=default_db_path)
        self.USE_WEBHOOK = config('USE_WEBHOOK', default=False, cast=bool)
        self.WEBHOOK_URL = self._clean_url(
            config('WEBHOOK_URL', default=None) or os.getenv('RENDER_EXTERNAL_URL')
        )
        self.ORDER_CONTACT = config('ORDER_CONTACT', default=None)
        self.ADMIN_DASHBOARD_ENABLED = config('ADMIN_DASHBOARD_ENABLED', default=False, cast=bool)
        self.ADMIN_DASHBOARD_TOKEN = config('ADMIN_DASHBOARD_TOKEN', default=None)
        self.SEO_AUTO_LEARN_ENABLED = config('SEO_AUTO_LEARN_ENABLED', default=True, cast=bool)
        self.SEO_AUTO_LEARN_INTERVAL_SECONDS = max(
            300,
            config('SEO_AUTO_LEARN_INTERVAL_SECONDS', default=900, cast=int),
        )
        self.SEO_AUTO_LEARN_LOOKBACK_HOURS = max(
            1,
            config('SEO_AUTO_LEARN_LOOKBACK_HOURS', default=720, cast=int),
        )
        self.SEO_AUTO_LEARN_MIN_COUNT = max(
            1,
            config('SEO_AUTO_LEARN_MIN_COUNT', default=1, cast=int),
        )
        self.SEO_AUTO_LEARN_SCORE = max(
            70,
            min(100, config('SEO_AUTO_LEARN_SCORE', default=84, cast=int)),
        )
        self.SEO_AUTO_LEARN_MAX_PER_RUN = max(
            1,
            min(100, config('SEO_AUTO_LEARN_MAX_PER_RUN', default=60, cast=int)),
        )
        self.SEO_AUTO_LEARN_MAX_MATCHED_COUNT = max(
            0,
            min(25, config('SEO_AUTO_LEARN_MAX_MATCHED_COUNT', default=5, cast=int)),
        )
        self.SEO_EXTERNAL_SIGNAL_SCORE = max(
            75,
            min(100, config('SEO_EXTERNAL_SIGNAL_SCORE', default=86, cast=int)),
        )
        self.SEO_SIDE_NOTE_LIMIT = max(
            3,
            min(25, config('SEO_SIDE_NOTE_LIMIT', default=10, cast=int)),
        )
        self.SEO_TRENDS_ENABLED = config('SEO_TRENDS_ENABLED', default=True, cast=bool)
        self.SEO_TRENDS_INTERVAL_SECONDS = max(
            21600,
            config('SEO_TRENDS_INTERVAL_SECONDS', default=21600, cast=int),
        )
        self.SEO_TRENDS_LIMIT_PER_GEO = max(
            3,
            min(25, config('SEO_TRENDS_LIMIT_PER_GEO', default=10, cast=int)),
        )
        self.SEO_TRENDS_MAX_TERMS_PER_RUN = max(
            10,
            min(200, config('SEO_TRENDS_MAX_TERMS_PER_RUN', default=50, cast=int)),
        )
        self.SEARCH_CONSOLE_SERVICE_ACCOUNT_JSON = config('SEARCH_CONSOLE_SERVICE_ACCOUNT_JSON', default=None)
        default_search_console_file = '/data/search-console-service-account.json'
        configured_search_console_file = config('SEARCH_CONSOLE_SERVICE_ACCOUNT_FILE', default=None)
        self.SEARCH_CONSOLE_SERVICE_ACCOUNT_FILE = (
            configured_search_console_file
            or (default_search_console_file if Path(default_search_console_file).exists() else None)
        )
        self.SEARCH_CONSOLE_OAUTH_CLIENT_JSON = config('SEARCH_CONSOLE_OAUTH_CLIENT_JSON', default=None)
        default_oauth_client_file = '/data/search-console-oauth-client.json'
        configured_oauth_client_file = config('SEARCH_CONSOLE_OAUTH_CLIENT_FILE', default=None)
        self.SEARCH_CONSOLE_OAUTH_CLIENT_FILE = (
            configured_oauth_client_file
            or (default_oauth_client_file if Path(default_oauth_client_file).exists() else None)
        )
        self.SEARCH_CONSOLE_OAUTH_TOKEN_JSON = config('SEARCH_CONSOLE_OAUTH_TOKEN_JSON', default=None)
        default_oauth_token_file = '/data/search-console-oauth-token.json'
        configured_oauth_token_file = config('SEARCH_CONSOLE_OAUTH_TOKEN_FILE', default=None)
        self.SEARCH_CONSOLE_OAUTH_TOKEN_FILE = (
            configured_oauth_token_file
            or (default_oauth_token_file if Path(default_oauth_token_file).exists() else None)
        )
        search_console_has_credentials = bool(
            self.SEARCH_CONSOLE_SERVICE_ACCOUNT_JSON
            or self.SEARCH_CONSOLE_SERVICE_ACCOUNT_FILE
            or (
                (self.SEARCH_CONSOLE_OAUTH_CLIENT_JSON or self.SEARCH_CONSOLE_OAUTH_CLIENT_FILE)
                and (self.SEARCH_CONSOLE_OAUTH_TOKEN_JSON or self.SEARCH_CONSOLE_OAUTH_TOKEN_FILE)
            )
        )
        self.SEARCH_CONSOLE_ENABLED = config(
            'SEARCH_CONSOLE_ENABLED',
            default=search_console_has_credentials,
            cast=bool,
        )
        self.SEARCH_CONSOLE_SITE_URL = config('SEARCH_CONSOLE_SITE_URL', default=None)
        self.SEARCH_CONSOLE_LOOKBACK_DAYS = max(
            1,
            min(30, config('SEARCH_CONSOLE_LOOKBACK_DAYS', default=30, cast=int)),
        )
        self.SEARCH_CONSOLE_ROW_LIMIT = max(
            10,
            min(2500, config('SEARCH_CONSOLE_ROW_LIMIT', default=250, cast=int)),
        )
        self.EXTERNAL_SEO_SIGNALS_FILE = config('EXTERNAL_SEO_SIGNALS_FILE', default=None)
        
        # Load WEB_APP_URL - if not set, derive from WEBHOOK_URL (existing behaviour)
        self.WEB_APP_URL = self._clean_url(config('WEB_APP_URL', default=None), strip_trailing_slash=False)
        if not self.WEB_APP_URL and self.WEBHOOK_URL:
            self.WEB_APP_URL = self.WEBHOOK_URL + '/webapp/index.html'

        # Load STOREFRONT_DOMAIN - canonical public origin for product deep links.
        # Falls back to CUSTOM_DOMAIN (legacy alias), then derives the origin
        # (scheme://host) from WEB_APP_URL or WEBHOOK_URL so that deployments
        # where only WEBHOOK_URL is set still get a correct storefront origin.
        self.STOREFRONT_DOMAIN = self._clean_url(
            config('STOREFRONT_DOMAIN', default=None)
            or config('CUSTOM_DOMAIN', default=None)
        )
        if not self.STOREFRONT_DOMAIN:
            derived: Optional[str] = None
            try:
                if self.WEB_APP_URL:
                    p = urllib.parse.urlparse(self.WEB_APP_URL)
                    if p.scheme and p.netloc:
                        derived = f"{p.scheme}://{p.netloc}"
                if not derived and self.WEBHOOK_URL:
                    p = urllib.parse.urlparse(self.WEBHOOK_URL)
                    if p.scheme and p.netloc:
                        derived = f"{p.scheme}://{p.netloc}"
            except Exception:  # urlparse is safe, but guard against unexpected input
                derived = None
            self.STOREFRONT_DOMAIN = derived

        if not self.SEARCH_CONSOLE_SITE_URL and self.STOREFRONT_DOMAIN:
            try:
                p = urllib.parse.urlparse(self.STOREFRONT_DOMAIN)
                host = (p.netloc or p.path).split("@")[-1].split(":")[0].strip().lower()
                if host.startswith("www."):
                    host = host[4:]
                self.SEARCH_CONSOLE_SITE_URL = f"sc-domain:{host}" if host else self.STOREFRONT_DOMAIN.rstrip('/') + '/'
            except Exception:
                self.SEARCH_CONSOLE_SITE_URL = self.STOREFRONT_DOMAIN.rstrip('/') + '/'

        # Ensure WEB_APP_URL exists (so Telegram MenuButton still works) even
        # when it was not derivable from WEBHOOK_URL above.
        if not self.WEB_APP_URL and self.STOREFRONT_DOMAIN:
            self.WEB_APP_URL = self.STOREFRONT_DOMAIN.rstrip('/') + '/webapp/index.html'

        configured_sitemap_url = config('SEARCH_CONSOLE_SITEMAP_URL', default=None)
        self.SEARCH_CONSOLE_SITEMAP_URL = (
            self._clean_url(configured_sitemap_url, strip_trailing_slash=False)
            if configured_sitemap_url
            else None
        )
        if not self.SEARCH_CONSOLE_SITEMAP_URL and self.STOREFRONT_DOMAIN:
            self.SEARCH_CONSOLE_SITEMAP_URL = self.STOREFRONT_DOMAIN.rstrip('/') + '/sitemap.xml'
        self.SEARCH_CONSOLE_SITEMAP_SUBMIT_ENABLED = config(
            'SEARCH_CONSOLE_SITEMAP_SUBMIT_ENABLED',
            default=bool(self.SEARCH_CONSOLE_ENABLED and self.SEARCH_CONSOLE_SITEMAP_URL),
            cast=bool,
        )
        self.SEARCH_CONSOLE_SITEMAP_INTERVAL_SECONDS = max(
            900,
            min(24 * 3600, config('SEARCH_CONSOLE_SITEMAP_INTERVAL_SECONDS', default=21600, cast=int)),
        )
        self.SEARCH_CONSOLE_SITEMAP_SUBMIT_ON_STARTUP = config(
            'SEARCH_CONSOLE_SITEMAP_SUBMIT_ON_STARTUP',
            default=True,
            cast=bool,
        )

        default_indexnow_key_file = (
            '/data/indexnow.key'
            if Path('/data').exists()
            else str((Path(self.DB_PATH).expanduser().resolve().parent / 'indexnow.key'))
        )
        self.INDEXNOW_ENABLED = config(
            'INDEXNOW_ENABLED',
            default=bool(self.STOREFRONT_DOMAIN),
            cast=bool,
        )
        self.INDEXNOW_ENDPOINT = config(
            'INDEXNOW_ENDPOINT',
            default='https://api.indexnow.org/indexnow',
        )
        self.INDEXNOW_KEY_FILE = config('INDEXNOW_KEY_FILE', default=default_indexnow_key_file)
        self.INDEXNOW_INTERVAL_SECONDS = max(
            300,
            min(24 * 3600, config('INDEXNOW_INTERVAL_SECONDS', default=900, cast=int)),
        )
        self.INDEXNOW_BATCH_SIZE = max(
            1,
            min(1000, config('INDEXNOW_BATCH_SIZE', default=100, cast=int)),
        )
        self.INDEXNOW_BOOTSTRAP_ON_STARTUP = config(
            'INDEXNOW_BOOTSTRAP_ON_STARTUP',
            default=True,
            cast=bool,
        )
        self.INDEXNOW_BOOTSTRAP_LIMIT = max(
            0,
            min(1000, config('INDEXNOW_BOOTSTRAP_LIMIT', default=500, cast=int)),
        )
        self.INDEXNOW_CHANGE_LOOKBACK_HOURS = max(
            1,
            min(168, config('INDEXNOW_CHANGE_LOOKBACK_HOURS', default=48, cast=int)),
        )

        self._loaded = True
    
    def validate(self) -> None:
        """
        Validate that all required environment variables are set correctly.
        Raises ConfigError with clear messages if validation fails.
        """
        errors = []
        
        # Validate BOT_TOKEN(s)
        if not self.BOT_TOKEN and not self.BOT_TOKENS:
            errors.append("BOT_TOKEN is required. Get it from @BotFather on Telegram.")
        
        # Validate ADMIN_IDS
        if not self.ADMIN_IDS:
            errors.append("ADMIN_IDS is required. Add at least one admin user ID. Get your ID from @userinfobot.")
        
        # Channel monitoring is optional: without it the bot still starts, but
        # product ingestion from a source channel is disabled.
        if not self.CHANNEL_ID and not self.CHANNEL_USERNAME:
            logger.warning(
                "No CHANNEL_ID or CHANNEL_USERNAME set. The bot will start, "
                "but automatic product ingestion from a channel is disabled."
            )
        
        # Validate webhook configuration
        if self.USE_WEBHOOK and not self.WEBHOOK_URL:
            errors.append(
                "WEBHOOK_URL is required when USE_WEBHOOK is true. "
                "On Render, set it to your service origin, e.g. https://your-app.onrender.com"
            )
        if self.WEBHOOK_URL:
            parsed_webhook = urllib.parse.urlparse(self.WEBHOOK_URL)
            if parsed_webhook.path not in {"", "/"}:
                logger.warning(
                    "WEBHOOK_URL should be the site origin only, not a webhook path: %s",
                    self.WEBHOOK_URL,
                )
        
        # If there are any errors, raise a comprehensive error message
        if errors:
            error_message = "\n❌ Configuration Validation Failed:\n\n" + "\n".join(f"  • {error}" for error in errors)
            error_message += "\n\n💡 Please check your .env file and ensure all required variables are set correctly."
            error_message += "\n📖 See .env.example for reference."
            raise ConfigError(error_message)
        
        logger.info("✅ Configuration validation successful")
        if self.BOT_TOKEN:
            logger.info(f"  - Primary bot token: {'*' * 10}{self.BOT_TOKEN[-5:]}")
        logger.info(f"  - Total bot instances: {len(self.BOT_TOKENS)}")
        for idx, token in enumerate(self.BOT_TOKENS):
            token_id = token.split(':')[0] if ':' in token else 'INVALID_FORMAT'
            webhook_path = '/webhook' if idx == 0 else f'/webhook/{idx}'
            logger.info(f"    - Bot {idx} (token_id={token_id}, path={webhook_path})")
        logger.info(f"  - Admin IDs: {len(self.ADMIN_IDS)} admin(s) configured")
        logger.info(f"  - Channel: {self.CHANNEL_ID or self.CHANNEL_USERNAME}")
        logger.info(f"  - Database: {self.DB_PATH}")
        logger.info(f"  - Webhook mode: {self.USE_WEBHOOK}")
        logger.info(f"  - Webhook URL: {self.WEBHOOK_URL or 'not configured'}")
        logger.info(f"  - WebApp URL: {self.WEB_APP_URL or 'not configured'}")
        logger.info(f"  - Storefront domain: {self.STOREFRONT_DOMAIN or 'not configured'}")


# Create a singleton instance
Config = ConfigClass()
