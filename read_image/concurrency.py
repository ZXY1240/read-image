from __future__ import annotations

import threading


class ConcurrencyGate:
    """Bounds active vision requests and recovers after successful calls."""

    def __init__(self, limit: int, recovery_threshold: int = 8):
        self._initial_limit = max(1, limit)
        self._limit = max(1, limit)
        self._recovery_threshold = max(1, recovery_threshold)
        self._condition = threading.Condition()
        self._active = 0
        self._rate_limit_hits = 0
        self._successes = 0

    def acquire(self) -> None:
        with self._condition:
            while self._active >= self._limit:
                self._condition.wait()
            self._active += 1

    def release(self) -> None:
        with self._condition:
            self._active = max(0, self._active - 1)
            self._condition.notify_all()

    def note_rate_limit(self) -> None:
        with self._condition:
            self._rate_limit_hits += 1
            self._successes = 0
            if self._rate_limit_hits >= 2 and self._limit > 1:
                self._limit -= 1
                self._rate_limit_hits = 0
                self._condition.notify_all()

    def note_success(self) -> None:
        with self._condition:
            self._rate_limit_hits = 0
            self._successes += 1
            if self._successes >= self._recovery_threshold and self._limit < self._initial_limit:
                self._limit += 1
                self._successes = 0
                self._condition.notify_all()

    @property
    def current_limit(self) -> int:
        with self._condition:
            return self._limit
