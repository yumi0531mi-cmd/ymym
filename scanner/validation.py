from __future__ import annotations

import json
import csv
import html
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .models import Regime, TradePlan


@dataclass(slots=True)
class HorizonResult:
    minutes: int
    predicted_low: float
    predicted_base: float
    predicted_high: float
    predicted_direction: str
    actual: float | None = None
    range_pass: bool | None = None
    direction_pass: bool | None = None
    pass_all: bool | None = None


@dataclass(slots=True)
class ValidationCase:
    case_id: str
    version: str
    symbol: str
    market: str
    session: str
    signal_time: str
    signal: str
    quote_price: float
    latest_trade_price: float | None
    quote_age_seconds: float | None
    quote_pass: bool | None
    entry: float | None
    predicted_regime: str
    actual_regime: str | None
    regime_pass: bool | None
    target: float | None
    target_basis: str
    stop: float | None
    target_first: bool | None = None
    stopped_first: bool | None = None
    target_pass: bool | None = None
    horizons: list[HorizonResult] = field(default_factory=list)
    full_path_pass: bool | None = None
    complete_four_area_pass: bool | None = None
    mfe_pct: float | None = None
    mae_pct: float | None = None
    net_return_pct: float | None = None
    missing: list[str] = field(default_factory=list)

    @classmethod
    def from_plan(cls, plan: TradePlan, latest_trade_price: float | None, session: str, version: str = "1.0.0"):
        case_id = f"{plan.market.value}-{plan.symbol}-{plan.created_at.strftime('%Y%m%dT%H%M%S%f')}"
        tick_tolerance = max(plan.current_price * 0.0002, 0.01 if plan.market.value == "US" else 1)
        quote_pass = None if latest_trade_price is None else abs(plan.current_price - latest_trade_price) <= tick_tolerance
        horizons = [HorizonResult(f.minutes, f.low, f.base, f.high, f.direction.value) for f in plan.forecasts]
        return cls(case_id=case_id, version=version, symbol=plan.symbol, market=plan.market.value, session=session,
                   signal_time=plan.created_at.isoformat(), signal=plan.signal.value, quote_price=plan.current_price,
                   latest_trade_price=latest_trade_price, quote_age_seconds=None, quote_pass=quote_pass,
                   entry=plan.entry, predicted_regime=plan.regime.value, actual_regime=None, regime_pass=None,
                   target=plan.target, target_basis=plan.target_basis, stop=plan.stop,
                   horizons=horizons, missing=list(plan.missing))

    def score_path(self, actual_prices: dict[int, float], actual_regime: Regime | None = None) -> None:
        origin = self.entry or self.quote_price
        for h in self.horizons:
            actual = actual_prices.get(h.minutes)
            h.actual = actual
            if actual is None:
                continue
            h.range_pass = h.predicted_low <= actual <= h.predicted_high
            actual_direction = Regime.UP.value if actual > origin * 1.0005 else Regime.DOWN.value if actual < origin * .9995 else Regime.RANGE.value
            h.direction_pass = actual_direction == h.predicted_direction
            h.pass_all = bool(h.range_pass and h.direction_pass)
        self.full_path_pass = bool(self.horizons) and all(h.pass_all is True for h in self.horizons)
        if actual_regime is not None:
            self.actual_regime = actual_regime.value
            self.regime_pass = self.actual_regime == self.predicted_regime
        self.complete_four_area_pass = all(x is True for x in (self.quote_pass, self.full_path_pass, self.regime_pass, self.target_pass))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def score_future_bars(self, future: pd.DataFrame, cost_pct: float = 0.10) -> None:
        """Score one signal chronologically. Ambiguous same-bar target/stop is not counted as success."""
        if future.empty:
            return
        frame = future.sort_index().copy()
        origin = self.entry or self.quote_price
        actual_prices: dict[int, float] = {}
        signal_at = datetime.fromisoformat(self.signal_time)
        if signal_at.tzinfo is not None and getattr(frame.index, "tz", None) is None:
            signal_at = signal_at.replace(tzinfo=None)
        for horizon in (5, 10, 15, 30):
            cutoff = signal_at + pd.Timedelta(minutes=horizon)
            eligible = frame.loc[frame.index <= cutoff]
            if not eligible.empty:
                actual_prices[horizon] = float(eligible.close.iloc[-1])

        end = frame.loc[frame.index <= signal_at + pd.Timedelta(minutes=30)]
        if end.empty:
            return
        high = float(end.high.max())
        low = float(end.low.min())
        self.mfe_pct = (high / origin - 1) * 100
        self.mae_pct = (low / origin - 1) * 100
        target_time = None
        stop_time = None
        if self.target is not None:
            touched = end.index[end.high >= self.target]
            target_time = touched[0] if len(touched) else None
        if self.stop is not None:
            touched = end.index[end.low <= self.stop]
            stop_time = touched[0] if len(touched) else None
        if target_time is not None and stop_time is not None and target_time == stop_time:
            self.target_first = False
            self.stopped_first = True
            self.target_pass = False
            self.missing.append("같은 1분봉에서 목표·손절 동시 접촉: 보수적으로 실패 처리")
        else:
            self.target_first = target_time is not None and (stop_time is None or target_time < stop_time)
            self.stopped_first = stop_time is not None and (target_time is None or stop_time < target_time)
            self.target_pass = self.target_first
        exit_price = self.target if self.target_first else self.stop if self.stopped_first else float(end.close.iloc[-1])
        self.net_return_pct = (float(exit_price) / origin - 1) * 100 - cost_pct

        first = float(end.close.iloc[0])
        last = float(end.close.iloc[-1])
        path_range = (float(end.high.max()) - float(end.low.min())) / max(first, 1e-9)
        net = (last - first) / max(first, 1e-9)
        realized = Regime.RANGE if abs(net) < max(0.001, path_range * .25) else Regime.UP if net > 0 else Regime.DOWN
        self.score_path(actual_prices, realized)


