"""
Configuration management using python-decouple for secure environment variable handling.
"""
import logging
from typing import List, Optional
from decouple import config, Csv

logger = logging.getLogger(__name__)


class Config:
    """Centralized configuration class for the bot."""
    
    # Required configurations
    BOT_TOKEN: str = config('BOT_TOKEN')
    ADMIN_IDS: List[int] = config('ADMIN_IDS', cast=Csv(int))
    
    # Channel configuration (at least one is required)
    CHANNEL_ID: Optional[int] = config('CHANNEL_ID', default=None, cast=lambda x: int(x) if x else None)
    CHANNEL_USERNAME: Optional[str] = config('CHANNEL_USERNAME', default=None)
    
    # Optional configurations with defaults
    DB_PATH: str = config('DB_PATH', default='catalog.db')
    USE_WEBHOOK: bool = config('USE_WEBHOOK', default=False, cast=bool)
    WEBHOOK_URL: Optional[str] = config('WEBHOOK_URL', default=None)
    
    @classmethod
    def validate(cls) -> None:
        """
        Validate that all required environment variables are set correctly.
        Raises ValueError with clear messages if validation fails.
        """
        errors = []
        
        # Validate BOT_TOKEN
        if not cls.BOT_TOKEN:
            errors.append("BOT_TOKEN is required. Get it from @BotFather on Telegram.")
        
        # Validate ADMIN_IDS
        if not cls.ADMIN_IDS:
            errors.append("ADMIN_IDS is required. Add at least one admin user ID. Get your ID from @userinfobot.")
        
        # Validate channel configuration
        if not cls.CHANNEL_ID and not cls.CHANNEL_USERNAME:
            errors.append(
                "Either CHANNEL_ID or CHANNEL_USERNAME must be set. "
                "Get your channel ID by forwarding a message from your channel to @userinfobot."
            )
        
        # Validate webhook configuration
        if cls.USE_WEBHOOK and not cls.WEBHOOK_URL:
            errors.append("WEBHOOK_URL is required when USE_WEBHOOK is true.")
        
        # If there are any errors, raise a comprehensive error message
        if errors:
            error_message = "\n❌ Configuration Validation Failed:\n\n" + "\n".join(f"  • {error}" for error in errors)
            error_message += "\n\n💡 Please check your .env file and ensure all required variables are set correctly."
            error_message += "\n📖 See .env.example for reference."
            raise ValueError(error_message)
        
        logger.info("✅ Configuration validation successful")
        logger.info(f"  - Bot token: {'*' * 10}{cls.BOT_TOKEN[-5:]}")
        logger.info(f"  - Admin IDs: {len(cls.ADMIN_IDS)} admin(s) configured")
        logger.info(f"  - Channel: {cls.CHANNEL_ID or cls.CHANNEL_USERNAME}")
        logger.info(f"  - Database: {cls.DB_PATH}")
        logger.info(f"  - Webhook mode: {cls.USE_WEBHOOK}")
