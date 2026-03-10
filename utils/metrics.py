"""
Lightweight in-process latency metrics collector.

Tracks per-endpoint request counts, total latency, and an approximate p95
using a fixed-size bucket histogram.  Designed for single-process use; in
multi-worker deployments each process has its own counters (best-effort).

Typical usage
-------------
::

    import time
    from utils.metrics import metrics

    t0 = time.monotonic()
    result = await do_work()
    metrics.record("api_products", (time.monotonic() - t0) * 1000)

Expose the collected metrics at an internal endpoint::

    from utils.metrics import metrics

    @app.get("/_internal/metrics")
    async def internal_metrics():
        return metrics.snapshot()
"""

import logging
import threading
import time
from collections import defaultdict
from typing import Dict, List

logger = logging.getLogger(__name__)

# Endpoints considered "critical" — a p95 > this threshold emits a warning log.
_P95_WARN_THRESHOLD_MS: float = 500.0

# Number of most-recent samples kept per endpoint for the histogram / p95
# approximation.  Older samples are evicted (ring-buffer behaviour).
_SAMPLE_WINDOW: int = 200


class _EndpointStats:
    """Per-endpoint accumulator (not thread-safe on its own; lock held by caller)."""

    __slots__ = ("count", "total_ms", "_samples")

    def __init__(self) -> None:
        self.count: int = 0
        self.total_ms: float = 0.0
        # Fixed-size ring buffer for p95 approximation
        self._samples: List[float] = []

    def record(self, duration_ms: float) -> None:
        self.count += 1
        self.total_ms += duration_ms
        self._samples.append(duration_ms)
        if len(self._samples) > _SAMPLE_WINDOW:
            self._samples.pop(0)

    @property
    def avg_ms(self) -> float:
        return self.total_ms / self.count if self.count else 0.0

    @property
    def p95_ms(self) -> float:
        """Approximate p95 from the rolling sample window."""
        if not self._samples:
            return 0.0
        sorted_samples = sorted(self._samples)
        idx = min(int(len(sorted_samples) * 0.95), len(sorted_samples) - 1)
        return sorted_samples[idx]


class MetricsCollector:
    """Thread-safe metrics collector exposing count, avg, and p95 per endpoint."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stats: Dict[str, _EndpointStats] = defaultdict(_EndpointStats)
        self._start_time: float = time.time()

    def record(self, endpoint: str, duration_ms: float) -> None:
        """Record a single request duration for *endpoint*.

        Emits a WARNING log when the rolling p95 exceeds the threshold so
        operators can catch latency regressions without querying the metrics
        endpoint.

        Args:
            endpoint: Logical endpoint name (e.g. ``"api_products"``).
            duration_ms: Request duration in milliseconds.
        """
        with self._lock:
            stats = self._stats[endpoint]
            stats.record(duration_ms)
            p95 = stats.p95_ms

        # Log outside the lock to keep the critical section short
        if p95 > _P95_WARN_THRESHOLD_MS:
            logger.warning(
                "[metrics] High latency on %s: p95=%.1f ms (threshold=%.0f ms) "
                "count=%d avg=%.1f ms",
                endpoint,
                p95,
                _P95_WARN_THRESHOLD_MS,
                stats.count,
                stats.avg_ms,
            )

    def snapshot(self) -> dict:
        """Return a JSON-serialisable snapshot of all collected metrics.

        Returns a dict suitable for direct use as a FastAPI response body::

            {
                "uptime_seconds": 3600,
                "endpoints": {
                    "api_products": {
                        "count": 120,
                        "avg_ms": 245.3,
                        "p95_ms": 498.7
                    },
                    ...
                }
            }
        """
        with self._lock:
            endpoints: Dict[str, dict] = {}
            for name, stats in self._stats.items():
                endpoints[name] = {
                    "count": stats.count,
                    "avg_ms": round(stats.avg_ms, 2),
                    "p95_ms": round(stats.p95_ms, 2),
                }
        return {
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "endpoints": endpoints,
        }

    def reset(self) -> None:
        """Clear all collected metrics (useful in tests)."""
        with self._lock:
            self._stats.clear()
            self._start_time = time.time()


# Module-level singleton – import this everywhere
metrics = MetricsCollector()
