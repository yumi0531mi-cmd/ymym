from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class Market(str, Enum):
    KR = "KR"
    US = "US"


class Regime(str, Enum):
    UP = "상승"
    DOWN = "하락"
    RANGE = "박스"
    TRANSITION = "전환"
    UNKNOWN = "미확인"


class Signal(str, Enum):
    BUY = "진입 고려"
    WAIT = "대기"
    SELL = "청산 고려"
    BLOCK = "진입 금지"
    UNVERIFIED = "미검증"


@dataclass(slots=True)
class Quote:
    symbol: str
    market: Market
    price: float
    previous_close: float
    timestamp: datetime
    bid: float | None = None
    ask: float | None = None
    volume: float | None = None
    turnover: float | None = None
    session: str = "UNKNOWN"
    source: str = "KIS"

    @property
    def change_pct(self) -> float:
        return (self.price / self.previous_close - 1) * 100 if self.previous_close > 0 else 0.0

    @property
    def spread_pct(self) -> float | None:
        if not self.bid or not self.ask or self.bid <= 0 or self.ask < self.bid:
            return None
        return (self.ask - self.bid) / ((self.ask + self.bid) / 2) * 100


@dataclass(slots=True)
class ForecastPoint:
    minutes: int
    low: float
    base: float
    high: float
    direction: Regime


@dataclass(slots=True)
class TradePlan:
    symbol: str
    market: Market
    created_at: datetime
    signal: Signal
    strategy: str
    regime: Regime
    current_price: float
    entry: float | None
    target: float | None
    stop: float | None
    target_basis: str
    stop_basis: str
    forecasts: list[ForecastPoint] = field(default_factory=list)
    score: int = 0
    reasons: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    repeat_box: tuple[float, float] | None = None
    data_verified: bool = False
    diagnostics: dict[str, Any] = field(default_factory=dict)
    target2: float | None = None
    target2_basis: str = "2차 목표 미확인"
    invalidation: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
