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
        self.PRIMARY_ADMIN_ID: Optional[int] = None
        self.CHANNEL_ID: Optional[int] = None
        self.CHANNEL_USERNAME: Optional[str] = None
        self.DB_PATH: str = 'catalog.db'
        self.USE_WEBHOOK: bool = False
        self.WEBHOOK_URL: Optional[str] = None
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
        
        # Load additional bot tokens (BOT_TOKEN_1, BOT_TOKEN_2, etc.)
        # This allows multiple bot instances to run with separate webhook endpoints
        bot_tokens = []
        if self.BOT_TOKEN:
            bot_tokens.append(self.BOT_TOKEN)
        
        # Try to load BOT_TOKEN_1, BOT_TOKEN_2, ... up to BOT_TOKEN_10
        for i in range(1, 11):
            try:
                token = config(f'BOT_TOKEN_{i}')
                if token:
                    bot_tokens.append(token)
            except UndefinedValueError:
                # Stop searching if we hit a missing token index >= 2
                # This assumes tokens are numbered sequentially starting from 1
                # We allow BOT_TOKEN to exist without BOT_TOKEN_1, but if BOT_TOKEN_2
                # is missing, we assume there are no more tokens
                if i >= 2:
                    break
        
        self.BOT_TOKENS = bot_tokens
        
        # Load ADMIN_IDS
        try:
            self.ADMIN_IDS = config('ADMIN_IDS', cast=Csv(int))
        except (UndefinedValueError, ValueError):
            self.ADMIN_IDS = []
        
        # Load PRIMARY_ADMIN_ID (optional - defaults to first admin if not set)
        try:
            self.PRIMARY_ADMIN_ID = config('PRIMARY_ADMIN_ID', default=None, cast=lambda x: int(x) if x else None)
        except (ValueError, TypeError):
            self.PRIMARY_ADMIN_ID = None
        
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
            logger.info(f"    - Bot {idx}: {'*' * 10}{token[-5:]}")
        logger.info(f"  - Admin IDs: {len(self.ADMIN_IDS)} admin(s) configured")
        logger.info(f"  - Channel: {self.CHANNEL_ID or self.CHANNEL_USERNAME}")
        logger.info(f"  - Database: {self.DB_PATH}")
        logger.info(f"  - Webhook mode: {self.USE_WEBHOOK}")


# Create a singleton instance
Config = ConfigClass()
