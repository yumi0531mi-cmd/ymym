from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any

import requests


class PersistenceError(RuntimeError):
    pass


def _secret(names: tuple[str, ...], secrets: Any | None = None) -> str:
    for name in names:
        if secrets is not None:
            try:
                value = secrets[name]
                if value:
                    return str(value)
            except Exception:
                pass
    return ""


class EventStore:
    """Optional Supabase REST event store.

    No network call occurs unless both SUPABASE_URL and SUPABASE_KEY are supplied.
    The scanner remains usable without this store, but local records then disappear
    when Community Cloud restarts the app.
    """

    def __init__(self, secrets: Any | None = None):
        self.url = _secret(("SUPABASE_URL",), secrets).rstrip("/")
        self.key = _secret(("SUPABASE_KEY", "SUPABASE_SERVICE_ROLE_KEY"), secrets)
        self.session = requests.Session()

    @property
    def configured(self) -> bool:
        return bool(self.url and self.key)

    @property
    def status(self) -> str:
        return "Supabase 영속 저장" if self.configured else "로컬 임시 저장"

    def _headers(self, prefer: str | None = None) -> dict[str, str]:
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def upsert(self, event_id: str, kind: str, created_at: str, payload: dict[str, Any]) -> None:
        if not self.configured:
            return
        row = {"id": event_id, "kind": kind, "created_at": created_at, "payload": payload}
        try:
            response = self.session.post(
                f"{self.url}/rest/v1/scanner_events?on_conflict=id",
                headers=self._headers("resolution=merge-duplicates,return=minimal"),
                json=row,
                timeout=8,
            )
        except requests.RequestException as exc:
            raise PersistenceError(f"영속 저장소 연결 오류: {type(exc).__name__}") from exc
        if not response.ok:
            raise PersistenceError(f"영속 저장소 저장 실패(HTTP {response.status_code}): {response.text[:160]}")

    def list(self, kind: str) -> list[dict[str, Any]]:
        if not self.configured:
            return []
        try:
            response = self.session.get(
                f"{self.url}/rest/v1/scanner_events",
                headers=self._headers(),
                params={"kind": f"eq.{kind}", "select": "id,created_at,payload", "order": "created_at.asc"},
                timeout=8,
            )
        except requests.RequestException as exc:
            raise PersistenceError(f"영속 저장소 조회 오류: {type(exc).__name__}") from exc
        if not response.ok:
            raise PersistenceError(f"영속 저장소 조회 실패(HTTP {response.status_code}): {response.text[:160]}")
        rows = response.json()
        return [dict(row.get("payload") or {}) for row in rows if isinstance(row, dict)]


@dataclass
class ManualTrade:
    trade_id: str
    created_at: str
    symbol: str
    market: str
    side: str
    entry_price: float
    exit_price: float | None
    quantity: float
    fees: float
    note: str

    @property
    def realized_pnl(self) -> float | None:
        if self.exit_price is None:
            return None
        sign = 1 if self.side == "매수" else -1
        return (self.exit_price - self.entry_price) * self.quantity * sign - self.fees

    @classmethod
    def create(
        cls,
        symbol: str,
        market: str,
        side: str,
        entry_price: float,
        quantity: float,
        exit_price: float | None = None,
        fees: float = 0.0,
        note: str = "",
    ) -> "ManualTrade":
        now = datetime.now().astimezone()
        return cls(
            trade_id=f"trade-{now.strftime('%Y%m%dT%H%M%S%f')}",
            created_at=now.isoformat(),
            symbol=symbol.strip().upper(),
            market=market,
            side=side,
            entry_price=float(entry_price),
            exit_price=float(exit_price) if exit_price is not None else None,
            quantity=float(quantity),
            fees=float(fees),
            note=note.strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["realized_pnl"] = self.realized_pnl
        return payload


def save_manual_trade(store: EventStore, trade: ManualTrade) -> None:
    store.upsert(trade.trade_id, "manual_trade", trade.created_at, trade.to_dict())
