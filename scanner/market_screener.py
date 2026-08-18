from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from .models import Market


# General ETF/ETN products remain eligible. Only domestic directional
# leveraged/inverse products are excluded at the user's request. U.S.
# leveraged and inverse ETFs stay in the candidate universe.
KR_DIRECTIONAL_PRODUCT_TOKENS = (
    "레버리지", "인버스", "곱버스", "LEVERAGE", "INVERSE",
)


def is_kr_directional_product(name: str) -> bool:
    normalized = str(name or "").upper().replace(" ", "")
    return any(token.replace(" ", "") in normalized for token in KR_DIRECTIONAL_PRODUCT_TOKENS)


@dataclass
class MarketCandidate:
    symbol: str
    name: str
    market: Market
    exchange: str = ""
    sources: set[str] = field(default_factory=set)
    price: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    turnover: float | None = None

    @property
    def screen_score(self) -> int:
        return sum(45 if source == "거래대금·거래량 순위" else 35 for source in self.sources) + (20 if len(self.sources) >= 2 else 0)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["market"] = self.market.value
        payload["sources"] = " · ".join(sorted(self.sources))
        payload["screen_score"] = self.screen_score
        return payload


def _first_text(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _first_number(row: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        raw = row.get(key)
        if raw in (None, ""):
            continue
        try:
            return float(str(raw).replace(",", ""))
        except (TypeError, ValueError):
            continue
    return None


def _candidate_from_row(row: dict[str, Any], market: Market, source: str) -> MarketCandidate | None:
    symbol = _first_text(row, ("mksc_shrn_iscd", "stck_shrn_iscd", "iscd", "symb", "symbol", "ticker"))
    if not symbol:
        return None
    name = _first_text(row, ("hts_kor_isnm", "hts_kor_isnm", "ovrs_item_name", "name", "ename", "item_name")) or symbol
    if market == Market.KR and is_kr_directional_product(name):
        return None
    return MarketCandidate(
        symbol=symbol.upper(),
        name=name,
        market=market,
        exchange=_first_text(row, ("_exchange", "excd", "exchange")),
        sources={source},
        price=_first_number(row, ("stck_prpr", "last", "ovrs_nmix_prpr", "price", "close")),
        change_pct=_first_number(row, ("prdy_ctrt", "rate", "change_rate", "fluctuation_rate", "change_pct")),
        volume=_first_number(row, ("acml_vol", "tvol", "volume", "trade_vol")),
        turnover=_first_number(row, ("acml_tr_pbmn", "tamt", "trade_pbmn", "turnover", "amount")),
    )


def merge_rankings(market: Market, rankings: dict[str, Iterable[dict[str, Any]]], limit: int = 20) -> list[MarketCandidate]:
    """Merge first-page KIS rank results without fetching per-symbol details."""
    candidates: dict[str, MarketCandidate] = {}
    for source, rows in rankings.items():
        for row in rows:
            if not isinstance(row, dict):
                continue
            candidate = _candidate_from_row(row, market, source)
            if candidate is None:
                continue
            existing = candidates.get(candidate.symbol)
            if existing is None:
                candidates[candidate.symbol] = candidate
                continue
            existing.sources.update(candidate.sources)
            existing.exchange = existing.exchange or candidate.exchange
            existing.price = existing.price if existing.price is not None else candidate.price
            existing.change_pct = existing.change_pct if existing.change_pct is not None else candidate.change_pct
            existing.volume = existing.volume if existing.volume is not None else candidate.volume
            existing.turnover = existing.turnover if existing.turnover is not None else candidate.turnover
    return sorted(
        candidates.values(),
        key=lambda item: (item.screen_score, item.turnover or 0.0, item.volume or 0.0, item.change_pct or -999.0),
        reverse=True,
    )[:max(1, limit)]
