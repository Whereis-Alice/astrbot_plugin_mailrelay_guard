"""Bounded in-memory rate limiters for global and sender-scoped delivery."""

from __future__ import annotations

import time
from collections import OrderedDict, deque
from collections.abc import Callable


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


class KeyedWindowRateLimiter:
    """Track a bounded number of sender-specific sliding-window event streams."""

    def __init__(
        self,
        *,
        max_keys: int = 1000,
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_keys = max_keys
        self._now = now
        self._timestamps: OrderedDict[str, deque[float]] = OrderedDict()

    def can_record(
        self,
        key: str,
        *,
        max_events: int,
        window_seconds: float,
        minimum_interval_seconds: float = 0,
    ) -> bool:
        """Return whether a new event may be recorded for ``key`` now."""

        timestamps = self._bucket(key, window_seconds)
        now = self._now()
        if len(timestamps) >= max_events:
            return False
        if minimum_interval_seconds and timestamps:
            return now - timestamps[-1] >= minimum_interval_seconds
        return True

    def remaining_seconds(
        self,
        key: str,
        *,
        max_events: int,
        window_seconds: float,
        minimum_interval_seconds: float = 0,
    ) -> int:
        """Return the longest active quota or cooldown wait for one sender."""

        timestamps = self._bucket(key, window_seconds)
        if not timestamps:
            return 0
        now = self._now()
        waits: list[float] = []
        if len(timestamps) >= max_events:
            waits.append(window_seconds - (now - timestamps[0]))
        if minimum_interval_seconds:
            waits.append(minimum_interval_seconds - (now - timestamps[-1]))
        return max(0, int(max(waits, default=0) + 0.999))

    def record(self, key: str) -> None:
        """Record an event after the caller has checked ``can_record``."""

        timestamps = self._bucket(key, window_seconds=float("inf"))
        timestamps.append(self._now())

    def _bucket(self, key: str, window_seconds: float) -> deque[float]:
        normalized_key = str(key or "anonymous")
        timestamps = self._timestamps.get(normalized_key)
        if timestamps is None:
            if len(self._timestamps) >= self._max_keys:
                self._timestamps.popitem(last=False)
            timestamps = deque()
            self._timestamps[normalized_key] = timestamps
        else:
            self._timestamps.move_to_end(normalized_key)

        if window_seconds != float("inf"):
            now = self._now()
            while timestamps and now - timestamps[0] >= window_seconds:
                timestamps.popleft()
        return timestamps
