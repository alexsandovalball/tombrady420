"""
Enhanced monitoring utilities for the webhook server.

Provides higher-level monitoring on top of ``utils.metrics.MetricsCollector``:

* ``WebhookMonitor`` – tracks per-bot webhook success/failure counters and
  logs a periodic summary so operators can spot anomalies in log aggregators.
* ``record_webhook_result`` – convenience helper used by ``webhook_server.py``
  to record the outcome of each processed webhook update.

No new environment variables are introduced.  All data is held in-memory and
resets on process restart (best-effort monitoring, not durable storage).

Usage::

    from utils.monitoring import monitor, record_webhook_result

    # Record a successful webhook for bot 3
    record_webhook_result(bot_index=3, success=True, duration_ms=580.0)

    # Record a failed webhook
    record_webhook_result(bot_index=3, success=False, duration_ms=0.0)

    # Read a snapshot for the /_internal/metrics endpoint
    snapshot = monitor.snapshot()
"""

import logging
import threading
import time
from collections import defaultdict
from typing import Any, Dict

logger = logging.getLogger(__name__)

# Log a warning when the success-rate drops below this fraction.
_WARN_SUCCESS_RATE: float = 0.90  # warn when fewer than 90% of requests succeed


class _BotStats:
    """Per-bot counters (not thread-safe; callers hold the outer lock)."""

    __slots__ = ("success", "failure", "total_duration_ms", "last_seen")

    def __init__(self) -> None:
        self.success: int = 0
        self.failure: int = 0
        self.total_duration_ms: float = 0.0
        self.last_seen: float = time.time()

    @property
    def total(self) -> int:
        return self.success + self.failure

    @property
    def success_rate(self) -> float:
        return self.success / self.total if self.total else 1.0

    @property
    def avg_duration_ms(self) -> float:
        return self.total_duration_ms / self.total if self.total else 0.0


class WebhookMonitor:
    """Thread-safe monitor that aggregates webhook outcomes per bot.

    Attributes:
        start_time: Unix timestamp when this monitor was created (for uptime).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._bots: Dict[int, _BotStats] = defaultdict(_BotStats)
        self.start_time: float = time.time()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(self, bot_index: int, success: bool, duration_ms: float) -> None:
        """Record the outcome of a single webhook processing attempt.

        Emits a WARNING log when the rolling success rate for this bot drops
        below ``_WARN_FAILURE_RATE``.

        Args:
            bot_index: Index of the bot that processed the update.
            success: True if the update was processed without error.
            duration_ms: Wall-clock processing time in milliseconds.
        """
        with self._lock:
            stats = self._bots[bot_index]
            if success:
                stats.success += 1
            else:
                stats.failure += 1
            stats.total_duration_ms += duration_ms
            stats.last_seen = time.time()

            # Snapshot values for use outside the lock
            total = stats.total
            success_rate = stats.success_rate
            avg_ms = stats.avg_duration_ms

        # Log outside the lock to keep critical section short
        if total % 100 == 0 and total > 0:
            logger.info(
                "[monitoring] bot=%d total=%d success_rate=%.1f%% avg_ms=%.1f",
                bot_index,
                total,
                success_rate * 100,
                avg_ms,
            )

        if not success and total >= 10 and success_rate < _WARN_SUCCESS_RATE:
            logger.warning(
                "[monitoring] bot=%d high failure rate: %.1f%% of %d requests failed",
                bot_index,
                (1 - success_rate) * 100,
                total,
            )

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> Dict[str, Any]:
        """Return a JSON-serialisable snapshot of all collected metrics.

        Returns:
            Dict with ``uptime_seconds`` and per-bot stats under ``bots``.
        """
        with self._lock:
            bots_snapshot = {
                str(bot_index): {
                    "success": s.success,
                    "failure": s.failure,
                    "total": s.total,
                    "success_rate": round(s.success_rate, 4),
                    "avg_duration_ms": round(s.avg_duration_ms, 2),
                    "last_seen": s.last_seen,
                }
                for bot_index, s in self._bots.items()
            }

        return {
            "uptime_seconds": round(time.time() - self.start_time, 1),
            "bots": bots_snapshot,
        }

    def reset(self) -> None:
        """Clear all counters (primarily useful for testing)."""
        with self._lock:
            self._bots.clear()
            self.start_time = time.time()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

#: Global monitor instance – import and use directly:
#: ``from utils.monitoring import monitor, record_webhook_result``
monitor: WebhookMonitor = WebhookMonitor()


def record_webhook_result(
    bot_index: int,
    success: bool,
    duration_ms: float,
) -> None:
    """Convenience wrapper around ``monitor.record``.

    Args:
        bot_index: Index of the bot that processed the update.
        success: True if the update was processed without error.
        duration_ms: Wall-clock processing time in milliseconds.
    """
    monitor.record(bot_index, success, duration_ms)
