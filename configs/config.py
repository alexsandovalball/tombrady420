"""
Configuration management using python-decouple for secure environment variable handling.
"""
import logging
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
        self.ORDER_CONTACT: str = '@FLYAWAYPEP'  # Configurable order contact
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
            if bot_tokens_raw:
                self.BOT_TOKENS = [t.strip() for t in bot_tokens_raw.split(',') if t.strip()]
                if not self.BOT_TOKENS:
                    logger.warning("BOT_TOKENS is set but contains no valid tokens (check for extra commas or whitespace).")
                self._loaded = True
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
        bot_tokens = []
        if self.BOT_TOKEN:
            bot_tokens.append(self.BOT_TOKEN)

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
        self.ORDER_CONTACT = config('ORDER_CONTACT', default='@FLYAWAYPEP')
        
        # Load WEB_APP_URL - if not set, derive from WEBHOOK_URL
        self.WEB_APP_URL = config('WEB_APP_URL', default=None)
        if not self.WEB_APP_URL and self.WEBHOOK_URL:
            self.WEB_APP_URL = self.WEBHOOK_URL.rstrip('/') + '/webapp/index.html'
        
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
