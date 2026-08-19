from __future__ import annotations

from collections import Counter, deque
from dataclasses import asdict, dataclass, field
import threading
import time
from typing import Callable


@dataclass(frozen=True)
class BudgetSnapshot:
    minute_limit: int
    minute_used: int
    minute_remaining: int
    five_hour_limit: int
    five_hour_used: int
    five_hour_remaining: int
    blocked_seconds: float
    usage_by_purpose: dict[str, int] = field(default_factory=dict)
    reserved_by_purpose: dict[str, int] = field(default_factory=dict)

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
        self._calls: deque[tuple[float, str]] = deque()
        self._reservations: deque[tuple[float, str, int]] = deque()
        self._blocked_until = 0.0
        self._lock = threading.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - 5 * 60 * 60
        while self._calls and self._calls[0][0] <= cutoff:
            self._calls.popleft()
        self._reservations = deque(
            (expires_at, purpose, count)
            for expires_at, purpose, count in self._reservations
            if expires_at > now and count > 0
        )

    def _reserved_count(self) -> int:
        return sum(count for _expires_at, _purpose, count in self._reservations)

    def _has_reservation(self, purpose: str) -> bool:
        return any(saved_purpose == purpose and count > 0 for _expires_at, saved_purpose, count in self._reservations)

    def _consume_reservation(self, purpose: str) -> bool:
        updated: deque[tuple[float, str, int]] = deque()
        consumed = False
        for expires_at, saved_purpose, count in self._reservations:
            if not consumed and saved_purpose == purpose and count > 0:
                count -= 1
                consumed = True
            if count > 0:
                updated.append((expires_at, saved_purpose, count))
        self._reservations = updated
        return consumed

    def _minute_used(self, now: float) -> int:
        cutoff = now - 60
        return sum(at > cutoff for at, _purpose in self._calls)

    def snapshot(self) -> BudgetSnapshot:
        with self._lock:
            now = self._clock()
            self._prune(now)
            minute_used = self._minute_used(now)
            five_hour_used = len(self._calls)
            usage_by_purpose = dict(sorted(Counter(purpose for _at, purpose in self._calls).items()))
            reserved_by_purpose = dict(sorted(Counter({}).items()))
            for _expires_at, purpose, count in self._reservations:
                reserved_by_purpose[purpose] = reserved_by_purpose.get(purpose, 0) + count
            return BudgetSnapshot(
                minute_limit=self.minute_limit,
                minute_used=minute_used,
                minute_remaining=max(0, self.minute_limit - minute_used),
                five_hour_limit=self.five_hour_limit,
                five_hour_used=five_hour_used,
                five_hour_remaining=max(0, self.five_hour_limit - five_hour_used),
                blocked_seconds=max(0.0, self._blocked_until - now),
                usage_by_purpose=usage_by_purpose,
                reserved_by_purpose=reserved_by_purpose,
            )

    def can_spend(self, count: int) -> bool:
        """Return whether ordinary calls can be admitted without consuming reserved boundaries."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            return len(self._calls) + self._reserved_count() + max(0, int(count)) <= self.five_hour_limit

    def reserve(self, purpose: str, count: int, ttl_seconds: float) -> bool:
        """Hold future request capacity for a critical time boundary before starting work."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            count = max(0, int(count))
            if not count or len(self._calls) + self._reserved_count() + count > self.five_hour_limit:
                return False
            self._reservations.append((now + max(1.0, float(ttl_seconds)), str(purpose), count))
            return True

    def release(self, purpose: str, count: int = 1) -> None:
        """Return unused future capacity when a WebSocket tick made REST fallback unnecessary."""
        with self._lock:
            self._prune(self._clock())
            for _ in range(max(0, int(count))):
                if not self._consume_reservation(str(purpose)):
                    break

    def acquire(self, purpose: str = "기타") -> float:
        """Count one request or return the conservative wait time in seconds."""
        with self._lock:
            now = self._clock()
            self._prune(now)
            if now < self._blocked_until:
                return self._blocked_until - now
            if self._minute_used(now) >= self.minute_limit:
                first_in_minute = next((at for at, _purpose in self._calls if at > now - 60), now)
                return max(0.1, first_in_minute + 60 - now)
            reserved = self._has_reservation(str(purpose))
            if not reserved and len(self._calls) + self._reserved_count() >= self.five_hour_limit:
                return max(0.1, self._calls[0][0] + 5 * 60 * 60 - now)
            if reserved:
                self._consume_reservation(str(purpose))
            self._calls.append((now, str(purpose or "기타")))
            return 0.0

    def block_for(self, seconds: float) -> None:
        with self._lock:
            self._blocked_until = max(self._blocked_until, self._clock() + max(0.0, seconds))
