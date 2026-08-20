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


def test_budget_snapshot_separates_usage_by_request_purpose():
    clock = Clock()
    budget = RequestBudget(minute_limit=5, five_hour_limit=10, clock=clock)

    assert budget.acquire("후보검색") == 0.0
    assert budget.acquire("분봉 조회") == 0.0
    assert budget.acquire("분봉 조회") == 0.0

    assert budget.snapshot().usage_by_purpose == {"분봉 조회": 2, "후보검색": 1}


def test_reserved_boundary_capacity_blocks_other_calls_and_is_consumed_by_boundary():
    clock = Clock()
    budget = RequestBudget(minute_limit=3, five_hour_limit=4, clock=clock)

    assert budget.acquire("후보검색") == 0.0
    assert budget.reserve("경계 수집", 2, ttl_seconds=1800) is True
    assert budget.snapshot().reserved_by_purpose == {"경계 수집": 2}
    assert budget.can_spend(1) is True
    assert budget.can_spend(2) is False
    assert budget.acquire("카드 현재가") == 0.0
    assert budget.acquire("카드 현재가") > 0.0
    assert budget.acquire("경계 수집") == 0.0
    assert budget.snapshot().reserved_by_purpose == {"경계 수집": 1}


def test_renew_keeps_pending_boundary_capacity_alive_without_reserving_extra_calls():
    clock = Clock()
    budget = RequestBudget(minute_limit=30, five_hour_limit=10, clock=clock)

    assert budget.reserve("경계 수집", 3, ttl_seconds=35 * 60) is True
    clock.now = 34 * 60

    assert budget.renew("경계 수집", ttl_seconds=35 * 60) == 3
    clock.now = 35 * 60 + 1

    assert budget.snapshot().reserved_by_purpose == {"경계 수집": 3}
    assert budget.acquire("경계 수집") == 0.0
    assert budget.snapshot().reserved_by_purpose == {"경계 수집": 2}
