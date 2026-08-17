from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass
import threading
import time
from typing import Callable


@dataclass(frozen=True, slots=True)
class BudgetSnapshot:
    minute_limit: int
    minute_used: int
    minute_remaining: int
    five_hour_limit: int
    five_hour_used: int
    five_hour_remaining: int
    blocked_seconds: float

    def to_dict(self) -> dict[str, int | float]:
        return asdict(self)


class RequestBudget:
    """Thread-safe rolling request budget for one KIS app process.

    A request is counted before it leaves the application, including a retry.
    The budget therefore protects the upstream API even when an endpoint is
    slow or returns an error. It is intentionally stricter than any assumed
    provider limit; KIS endpoint-specific limits can be lower or change.
    """

    def __init__(
        self,
        minute_limit: int = 30,
        five_hour_limit: int = 1100,
        *,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.minute_limit = max(1, int(minute_limit))
        self.five_hour_limit = max(self.minute_limit, int(five_hour_limit))
        self._clock = clock or time.monotonic
        self._calls: deque[float] = deque()
        self._blocked_until = 0.0
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - 5 * 60 * 60
        while self._calls and self._calls[0] <= cutoff:
            self._calls.popleft()

    def _minute_used(self, now: float) -> int:
        cutoff = now - 60
        return sum(value > cutoff for value in self._calls)

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            now = self._clock()
            self._prune(now)
            minute_used = self._minute_used(now)
            five_hour_used = len(self._calls)
            return BudgetSnapshot(
                minute_limit=self.minute_limit,
                minute_used=minute_used,
                minute_remaining=max(0, self.minute_limit - minute_used),
                five_hour_limit=self.five_hour_limit,
                five_hour_used=five_hour_used,
                five_hour_remaining=max(0, self.five_hour_limit - five_hour_used),
                blocked_seconds=max(0.0, self._blocked_until - now),
            )

    def acquire(self) -> float:
        """Count one request or return the conservative wait time in seconds."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            if now < self._blocked_until:
                return self._blocked_until - now
            if self._minute_used(now) >= self.minute_limit:
                first_in_minute = next((value for value in self._calls if value > now - 60), now)
                return max(0.1, first_in_minute + 60 - now)
            if len(self._calls) >= self.five_hour_limit:
                return max(0.1, self._calls[0] + 5 * 60 * 60 - now)
            self._calls.append(now)
            return 0.0

    def block_for(self, seconds: float) -> None:
        with self._lock:
            self._blocked_until = max(self._blocked_until, self._clock() + max(0.0, seconds))
