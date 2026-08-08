"""In-memory success-only delivery rate limiting."""

from __future__ import annotations

import time
from collections import deque


class SuccessWindowRateLimiter:
    """Track accepted deliveries in a sliding window.

    The caller is expected to serialize ``can_send`` -> SMTP delivery ->
    ``record_success`` with a lock. Failed SMTP attempts deliberately do not
    consume quota, which avoids hiding delivery failures behind cooldowns.
    """

    def __init__(self) -> None:
        self._timestamps: deque[float] = deque()

    def can_send(self, *, max_messages: int, window_seconds: float) -> bool:
        now = time.monotonic()
        self._purge(now, window_seconds)
        return len(self._timestamps) < max_messages

    def remaining_seconds(self, *, window_seconds: float) -> int:
        now = time.monotonic()
        self._purge(now, window_seconds)
        if not self._timestamps:
            return 0
        return max(0, int(window_seconds - (now - self._timestamps[0])))

    def record_success(self) -> None:
        self._timestamps.append(time.monotonic())

    def _purge(self, now: float, window_seconds: float) -> None:
        while self._timestamps and now - self._timestamps[0] >= window_seconds:
            self._timestamps.popleft()