class ValidationStore:
    def __init__(self, root: str | Path = ".scanner_data/validation"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def save(self, case: ValidationCase) -> Path:
        day = datetime.fromisoformat(case.signal_time).strftime("%Y-%m-%d")
        folder = self.root / day
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{case.case_id}.json"
        path.write_text(json.dumps(case.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def save_once(self, case: ValidationCase, cooldown_seconds: int = 180) -> tuple[Path, bool]:
        """Persist one raw signal event; UI reruns inside the cooldown reuse it."""
        day = datetime.fromisoformat(case.signal_time).strftime("%Y-%m-%d")
        folder = self.root / day
        folder.mkdir(parents=True, exist_ok=True)
        candidates = sorted(folder.glob(f"{case.market}-{case.symbol}-*.json"), reverse=True)
        for existing in candidates[:10]:
            try:
                row = json.loads(existing.read_text(encoding="utf-8"))
                old_time = datetime.fromisoformat(row["signal_time"])
                new_time = datetime.fromisoformat(case.signal_time)
                same_event = row.get("version") == case.version and row.get("predicted_regime") == case.predicted_regime
                if same_event and abs((new_time - old_time).total_seconds()) < cooldown_seconds:
                    return existing, False
            except Exception:
                continue
        return self.save(case), True

    def update(self, case: ValidationCase) -> Path:
        return self.save(case)

    def export_csv(self, output: str | Path) -> Path:
        rows = self.load_all()
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        fields = ["case_id", "version", "symbol", "market", "session", "signal_time", "signal", "quote_pass",
                  "predicted_regime", "actual_regime", "regime_pass", "target_pass", "full_path_pass",
                  "complete_four_area_pass", "mfe_pct", "mae_pct", "net_return_pct"]
        with path.open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return path

    def cases(self) -> list[ValidationCase]:
        result: list[ValidationCase] = []
        for row in self.load_all():
            try:
                row["horizons"] = [HorizonResult(**item) for item in row.get("horizons", [])]
                result.append(ValidationCase(**row))
            except Exception:
                continue
        return result

    def pending(self, market: str | None = None) -> list[ValidationCase]:
        return [case for case in self.cases() if case.full_path_pass is None and (market is None or case.market == market)]

    def score_ready(self, symbol: str, market: str, bars: pd.DataFrame, cost_pct: float) -> int:
        """Score pending cases only after a complete 30-minute future path exists."""
        if bars.empty:
            return 0
        latest = bars.index.max()
        scored = 0
        for case in self.pending(market):
            if case.symbol != symbol:
                continue
            signal_at = pd.Timestamp(case.signal_time)
            if latest.tz is None and signal_at.tzinfo is not None:
                signal_at = signal_at.tz_localize(None)
            elif latest.tz is not None and signal_at.tzinfo is None:
                signal_at = signal_at.tz_localize(latest.tz)
            if latest < signal_at + pd.Timedelta(minutes=30):
                continue
            future = bars.loc[(bars.index >= signal_at) & (bars.index <= signal_at + pd.Timedelta(minutes=30))]
            if len(future) < 4:
                continue
            case.score_future_bars(future, cost_pct)
            self.update(case)
            scored += 1
        return scored

    def load_all(self) -> list[dict[str, Any]]:
        result = []
        for path in self.root.glob("*/*.json"):
            try:
                result.append(json.loads(path.read_text(encoding="utf-8")))
            except Exception:
                continue
        return result

    def summary(self) -> dict[str, Any]:
        rows = self.load_all()
        complete = [r for r in rows if r.get("complete_four_area_pass") is not None]
        passed = [r for r in complete if r.get("complete_four_area_pass") is True]
        return {"signals": len(rows), "fully_scored": len(complete), "four_area_pass": len(passed),
                "four_area_rate": len(passed) / len(complete) * 100 if complete else None}

    def export_html(self, output: str | Path) -> Path:
        rows = self.load_all()
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        summary = self.summary()
        body = []
        for row in sorted(rows, key=lambda x: x.get("signal_time", "")):
            horizons = row.get("horizons", [])
            path_text = ", ".join(
                f"{h.get('minutes')}분 실제 {h.get('actual')} / 통과 {h.get('pass_all')}" for h in horizons
            )
            body.append(
                "<tr>"
                f"<td>{html.escape(str(row.get('signal_time', '')))}</td>"
                f"<td>{html.escape(str(row.get('market', '')))}</td>"
                f"<td>{html.escape(str(row.get('symbol', '')))}</td>"
                f"<td>{html.escape(str(row.get('signal', '')))}</td>"
                f"<td>{html.escape(path_text)}</td>"
                f"<td>{row.get('mfe_pct')}</td><td>{row.get('mae_pct')}</td>"
                f"<td>{row.get('target_pass')}</td><td>{row.get('complete_four_area_pass')}</td>"
                "</tr>"
            )
        document = f"""<!doctype html><meta charset='utf-8'><title>스캐너 검증 보고서</title>
<style>body{{font-family:Arial,sans-serif;margin:24px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ddd;padding:7px;text-align:left}}th{{background:#f3f5f8}}</style>
<h1>스캐너 원본 신호 검증 보고서</h1>
<p>신호 {summary['signals']}건 · 4영역 채점완료 {summary['fully_scored']}건 · 엄격 통과 {summary['four_area_pass']}건 · 통과율 {summary['four_area_rate']}</p>
<table><thead><tr><th>신호시각</th><th>시장</th><th>종목</th><th>신호</th><th>5~30분 경로</th><th>MFE%</th><th>MAE%</th><th>목표 우선</th><th>4영역 통과</th></tr></thead>
<tbody>{''.join(body)}</tbody></table>"""
        path.write_text(document, encoding="utf-8")
        return path
