"""Technical indicators on plain float lists (no pandas).

Every function returns a list the same length as its input; positions without enough
history are ``None``.
"""

from __future__ import annotations

import math

Series = list[float | None]


def sma(values: list[float], period: int) -> Series:
    out: Series = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= period:
            total -= values[i - period]
        if i >= period - 1:
            out[i] = total / period
    return out


def ema(values: Series, period: int) -> Series:
    """EMA seeded with the SMA of the first ``period`` non-None values."""
    out: Series = [None] * len(values)
    k = 2 / (period + 1)
    seed: list[float] = []
    prev: float | None = None
    for i, v in enumerate(values):
        if v is None:
            continue
        if prev is None:
            seed.append(v)
            if len(seed) == period:
                prev = sum(seed) / period
                out[i] = prev
            continue
        prev = v * k + prev * (1 - k)
        out[i] = prev
    return out


def rsi(closes: list[float], period: int = 14) -> Series:
    """Wilder's RSI."""
    out: Series = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    avg_g, avg_l = gains / period, losses / period

    def value(g: float, lo: float) -> float:
        return 100.0 if lo == 0 else 100 - 100 / (1 + g / lo)

    out[period] = value(avg_g, avg_l)
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_g = (avg_g * (period - 1) + max(d, 0)) / period
        avg_l = (avg_l * (period - 1) + max(-d, 0)) / period
        out[i] = value(avg_g, avg_l)
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[Series, Series, Series]:
    ef, es = ema(closes, fast), ema(closes, slow)
    line: Series = [None if a is None or b is None else a - b for a, b in zip(ef, es)]
    sig = ema(line, signal)
    hist: Series = [None if a is None or b is None else a - b for a, b in zip(line, sig)]
    return line, sig, hist


def bollinger(closes: list[float], period: int = 20, mult: float = 2.0) -> tuple[Series, Series, Series]:
    mid = sma(closes, period)
    upper: Series = [None] * len(closes)
    lower: Series = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = closes[i - period + 1 : i + 1]
        m = mid[i]
        sd = math.sqrt(sum((x - m) ** 2 for x in window) / period)
        upper[i], lower[i] = m + mult * sd, m - mult * sd
    return mid, upper, lower


def stochastic_kd(highs: list[float], lows: list[float], closes: list[float],
                  period: int = 9, k_smooth: int = 3, d_smooth: int = 3) -> tuple[Series, Series]:
    """Taiwan-style KD: RSV smoothed with 1/k weights, starting from 50."""
    k_out: Series = [None] * len(closes)
    d_out: Series = [None] * len(closes)
    k = d = 50.0
    for i in range(period - 1, len(closes)):
        hi, lo = max(highs[i - period + 1 : i + 1]), min(lows[i - period + 1 : i + 1])
        rsv = 50.0 if hi == lo else (closes[i] - lo) / (hi - lo) * 100
        k = k * (k_smooth - 1) / k_smooth + rsv / k_smooth
        d = d * (d_smooth - 1) / d_smooth + k / d_smooth
        k_out[i], d_out[i] = k, d
    return k_out, d_out


def williams_r(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> Series:
    out: Series = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        hi, lo = max(highs[i - period + 1 : i + 1]), min(lows[i - period + 1 : i + 1])
        out[i] = -50.0 if hi == lo else (hi - closes[i]) / (hi - lo) * -100
    return out


def cci(highs: list[float], lows: list[float], closes: list[float], period: int = 20) -> Series:
    tp = [(h + lo + c) / 3 for h, lo, c in zip(highs, lows, closes)]
    ma = sma(tp, period)
    out: Series = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        window = tp[i - period + 1 : i + 1]
        md = sum(abs(x - ma[i]) for x in window) / period
        out[i] = 0.0 if md == 0 else (tp[i] - ma[i]) / (0.015 * md)
    return out


def atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> Series:
    """Wilder's average true range."""
    out: Series = [None] * len(closes)
    trs = []
    for i in range(len(closes)):
        if i == 0:
            trs.append(highs[0] - lows[0])
        else:
            pc = closes[i - 1]
            trs.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))
    if len(trs) < period:
        return out
    a = sum(trs[:period]) / period
    out[period - 1] = a
    for i in range(period, len(trs)):
        a = (a * (period - 1) + trs[i]) / period
        out[i] = a
    return out


def bias(closes: list[float], period: int = 60) -> Series:
    """乖離率 (%): distance of the close from its moving average."""
    ma = sma(closes, period)
    return [None if m is None else (c / m - 1) * 100 for c, m in zip(closes, ma)]


def rolling_zscore(values: Series, lookback: int) -> Series:
    """Z-score of each value against the previous ``lookback`` non-None values (inclusive)."""
    out: Series = [None] * len(values)
    window: list[float] = []
    for i, v in enumerate(values):
        if v is None:
            continue
        window.append(v)
        if len(window) > lookback:
            window.pop(0)
        if len(window) >= 30:
            m = sum(window) / len(window)
            sd = math.sqrt(sum((x - m) ** 2 for x in window) / len(window))
            out[i] = 0.0 if sd == 0 else (v - m) / sd
    return out
