from __future__ import annotations

"""Read-only v5.1 minute collector.

Run this only in a continuously running environment after setting KIS and Supabase
secrets as environment variables. It never sends orders. The Streamlit UI remains the
manual decision surface; this process only persists bars and decision diagnostics.
"""

import argparse
import time
from datetime import datetime

import pandas as pd

from scanner.calibration import calibration_for, save_snapshot
from scanner.cycle import CycleStore
from scanner.engine import analyze
from scanner.kis_client import KISClient, KISError
from scanner.models import Market
from scanner.persistence import EventStore, PersistenceError
from scanner.validation import ValidationStore


def _bar_payload(market: Market, symbol: str, at: pd.Timestamp, row: pd.Series) -> dict[str, object]:
    return {
        "market": market.value,
        "symbol": symbol,
        "bar_at": at.isoformat(),
        "open": float(row.open), "high": float(row.high), "low": float(row.low),
        "close": float(row.close), "volume": float(row.volume),
    }


def _load_history(events: EventStore, market: Market, symbol: str, latest: pd.DataFrame) -> pd.DataFrame:
    if not events.configured:
        return latest
    records = []
    try:
        for payload in events.list("minute_bar_v51"):
            if payload.get("market") == market.value and payload.get("symbol") == symbol:
                records.append(payload)
    except PersistenceError:
        return latest
    if not records:
        return latest
    history = pd.DataFrame(records)
    if history.empty:
        return latest
    history.index = pd.to_datetime(history.pop("bar_at"), errors="coerce")
    history = history.loc[history.index.notna(), ["open", "high", "low", "close", "volume"]]
    merged = pd.concat([history, latest]).sort_index()
    return merged[~merged.index.duplicated(keep="last")]


def _persist_bars(events: EventStore, market: Market, symbol: str, bars: pd.DataFrame) -> None:
    if not events.configured:
        return
    for at, row in bars.tail(5).iterrows():
        payload = _bar_payload(market, symbol, pd.Timestamp(at), row)
        identifier = f"bar-v51-{market.value}-{symbol}-{pd.Timestamp(at).strftime('%Y%m%dT%H%M%S')}"
        events.upsert(identifier, "minute_bar_v51", payload["bar_at"], payload)


def collect_one(client: KISClient, events: EventStore, validation: ValidationStore, cycle_store: CycleStore, market: Market, symbol: str, exchange: str) -> None:
    quote = client.quote(symbol, market, exchange, include_orderbook=True)
    fresh = client.intraday(symbol, market, exchange)
    bars = _load_history(events, market, symbol, fresh)
    cycle = cycle_store.get(symbol, market, quote.timestamp)
    first = analyze(
        quote, bars, cooldown_active=cycle.cooldown_active, hard_kill=cycle.hard_kill,
    )
    calibration = calibration_for(
        validation, market=market.value, session=quote.session, strategy=first.strategy, score=first.score,
    )
    plan = analyze(
        quote,
        bars,
        cooldown_active=cycle.cooldown_active,
        hard_kill=cycle.hard_kill,
        calibration_probability=calibration.probability_pct,
        calibration_samples=calibration.samples,
    )
    marker = str(plan.diagnostics.get("completed_bar_at") or quote.timestamp.isoformat())
    cycle_store.apply_risk_state(cycle, plan.risk_state, marker)
    _persist_bars(events, market, symbol, bars)
    signal = plan.to_dict()
    signal.update({"session": quote.session, "trade_date": cycle.trade_date, "cycle": cycle.to_dict()})
    identifier = f"signal-v51-{market.value}-{symbol}-{quote.timestamp.strftime('%Y%m%dT%H%M%S')}"
    events.upsert(identifier, "signal_event_v51", quote.timestamp.isoformat(), signal)
    save_snapshot(events, calibration, quote.timestamp.isoformat())
    print(
        f"{quote.timestamp.isoformat()} {market.value} {symbol} {plan.signal.value} "
        f"persistence={plan.persistence_score} risk={plan.risk_state} samples={calibration.samples}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="v5.1 read-only repeated-scalping collector")
    parser.add_argument("--market", choices=["KR", "US"], required=True)
    parser.add_argument("--symbols", required=True, help="Comma-separated codes/tickers")
    parser.add_argument("--exchange", default="NAS")
    parser.add_argument("--interval", type=int, default=60, help="Seconds between collection cycles; minimum 15")
    parser.add_argument("--once", action="store_true", help="Collect one cycle and stop")
    args = parser.parse_args()
    interval = max(15, args.interval)
    market = Market(args.market)
    symbols = [value.strip().upper() for value in args.symbols.split(",") if value.strip()]
    client = KISClient()
    events = EventStore()
    if not client.configured or not client.access_token:
        raise SystemExit("KIS_APP_KEY, KIS_APP_SECRET, KIS_ACCESS_TOKEN 환경 변수가 필요합니다.")
    if not events.configured:
        raise SystemExit("SUPABASE_URL과 SUPABASE_KEY 환경 변수가 필요합니다. 로컬 파일은 5시간 상태 원본으로 사용할 수 없습니다.")
    validation = ValidationStore(".scanner_data/validation", event_store=events)
    cycle_store = CycleStore(events)
    while True:
        started = time.monotonic()
        for symbol in symbols:
            try:
                collect_one(client, events, validation, cycle_store, market, symbol, args.exchange)
            except (KISError, PersistenceError, ValueError, KeyError) as exc:
                print(f"{datetime.now().astimezone().isoformat()} {symbol} collection error: {type(exc).__name__}: {exc}")
        if args.once:
            return 0
        time.sleep(max(1.0, interval - (time.monotonic() - started)))


if __name__ == "__main__":
    raise SystemExit(main())
