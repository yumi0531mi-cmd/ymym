from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from .models import Market
from .persistence import EventStore, PersistenceError
from .sessions import ET, KST


@dataclass(slots=True)
class CycleState:
    symbol: str
    market: str
    trade_date: str
    real_breakdowns: int = 0
    hard_exits: int = 0
    cooldown_until: str | None = None
    hard_kill: bool = False
    last_event_marker: str | None = None
    updated_at: str = ""

    @property
    def cooldown_active(self) -> bool:
        if not self.cooldown_until:
            return False
        try:
            return datetime.now().astimezone() < datetime.fromisoformat(self.cooldown_until)
        except ValueError:
            return False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["cooldown_active"] = self.cooldown_active
        return payload


def _trade_date(market: Market, now: datetime) -> str:
    local = now.astimezone(KST if market == Market.KR else ET)
    return local.strftime("%Y-%m-%d")


class CycleStore:
    """Optional durable state for v5.1 re-entry control.

    Without Supabase configuration this object remains in-memory for the current request,
    so the UI stays read-only and never pretends that cooldown survives a cloud restart.
    """

    def __init__(self, events: EventStore):
        self.events = events
        self._local: dict[str, CycleState] = {}

    @staticmethod
    def _id(market: str, symbol: str, date: str) -> str:
        return f"cycle-v51-{market}-{symbol.upper()}-{date}"

    def get(self, symbol: str, market: Market, now: datetime | None = None) -> CycleState:
        now = now or datetime.now().astimezone()
        date = _trade_date(market, now)
        key = self._id(market.value, symbol, date)
        if key in self._local:
            return self._local[key]
        if self.events.configured:
            try:
                for row in reversed(self.events.list("cycle_state_v51")):
                    if row.get("market") == market.value and row.get("symbol") == symbol.upper() and row.get("trade_date") == date:
                        state = CycleState(**{name: row.get(name) for name in CycleState.__dataclass_fields__})
                        self._local[key] = state
                        return state
            except PersistenceError:
                pass
        state = CycleState(symbol=symbol.upper(), market=market.value, trade_date=date, updated_at=now.isoformat())
        self._local[key] = state
        return state

    def save(self, state: CycleState) -> None:
        state.updated_at = datetime.now().astimezone().isoformat()
        key = self._id(state.market, state.symbol, state.trade_date)
        self._local[key] = state
        self.events.upsert(key, "cycle_state_v51", state.updated_at, state.to_dict())

    def apply_risk_state(self, state: CycleState, risk_state: str, marker: str) -> CycleState:
        """Apply at most one event per completed-bar marker to prevent rerun double counting."""
        if state.last_event_marker == marker:
            return state
        now = datetime.now().astimezone()
        if risk_state == "REAL_BREAKDOWN":
            state.real_breakdowns += 1
            state.cooldown_until = (now + timedelta(minutes=10)).isoformat()
            state.last_event_marker = marker
        elif risk_state == "HARD_EXIT":
            state.hard_exits += 1
            state.cooldown_until = (now + timedelta(minutes=15)).isoformat()
            state.last_event_marker = marker
        else:
            return state
        if state.real_breakdowns >= 2 or state.hard_exits >= 2:
            state.hard_kill = True
        self.save(state)
        return state
