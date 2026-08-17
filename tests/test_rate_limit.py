from __future__ import annotations

from scanner.rate_limit import RequestBudget


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_minute_budget_blocks_before_exceeding_limit():
    clock = Clock()
    budget = RequestBudget(minute_limit=3, five_hour_limit=10, clock=clock)

    assert budget.acquire() == 0.0
    assert budget.acquire() == 0.0
    assert budget.acquire() == 0.0
    assert budget.acquire() == 60.0

    snapshot = budget.snapshot()
    assert snapshot.minute_used == 3
    assert snapshot.minute_remaining == 0


def test_five_hour_budget_blocks_after_expired_minute_window():
    clock = Clock()
    budget = RequestBudget(minute_limit=3, five_hour_limit=4, clock=clock)
    for _ in range(3):
        assert budget.acquire() == 0.0

    clock.now = 60.1
    assert budget.acquire() == 0.0
    assert budget.acquire() > 0.0

    snapshot = budget.snapshot()
    assert snapshot.minute_used == 1
    assert snapshot.five_hour_used == 4
    assert snapshot.five_hour_remaining == 0


def test_retry_after_block_is_visible_without_spending_extra_budget():
    clock = Clock()
    budget = RequestBudget(minute_limit=3, five_hour_limit=10, clock=clock)
    assert budget.acquire() == 0.0
    budget.block_for(7)

    assert budget.acquire() == 7.0
    assert budget.snapshot().minute_used == 1

    clock.now = 7.0
    assert budget.acquire() == 0.0
