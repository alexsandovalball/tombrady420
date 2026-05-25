"""
Shared notification service instance.

NotificationService owns in-memory queues and rate limiters, so callers must
reuse one process-wide instance for FIFO delivery and duplicate-task guards to
work as intended.
"""
from database import Database
from utils.notifications import NotificationService


_db = Database()
notification_service = NotificationService(_db)


def get_notification_service() -> NotificationService:
    """Return the process-wide notification service."""
    return notification_service
