"""
Shared decorators for handler functions.
"""
import functools
import logging

from utils.helpers import is_admin

logger = logging.getLogger(__name__)

_ADMIN_ONLY_MSG = "❌ This command is only available for administrators."


def admin_required(func):
    """
    Decorator that rejects non-admin users attempting admin-only callback actions.

    Checks whether the ``user_id`` argument is in the configured admin list and,
    if not, answers the callback query with an error alert and returns early so
    the wrapped handler is never called.

    Expected handler signature::

        async def handler(update, context, query, user_id, parts) -> None: ...
    """
    @functools.wraps(func)
    async def wrapper(update, context, query, user_id, parts):
        if not is_admin(user_id):
            await query.answer("❌ Only admins can perform this action.", show_alert=True)
            return
        return await func(update, context, query, user_id, parts)
    return wrapper


def command_admin_required(func):
    """
    Decorator for Telegram command handlers that restricts access to admin users.

    Reads ``update.effective_user.id``, checks ``is_admin()``, and replies with a
    rejection message if the user is not an admin.  The wrapped function is only
    called when the check passes.

    Expected handler signature::

        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None: ...
    """
    @functools.wraps(func)
    async def wrapper(update, context):
        if not is_admin(update.effective_user.id):
            await update.message.reply_text(_ADMIN_ONLY_MSG)
            return
        return await func(update, context)
    return wrapper
