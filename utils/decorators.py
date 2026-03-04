"""
Shared decorators for callback query handler functions.
"""
import functools
import logging

from utils.helpers import is_admin

logger = logging.getLogger(__name__)


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
