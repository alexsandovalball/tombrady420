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
        self.BOT_TOKENS: List[str] = []  # Multiple bot tokens for multi-bot support
        self.ADMIN_IDS: List[int] = []
        self.CHANNEL_ID: Optional[int] = None
        self.CHANNEL_USERNAME: Optional[str] = None
        self.DB_PATH: str = 'catalog.db'
        self.USE_WEBHOOK: bool = False
        self.WEBHOOK_URL: Optional[str] = None
        self.ORDER_CONTACT: str = '@FLYAWAYPEP'  # Configurable order contact
        self.ENABLE_WAL_MODE: bool = True
        self.ENABLE_FTS_SEARCH: bool = True
        self.DB_POOL_SIZE: int = 10
        self.DB_CACHE_TTL: int = 300  # 5 minutes
        self._load_config()
    
    def _load_config(self):
        """Load configuration from environment variables."""
        if self._loaded:
            return
        
        # Load BOT_TOKEN (backward compatibility)
        try:
            self.BOT_TOKEN = config('BOT_TOKEN')
        except UndefinedValueError:
            self.BOT_TOKEN = None
        
        # Load multiple bot tokens (BOT_TOKEN_1, BOT_TOKEN_2, etc.)
        bot_tokens = []
        if self.BOT_TOKEN:
            # If single BOT_TOKEN exists, use it as primary
            bot_tokens.append(self.BOT_TOKEN)
        else:
            # Try loading numbered tokens
            for i in range(1, 10):  # Support up to 9 bots
                try:
                    token = config(f'BOT_TOKEN_{i}')
                    if token:
                        bot_tokens.append(token)
                except UndefinedValueError:
                    break  # Stop at first missing token
        
        self.BOT_TOKENS = bot_tokens
        # Set BOT_TOKEN to first token for backward compatibility
        if bot_tokens:
            self.BOT_TOKEN = bot_tokens[0]
        
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
        
        # Performance optimization settings
        self.ENABLE_WAL_MODE = config('ENABLE_WAL_MODE', default=True, cast=bool)
        self.ENABLE_FTS_SEARCH = config('ENABLE_FTS_SEARCH', default=True, cast=bool)
        self.DB_POOL_SIZE = config('DB_POOL_SIZE', default=10, cast=int)
        self.DB_CACHE_TTL = config('DB_CACHE_TTL', default=300, cast=int)
        
        self._loaded = True
    
    def validate(self) -> None:
        """
        Validate that all required environment variables are set correctly.
        Raises ConfigError with clear messages if validation fails.
        """
        errors = []
        
        # Validate BOT_TOKEN or BOT_TOKENS
        if not self.BOT_TOKEN and not self.BOT_TOKENS:
            errors.append("BOT_TOKEN or BOT_TOKEN_1 is required. Get it from @BotFather on Telegram.")
        
        # Validate ADMIN_IDS
        if not self.ADMIN_IDS:
            errors.append("ADMIN_IDS is required. Add at least one admin user ID. Get your ID from @userinfobot.")
        
        # Validate channel configuration (only required for primary bot)
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
        logger.info(f"  - Bot instances: {len(self.BOT_TOKENS)}")
        logger.info(f"  - Primary bot token: {'*' * 10}{self.BOT_TOKEN[-5:]}")
        logger.info(f"  - Admin IDs: {len(self.ADMIN_IDS)} admin(s) configured")
        logger.info(f"  - Channel: {self.CHANNEL_ID or self.CHANNEL_USERNAME}")
        logger.info(f"  - Database: {self.DB_PATH}")
        logger.info(f"  - Webhook mode: {self.USE_WEBHOOK}")
        logger.info(f"  - WAL mode: {self.ENABLE_WAL_MODE}")
        logger.info(f"  - FTS search: {self.ENABLE_FTS_SEARCH}")


# Create a singleton instance
Config = ConfigClass()
