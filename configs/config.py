"""
Configuration management using python-decouple for secure environment variable handling.
"""
import logging
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
        self.ADMIN_DASHBOARD_TOKEN: Optional[str] = None
        self.SEO_AUTO_LEARN_ENABLED: bool = True
        self.SEO_AUTO_LEARN_INTERVAL_SECONDS: int = 1800
        self.SEO_AUTO_LEARN_LOOKBACK_HOURS: int = 168
        self.SEO_AUTO_LEARN_MIN_COUNT: int = 1
        self.SEO_AUTO_LEARN_SCORE: int = 92
        self.SEO_AUTO_LEARN_MAX_PER_RUN: int = 25
        self.SEO_EXTERNAL_SIGNAL_SCORE: int = 94
        self.SEO_SIDE_NOTE_LIMIT: int = 10
        self.SEARCH_CONSOLE_ENABLED: bool = False
        self.SEARCH_CONSOLE_SITE_URL: Optional[str] = None
        self.SEARCH_CONSOLE_SERVICE_ACCOUNT_JSON: Optional[str] = None
        self.SEARCH_CONSOLE_SERVICE_ACCOUNT_FILE: Optional[str] = None
        self.SEARCH_CONSOLE_OAUTH_CLIENT_JSON: Optional[str] = None
        self.SEARCH_CONSOLE_OAUTH_CLIENT_FILE: Optional[str] = None
        self.SEARCH_CONSOLE_OAUTH_TOKEN_JSON: Optional[str] = None
        self.SEARCH_CONSOLE_OAUTH_TOKEN_FILE: Optional[str] = None
        self.SEARCH_CONSOLE_LOOKBACK_DAYS: int = 7
        self.SEARCH_CONSOLE_ROW_LIMIT: int = 250
        self.EXTERNAL_SEO_SIGNALS_FILE: Optional[str] = None
        self._load_config()
    
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
        self.DB_PATH = config('DB_PATH', default='catalog.db')
        self.USE_WEBHOOK = config('USE_WEBHOOK', default=False, cast=bool)
        self.WEBHOOK_URL = config('WEBHOOK_URL', default=None)
        self.ORDER_CONTACT = config('ORDER_CONTACT', default=None)
        self.ADMIN_DASHBOARD_TOKEN = config('ADMIN_DASHBOARD_TOKEN', default=None)
        self.SEO_AUTO_LEARN_ENABLED = config('SEO_AUTO_LEARN_ENABLED', default=True, cast=bool)
        self.SEO_AUTO_LEARN_INTERVAL_SECONDS = max(
            300,
            config('SEO_AUTO_LEARN_INTERVAL_SECONDS', default=1800, cast=int),
        )
        self.SEO_AUTO_LEARN_LOOKBACK_HOURS = max(
            1,
            config('SEO_AUTO_LEARN_LOOKBACK_HOURS', default=168, cast=int),
        )
        self.SEO_AUTO_LEARN_MIN_COUNT = max(
            1,
            config('SEO_AUTO_LEARN_MIN_COUNT', default=1, cast=int),
        )
        self.SEO_AUTO_LEARN_SCORE = max(
            70,
            min(100, config('SEO_AUTO_LEARN_SCORE', default=92, cast=int)),
        )
        self.SEO_AUTO_LEARN_MAX_PER_RUN = max(
            1,
            min(100, config('SEO_AUTO_LEARN_MAX_PER_RUN', default=25, cast=int)),
        )
        self.SEO_EXTERNAL_SIGNAL_SCORE = max(
            80,
            min(100, config('SEO_EXTERNAL_SIGNAL_SCORE', default=94, cast=int)),
        )
        self.SEO_SIDE_NOTE_LIMIT = max(
            3,
            min(25, config('SEO_SIDE_NOTE_LIMIT', default=10, cast=int)),
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
            min(30, config('SEARCH_CONSOLE_LOOKBACK_DAYS', default=7, cast=int)),
        )
        self.SEARCH_CONSOLE_ROW_LIMIT = max(
            10,
            min(2500, config('SEARCH_CONSOLE_ROW_LIMIT', default=250, cast=int)),
        )
        self.EXTERNAL_SEO_SIGNALS_FILE = config('EXTERNAL_SEO_SIGNALS_FILE', default=None)
        
        # Load WEB_APP_URL - if not set, derive from WEBHOOK_URL (existing behaviour)
        self.WEB_APP_URL = config('WEB_APP_URL', default=None)
        if not self.WEB_APP_URL and self.WEBHOOK_URL:
            self.WEB_APP_URL = self.WEBHOOK_URL.rstrip('/') + '/webapp/index.html'

        # Load STOREFRONT_DOMAIN - canonical public origin for product deep links.
        # Falls back to CUSTOM_DOMAIN (legacy alias), then derives the origin
        # (scheme://host) from WEB_APP_URL or WEBHOOK_URL so that deployments
        # where only WEBHOOK_URL is set still get a correct storefront origin.
        self.STOREFRONT_DOMAIN = (
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
            self.SEARCH_CONSOLE_SITE_URL = self.STOREFRONT_DOMAIN.rstrip('/') + '/'

        # Ensure WEB_APP_URL exists (so Telegram MenuButton still works) even
        # when it was not derivable from WEBHOOK_URL above.
        if not self.WEB_APP_URL and self.STOREFRONT_DOMAIN:
            self.WEB_APP_URL = self.STOREFRONT_DOMAIN.rstrip('/') + '/webapp/index.html'

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
        
        # Validate channel configuration
        if not self.CHANNEL_ID and not self.CHANNEL_USERNAME:
            errors.append(
                "Either CHANNEL_ID or CHANNEL_USERNAME must be set. "
                "Get your channel ID by forwarding a message from your channel to @userinfobot."
            )
        
        # Validate webhook configuration
        if self.USE_WEBHOOK and not self.WEBHOOK_URL:
            errors.append("WEBHOOK_URL is required when USE_WEBHOOK is true.")
        
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


# Create a singleton instance
Config = ConfigClass()
