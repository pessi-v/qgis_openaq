"""
Thread-safe rate limiter tracking OpenAQ's 60 req/min and 2000 req/hr limits.

Implemented as a deque of UTC timestamps. Entries older than one hour are
pruned lazily on every operation, so memory stays bounded.
"""
from __future__ import annotations

import threading
from collections import deque
from datetime import datetime, timedelta, timezone


class RateLimiter:
    MINUTE_LIMIT: int = 60
    HOUR_LIMIT: int = 2000

    def __init__(self) -> None:
        self._timestamps: deque[datetime] = deque()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def can_request(self) -> bool:
        """Return True if another request can be made without exceeding limits."""
        with self._lock:
            self._prune()
            return (
                self._count_last_minute() < self.MINUTE_LIMIT
                and len(self._timestamps) < self.HOUR_LIMIT
            )

    def record_request(self) -> None:
        """Record that a request was just made."""
        with self._lock:
            self._timestamps.append(datetime.now(tz=timezone.utc))

    def count_last_minute(self) -> int:
        with self._lock:
            self._prune()
            return self._count_last_minute()

    def count_last_hour(self) -> int:
        with self._lock:
            self._prune()
            return len(self._timestamps)

    # ------------------------------------------------------------------
    # Internal helpers (must be called with lock held)
    # ------------------------------------------------------------------

    def _prune(self) -> None:
        """Drop timestamps older than one hour."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=1)
        while self._timestamps and self._timestamps[0] < cutoff:
            self._timestamps.popleft()

    def _count_last_minute(self) -> int:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(minutes=1)
        return sum(1 for t in self._timestamps if t > cutoff)
