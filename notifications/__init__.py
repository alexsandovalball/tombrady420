"""
notifications package — rate-limit constants and policy knobs.

The ``NotificationService`` class lives in ``utils.notifications`` and is
imported from there directly.  This package holds the extracted constants and,
in future refactors, further sub-modules (service.py, delivery.py, storage.py).

Usage::

    from notifications.constants import STAGGER_GROUP_SIZE
    from utils.notifications import NotificationService   # class still here
"""

from notifications.constants import (  # noqa: F401
    MAX_NOTIFICATIONS_PER_HOUR,
    MAX_CUSTOM_MESSAGES_PER_HOUR,
    NOTIFICATION_BATCH_SIZE,
    BATCH_DELAY_SECONDS,
    PENDING_NOTIFICATIONS_BATCH_LIMIT,
    CUSTOM_MESSAGE_BATCH_SIZE,
    CUSTOM_MESSAGE_DELAY_SECONDS,
    ENABLE_STAGGERED_DELIVERY,
    STAGGER_GROUP_SIZE,
    STAGGER_INTERVAL_SECONDS,
    TELEGRAM_MSGS_PER_SECOND_HARD_LIMIT,
    RATE_LIMIT_SAFETY_FACTOR,
    BOT_SEND_RATE_PER_SEC,
    BOT_SEND_BURST,
    MAX_CONCURRENT_BOT_WORKERS,
    FILE_ID_PREVIEW_LENGTH,
)

__all__ = [
    "MAX_NOTIFICATIONS_PER_HOUR",
    "MAX_CUSTOM_MESSAGES_PER_HOUR",
    "NOTIFICATION_BATCH_SIZE",
    "BATCH_DELAY_SECONDS",
    "PENDING_NOTIFICATIONS_BATCH_LIMIT",
    "CUSTOM_MESSAGE_BATCH_SIZE",
    "CUSTOM_MESSAGE_DELAY_SECONDS",
    "ENABLE_STAGGERED_DELIVERY",
    "STAGGER_GROUP_SIZE",
    "STAGGER_INTERVAL_SECONDS",
    "TELEGRAM_MSGS_PER_SECOND_HARD_LIMIT",
    "RATE_LIMIT_SAFETY_FACTOR",
    "BOT_SEND_RATE_PER_SEC",
    "BOT_SEND_BURST",
    "MAX_CONCURRENT_BOT_WORKERS",
    "FILE_ID_PREVIEW_LENGTH",
]
