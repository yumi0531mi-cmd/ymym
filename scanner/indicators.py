from __future__ import annotations

import numpy as np
import pandas as pd


REQUIRED = ("open", "high", "low", "close", "volume")


def normalize_bars(frame: pd.DataFrame) -> pd.DataFrame:
    df = frame.copy()
    df.columns = [str(c).lower() for c in df.columns]
    missing = [c for c in REQUIRED if c not in df]
    if missing:
        raise ValueError(f"분봉 필수 열 누락: {', '.join(missing)}")
    for c in REQUIRED:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=list(REQUIRED)).sort_index()
    return df[(df["high"] >= df["low"]) & (df["close"] > 0) & (df["volume"] >= 0)]


def enrich(frame: pd.DataFrame) -> pd.DataFrame:
    df = normalize_bars(frame)
    typical = (df.high + df.low + df.close) / 3
    cumulative_volume = df.volume.cumsum().replace(0, np.nan)
    df["vwap"] = (typical * df.volume).cumsum() / cumulative_volume
    for n in (9, 20, 60):
        df[f"ema{n}"] = df.close.ewm(span=n, adjust=False).mean()
    delta = df.close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    df["rsi"] = (100 - 100 / (1 + rs)).fillna(50)
    tr = pd.concat([(df.high - df.low), (df.high - df.close.shift()).abs(), (df.low - df.close.shift()).abs()], axis=1).max(axis=1)
    df["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    avg_volume = df.volume.rolling(20, min_periods=5).mean()
    df["rvol"] = (df.volume / avg_volume.replace(0, np.nan)).fillna(0)
    return df


def resample(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    if minutes == 1:
        return frame.copy()
    rule = f"{minutes}min"
    return frame.resample(rule, label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()

