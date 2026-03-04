"""
Telegram WebApp security utilities.
Implements initData verification per Telegram WebApp documentation.
"""
import hashlib
import hmac
import json
import logging
from typing import Any, Dict, Optional
from urllib.parse import parse_qsl

from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)


class TelegramUser(BaseModel):
    """Strongly-typed representation of the Telegram WebApp user object."""

    model_config = {"extra": "ignore"}

    id: int
    first_name: str
    last_name: Optional[str] = None
    username: Optional[str] = None
    language_code: Optional[str] = None
    is_premium: Optional[bool] = None
    allows_write_to_pm: Optional[bool] = None
    photo_url: Optional[str] = None


def verify_init_data(init_data: str, bot_token: str) -> bool:
    """
    Verify Telegram WebApp initData using HMAC-SHA256.
    
    According to Telegram docs:
    1. Parse init_data string as URL query parameters
    2. Sort all parameters alphabetically (except 'hash')
    3. Create data_check_string by joining sorted key=value pairs with newlines
    4. Compute HMAC-SHA256(secret_key, data_check_string) where secret_key = HMAC-SHA256("WebAppData", bot_token)
    5. Compare computed hash with the hash from init_data
    
    Args:
        init_data: The initData string from Telegram.WebApp.initData
        bot_token: The bot token
    
    Returns:
        True if initData is valid, False otherwise
    
    Reference:
        https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """
    try:
        # Parse init_data as URL query string
        parsed_data = dict(parse_qsl(init_data))
        
        # Extract hash (the signature we need to verify)
        received_hash = parsed_data.get('hash')
        if not received_hash:
            logger.warning("No hash found in initData")
            return False
        
        # Remove hash from data for verification
        parsed_data.pop('hash', None)
        
        # Sort parameters alphabetically
        sorted_params = sorted(parsed_data.items())
        
        # Create data_check_string by joining key=value pairs with newlines
        data_check_string = '\n'.join(f"{key}={value}" for key, value in sorted_params)
        
        # Compute secret_key = HMAC-SHA256("WebAppData", bot_token)
        secret_key = hmac.new(
            key=b"WebAppData",
            msg=bot_token.encode(),
            digestmod=hashlib.sha256
        ).digest()
        
        # Compute hash = HMAC-SHA256(secret_key, data_check_string)
        computed_hash = hmac.new(
            key=secret_key,
            msg=data_check_string.encode(),
            digestmod=hashlib.sha256
        ).hexdigest()
        
        # Compare hashes
        is_valid = hmac.compare_digest(computed_hash, received_hash)

        if not is_valid:
            # Log at DEBUG level instead of WARNING to avoid noise in multi-bot setups
            # The caller (_get_validated_init_data) will log a single warning if no tokens match
            masked_token_id = bot_token[:bot_token.index(":")] if ":" in bot_token else bot_token[:8]
            logger.debug("InitData validation failed: hash mismatch (bot_id=%s)", masked_token_id)

        return is_valid
        
    except Exception as e:
        logger.exception(f"Error verifying initData: {e}")
        return False


def parse_init_data(init_data: str) -> Dict[str, Any]:
    """
    Parse Telegram WebApp initData string into a dictionary.
    
    Args:
        init_data: The initData string from Telegram.WebApp.initData
        
    Returns:
        Dictionary with parsed data, including user info if available.
        Returns empty dict on parsing failure (error is logged).
    """
    if not init_data:
        logger.warning("Empty initData provided to parse_init_data")
        return {}
    
    try:
        parsed = dict(parse_qsl(init_data))

        # Parse and validate user JSON if present
        if 'user' in parsed:
            try:
                raw_user = json.loads(parsed['user'])
                parsed['user'] = TelegramUser.model_validate(raw_user)
            except (json.JSONDecodeError, TypeError, ValidationError) as e:
                logger.error(f"Failed to parse user JSON from initData: {e}")
                # Remove the invalid user entry so callers see None instead of a raw string
                parsed.pop('user', None)

        return parsed
    except Exception as e:
        logger.error(f"Critical error parsing initData: {e}", exc_info=True)
        return {}

