from __future__ import annotations

from dataclasses import dataclass

from .models import Market, Quote


@dataclass(frozen=True, slots=True)
class UniverseItem:
    symbol: str
    name: str
    exchange: str = "NAS"


# A transparent, liquid starter universe. It is not represented as the whole market.
KR_LIQUID = (
    UniverseItem("005930", "삼성전자"), UniverseItem("000660", "SK하이닉스"),
    UniverseItem("035420", "NAVER"), UniverseItem("035720", "카카오"),
    UniverseItem("005380", "현대차"), UniverseItem("000270", "기아"),
    UniverseItem("068270", "셀트리온"), UniverseItem("105560", "KB금융"),
    UniverseItem("055550", "신한지주"), UniverseItem("012330", "현대모비스"),
    UniverseItem("069500", "KODEX 200"), UniverseItem("102110", "TIGER 200"),
    UniverseItem("122630", "KODEX 레버리지"), UniverseItem("252670", "KODEX 200선물인버스2X"),
)

US_LIQUID = (
    UniverseItem("AAPL", "애플"), UniverseItem("MSFT", "마이크로소프트"),
    UniverseItem("NVDA", "엔비디아"), UniverseItem("AMZN", "아마존"),
    UniverseItem("META", "메타"), UniverseItem("GOOGL", "알파벳"),
    UniverseItem("TSLA", "테슬라"), UniverseItem("AMD", "AMD"),
    UniverseItem("AVGO", "브로드컴"), UniverseItem("PLTR", "팔란티어"),
    UniverseItem("QQQ", "나스닥100 ETF"), UniverseItem("SPY", "S&P500 ETF", "AMS"),
    UniverseItem("IWM", "러셀2000 ETF", "AMS"), UniverseItem("SOXL", "반도체 3배 레버리지"),
    UniverseItem("SOXS", "반도체 3배 인버스"), UniverseItem("TQQQ", "나스닥100 3배 레버리지"),
    UniverseItem("SQQQ", "나스닥100 3배 인버스"), UniverseItem("UPRO", "S&P500 3배 레버리지", "AMS"),
    UniverseItem("SPXU", "S&P500 3배 인버스", "AMS"), UniverseItem("TNA", "러셀2000 3배 레버리지", "AMS"),
    UniverseItem("TZA", "러셀2000 3배 인버스", "AMS"),
)


def rank_quotes(quotes: list[Quote], market: Market, minimum_change: float = .5) -> list[Quote]:
    """First-stage ranking only; final entry still requires bars and orderbook."""
    valid = []
    for quote in quotes:
        if quote.price <= 0 or quote.change_pct < minimum_change:
            continue
        if market == Market.KR and quote.change_pct >= 25:
            continue
        valid.append(quote)
    return sorted(valid, key=lambda q: (q.change_pct, q.turnover or 0), reverse=True)
