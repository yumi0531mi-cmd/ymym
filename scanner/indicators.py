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
    df["notional"] = df.close * df.volume
    notional_5m = df["notional"].rolling(5, min_periods=5).sum()
    baseline_notional = notional_5m.rolling(20, min_periods=10).median()
    df["notional_rvol"] = (notional_5m / baseline_notional.replace(0, np.nan)).fillna(0)
    df["atr_pct"] = (df["atr"] / df.close.replace(0, np.nan) * 100).fillna(0)
    body_high = df[["open", "close"]].max(axis=1)
    body_low = df[["open", "close"]].min(axis=1)
    df["body"] = (df.close - df.open).abs()
    df["upper_wick"] = (df.high - body_high).clip(lower=0)
    df["lower_wick"] = (body_low - df.low).clip(lower=0)
    lowest_14 = df.low.rolling(14, min_periods=8).min()
    highest_14 = df.high.rolling(14, min_periods=8).max()
    df["stoch_k"] = ((df.close - lowest_14) / (highest_14 - lowest_14).replace(0, np.nan) * 100).fillna(50)
    df["stoch_d"] = df.stoch_k.rolling(3, min_periods=2).mean().fillna(df.stoch_k)
    fast_ema = df.close.ewm(span=12, adjust=False).mean()
    slow_ema = df.close.ewm(span=26, adjust=False).mean()
    df["macd"] = fast_ema - slow_ema
    df["macd_signal"] = df.macd.ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df.macd - df.macd_signal
    up_move = df.high.diff()
    down_move = -df.low.diff()
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False).mean() / df.atr.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False).mean() / df.atr.replace(0, np.nan)
    df["plus_di"] = plus_di.fillna(0)
    df["minus_di"] = minus_di.fillna(0)
    dx = (df.plus_di - df.minus_di).abs() / (df.plus_di + df.minus_di).replace(0, np.nan) * 100
    df["adx"] = dx.ewm(alpha=1 / 14, adjust=False).mean().fillna(0)
    middle = df.close.rolling(20, min_periods=10).mean()
    deviation = df.close.rolling(20, min_periods=10).std(ddof=0)
    df["boll_mid"] = middle.fillna(df.close)
    df["boll_upper"] = (middle + deviation * 2).fillna(df.close)
    df["boll_lower"] = (middle - deviation * 2).fillna(df.close)
    df["boll_width_pct"] = ((df.boll_upper - df.boll_lower) / df.boll_mid.replace(0, np.nan) * 100).fillna(0)
    df["boll_pct_b"] = ((df.close - df.boll_lower) / (df.boll_upper - df.boll_lower).replace(0, np.nan)).fillna(0.5)
    signed_volume = np.sign(df.close.diff()).fillna(0) * df.volume
    df["obv"] = signed_volume.cumsum()
    money_flow_multiplier = ((df.close - df.low) - (df.high - df.close)) / (df.high - df.low).replace(0, np.nan)
    df["cmf"] = (money_flow_multiplier.fillna(0) * df.volume).rolling(20, min_periods=8).sum() / df.volume.rolling(20, min_periods=8).sum().replace(0, np.nan)
    df["cmf"] = df.cmf.fillna(0)
    raw_money_flow = typical * df.volume
    positive_flow = raw_money_flow.where(typical.diff() > 0, 0.0).rolling(14, min_periods=8).sum()
    negative_flow = raw_money_flow.where(typical.diff() < 0, 0.0).rolling(14, min_periods=8).sum()
    money_ratio = positive_flow / negative_flow.replace(0, np.nan)
    df["mfi"] = (100 - 100 / (1 + money_ratio)).where(negative_flow > 0, np.where(positive_flow > 0, 100.0, 50.0))
    df["roc10"] = (df.close.pct_change(10) * 100).fillna(0)
    df["ema9_slope"] = df.ema9.pct_change(3).fillna(0)
    df["ema20_slope"] = df.ema20.pct_change(5).fillna(0)
    df["regression_slope"] = df.close.rolling(20, min_periods=8).apply(
        lambda values: np.polyfit(np.arange(len(values), dtype=float), values, 1)[0] / max(float(values[-1]), 1e-9),
        raw=True,
    ).fillna(0)
    return df


def resample(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    if minutes == 1:
        return frame.copy()
    rule = f"{minutes}min"
    return frame.resample(rule, label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()


def resample_completed(frame: pd.DataFrame, minutes: int) -> pd.DataFrame:
    """Return only fully formed higher-timeframe bars from one-minute observations."""
    df = normalize_bars(frame)
    if minutes == 1:
        return df
    rule = f"{minutes}min"
    grouped = resample(df, minutes)
    counts = df.close.resample(rule, label="right", closed="right").count()
    valid = counts[counts >= minutes].index
    return grouped.loc[grouped.index.intersection(valid)].copy()
