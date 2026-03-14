"""
Shared utility for determining whether the running bot instance is the primary one.

The "primary instance" is defined as the bot whose token matches the first entry
in Config.BOT_TOKENS.  Only the primary instance should run background tasks that
must not be duplicated across bots (notification queues, cleanup jobs, etc.).
"""
import logging
from telegram.ext import ContextTypes

from configs.config import Config

logger = logging.getLogger(__name__)


def is_primary_instance(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Return True if *context* belongs to the primary bot instance.

    The primary bot is the one whose token is first in Config.BOT_TOKENS.
    When the token cannot be determined (e.g. in tests or unusual setups) we
    default to True so that single-bot deployments keep working without change.

    Args:
        context: Telegram handler context; must expose ``context.bot.token``.

    Returns:
        True when this is the primary instance (or when primary status cannot
        be determined), False otherwise.
    """
    if not hasattr(context, "bot") or not hasattr(context.bot, "token"):
        logger.warning("Unable to determine bot instance, assuming primary")
        return True

    current_token = context.bot.token
    bot_tokens = Config.BOT_TOKENS

    if not bot_tokens or not isinstance(bot_tokens, list):
        return True

    is_primary = current_token == bot_tokens[0]

    if not is_primary:
        logger.debug(
            "Skipping operation - not primary instance (bot token: ...%s)",
            current_token[-5:],
        )

    return is_primary
