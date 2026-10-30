"""Score each bar from -100 (relative high: exit) to +100 (relative low: entry).

Every indicator casts a vote in [-1, 1] (positive = oversold / buy side); the score is the
weighted average × 100, then scaled by a trend filter: a buy signal inside an uptrend
(a pullback) is strengthened, one inside a downtrend (catching a falling knife) is damped,
and the same for sell signals.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field

from . import indicators as ta
from .config import Config, MetalSpec
from .yahoo import Bar

LEVELS = {
    "strong_buy": ("強烈進場訊號", "相對低點,多項指標同時超賣"),
    "buy": ("留意進場", "價格偏低,可分批布局"),
    "neutral": ("中性觀望", "沒有明確的高低點訊號"),
    "sell": ("留意出場", "價格偏高,可分批獲利了結"),
    "strong_sell": ("強烈出場訊號", "相對高點,多項指標同時超買"),
}
DIRECTION = {"strong_buy": "buy", "buy": "buy", "neutral": "", "sell": "sell", "strong_sell": "sell"}


def level_for(score: float, cut: dict[str, float]) -> str:
    if score >= cut["strong_buy"]:
        return "strong_buy"
    if score >= cut["buy"]:
        return "buy"
    if score <= cut["strong_sell"]:
        return "strong_sell"
    if score <= cut["sell"]:
        return "sell"
    return "neutral"


def _clamp(x: float, lo: float = -1.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class Vote:
    key: str
    name: str
    value: str       # 指標數值(顯示用)
    vote: float      # -1 ~ 1,正 = 偏買
    weight: float
    comment: str


@dataclass
class Series:
    """All indicator series for one instrument, computed once."""
    bars: list[Bar]
    closes: list[float]
    rsi: list
    k: list
    d: list
    macd: list
    macd_signal: list
    macd_hist: list
    bb_mid: list
    bb_upper: list
    bb_lower: list
    wr: list
    cci: list
    bias: list
    bias_z: list
    atr: list
    ema_trend: list
    range_lo: list
    range_hi: list
    daily_ts: list[int] = field(default_factory=list)
    daily_ma: list = field(default_factory=list)
    daily_close: list[float] = field(default_factory=list)


def compute_series(bars: list[Bar], daily: list[Bar], cfg: Config) -> Series:
    c = [b.close for b in bars]
    h = [b.high for b in bars]
    lo = [b.low for b in bars]
    p = cfg.ind
    m_line, m_sig, m_hist = ta.macd(c, p("macd").get("fast", 12), p("macd").get("slow", 26), p("macd").get("signal", 9))
    mid, up, low = ta.bollinger(c, p("bollinger").get("period", 20), p("bollinger").get("std", 2.0))
    k, d = ta.stochastic_kd(h, lo, c, p("kd").get("period", 9), p("kd").get("k", 3), p("kd").get("d", 3))
    bias = ta.bias(c, p("bias").get("period", 60))

    # High / low of the last N days, by timestamp (hourly bars skip weekends and holidays).
    span = int(p("range").get("days", 20)) * 86400
    rlo: list = [None] * len(bars)
    rhi: list = [None] * len(bars)
    start = 0
    for i, b in enumerate(bars):
        while bars[start].ts < b.ts - span:
            start += 1
        if b.ts - bars[0].ts >= span * 0.5:  # need at least half the window
            rlo[i], rhi[i] = min(lo[start : i + 1]), max(h[start : i + 1])

    dc = [b.close for b in daily]
    return Series(
        bars=bars, closes=c, rsi=ta.rsi(c, p("rsi").get("period", 14)), k=k, d=d,
        macd=m_line, macd_signal=m_sig, macd_hist=m_hist, bb_mid=mid, bb_upper=up, bb_lower=low,
        wr=ta.williams_r(h, lo, c, p("williams").get("period", 14)),
        cci=ta.cci(h, lo, c, p("cci").get("period", 20)),
        bias=bias, bias_z=ta.rolling_zscore(bias, int(p("bias").get("lookback", 500))),
        atr=ta.atr(h, lo, c, p("atr").get("period", 14)),
        ema_trend=ta.ema(c, int(p("trend").get("ema", 200))),
        range_lo=rlo, range_hi=rhi,
        daily_ts=[b.ts for b in daily], daily_ma=ta.sma(dc, int(p("trend").get("daily_ma", 50))), daily_close=dc,
    )


def _votes(s: Series, i: int, cfg: Config) -> list[Vote]:
    p = cfg.ind
    c = s.closes[i]
    out: list[Vote] = []

    def add(key, name, value, vote, comment):
        w = float(p(key).get("weight", 1.0))
        if w > 0:
            out.append(Vote(key, name, value, round(_clamp(vote), 3), w, comment))

    r = s.rsi[i]
    if r is not None:
        lo_, hi_ = p("rsi").get("low", 30), p("rsi").get("high", 70)
        mid = (lo_ + hi_) / 2
        if r <= lo_:
            v, msg = 0.6 + 0.4 * min(1, (lo_ - r) / 10), "超賣"
        elif r >= hi_:
            v, msg = -(0.6 + 0.4 * min(1, (r - hi_) / 10)), "超買"
        else:
            v, msg = 0.3 * (mid - r) / (mid - lo_), "區間內"
        add("rsi", "RSI 相對強弱", f"{r:.1f}", v, f"{msg}(<{lo_} 超賣、>{hi_} 超買)")

    k, d = s.k[i], s.d[i]
    if k is not None and i > 0 and s.k[i - 1] is not None:
        lo_, hi_ = p("kd").get("low", 20), p("kd").get("high", 80)
        golden = s.k[i - 1] <= s.d[i - 1] and k > d
        death = s.k[i - 1] >= s.d[i - 1] and k < d
        if golden and k < lo_ + 10:
            v, msg = 1.0, "低檔黃金交叉"
        elif death and k > hi_ - 10:
            v, msg = -1.0, "高檔死亡交叉"
        elif k < lo_:
            v, msg = 0.5, "低檔鈍化區"
        elif k > hi_:
            v, msg = -0.5, "高檔鈍化區"
        else:
            v, msg = 0.0, "區間內"
        add("kd", "KD 隨機指標", f"K {k:.1f} / D {d:.1f}", v, msg)

    hist = s.macd_hist[i]
    if hist is not None and i > 1 and s.macd_hist[i - 1] is not None and s.macd_hist[i - 2] is not None:
        prev, prev2, line = s.macd_hist[i - 1], s.macd_hist[i - 2], s.macd[i]
        if prev <= 0 < hist:
            v, msg = (0.8, "零軸下黃金交叉") if line < 0 else (0.5, "黃金交叉")
        elif prev >= 0 > hist:
            v, msg = (-0.8, "零軸上死亡交叉") if line > 0 else (-0.5, "死亡交叉")
        elif hist < 0 and hist > prev > prev2:
            v, msg = 0.3, "空方動能收斂"
        elif hist > 0 and hist < prev < prev2:
            v, msg = -0.3, "多方動能收斂"
        else:
            v, msg = 0.0, "多方動能" if hist > 0 else "空方動能"
        add("macd", "MACD", f"柱狀 {hist:+.2f}", v, msg)

    up, low = s.bb_upper[i], s.bb_lower[i]
    if up is not None and up > low:
        pb = (c - low) / (up - low)
        if pb <= 0:
            v, msg = 1.0, "跌破下軌"
        elif pb < 0.1:
            v, msg = 0.7, "貼近下軌"
        elif pb < 0.2:
            v, msg = 0.4, "接近下軌"
        elif pb >= 1:
            v, msg = -1.0, "突破上軌"
        elif pb > 0.9:
            v, msg = -0.7, "貼近上軌"
        elif pb > 0.8:
            v, msg = -0.4, "接近上軌"
        else:
            v, msg = 0.0, "通道中段"
        add("bollinger", "布林通道 %B", f"{pb * 100:.0f}%", v, msg)

    wr = s.wr[i]
    if wr is not None:
        lo_, hi_ = p("williams").get("low", -80), p("williams").get("high", -20)
        v, msg = (0.8, "超賣") if wr <= lo_ else (-0.8, "超買") if wr >= hi_ else (0.0, "區間內")
        add("williams", "威廉指標 %R", f"{wr:.1f}", v, msg)

    cc = s.cci[i]
    if cc is not None:
        lo_, hi_ = p("cci").get("low", -100), p("cci").get("high", 100)
        if cc <= lo_:
            v, msg = 0.6 + (lo_ - cc) / 250, "超賣"
        elif cc >= hi_:
            v, msg = -(0.6 + (cc - hi_) / 250), "超買"
        else:
            v, msg = 0.0, "區間內"
        add("cci", "CCI 順勢指標", f"{cc:.0f}", v, msg)

    z, b = s.bias_z[i], s.bias[i]
    if z is not None:
        zt = float(p("bias").get("z", 2.0))
        if z <= -zt:
            v, msg = 1.0, "負乖離過大"
        elif z <= -zt / 2:
            v, msg = 0.5, "負乖離偏大"
        elif z >= zt:
            v, msg = -1.0, "正乖離過大"
        elif z >= zt / 2:
            v, msg = -0.5, "正乖離偏大"
        else:
            v, msg = 0.0, "乖離正常"
        add("bias", f"乖離率(MA{p('bias').get('period', 60)})", f"{b:+.2f}%(Z {z:+.1f})", v, msg)

    rl, rh = s.range_lo[i], s.range_hi[i]
    if rl is not None and rh > rl:
        pos = (c - rl) / (rh - rl)
        if pos <= 0.1:
            v, msg = 1.0, "近區間低點"
        elif pos <= 0.25:
            v, msg = 0.5, "區間下緣"
        elif pos >= 0.9:
            v, msg = -1.0, "近區間高點"
        elif pos >= 0.75:
            v, msg = -0.5, "區間上緣"
        else:
            v, msg = 0.0, "區間中段"
        add("range", f"近 {p('range').get('days', 20)} 日區間位置", f"{pos * 100:.0f}%", v, msg)
    return out


def trend_at(s: Series, i: int, cfg: Config) -> tuple[int, str]:
    """+1 uptrend, -1 downtrend, 0 sideways: hourly EMA (price above + rising) and daily MA."""
    e = s.ema_trend[i]
    back = s.ema_trend[i - 24] if i >= 24 else None
    if e is None or back is None:
        return 0, "資料不足"
    c = s.closes[i]
    t = 1 if c > e and e > back else -1 if c < e and e < back else 0
    # Daily MA from fully completed days only (no look-ahead in the backtest).
    j = bisect.bisect_right(s.daily_ts, s.bars[i].ts - 86400) - 1
    dm = s.daily_ma[j] if 0 <= j < len(s.daily_ma) else None
    n = cfg.ind("trend").get("daily_ma", 50)
    if dm is not None:
        daily_up = s.daily_close[j] > dm
        if t == 0:
            t = 1 if daily_up and c > e else -1 if not daily_up and c < e else 0
        elif (t == 1) != daily_up:
            t = 0  # hourly and daily disagree
    label = {1: "多頭趨勢", -1: "空頭趨勢", 0: "盤整"}[t]
    detail = f"EMA{cfg.ind('trend').get('ema', 200)} {e:,.2f}" + (f"、日線 MA{n} {dm:,.2f}" if dm is not None else "")
    return t, f"{label}({detail})"


def score_at(s: Series, i: int, cfg: Config) -> tuple[float, list[Vote], int, str]:
    votes = _votes(s, i, cfg)
    wsum = sum(v.weight for v in votes)
    raw = sum(v.vote * v.weight for v in votes) / wsum * 100 if wsum else 0.0
    trend, trend_text = trend_at(s, i, cfg)
    w = float(cfg.ind("trend").get("weight", 1.0))
    if trend and raw:
        with_trend = (raw > 0) == (trend > 0)
        raw *= 1 + 0.15 * w if with_trend else max(0.0, 1 - 0.3 * w)
    return max(-100.0, min(100.0, raw)), votes, trend, trend_text


def _r(x, nd=2):
    return None if x is None else round(x, nd)


def backtest(s: Series, scores: list[float | None], cfg: Config) -> dict:
    """Forward return after each entry into the buy / sell zone (first bar of the zone)."""
    n = cfg.forward_bars
    stats = {}
    for side, inside in (("buy", lambda x: x >= cfg.levels["buy"]), ("sell", lambda x: x <= cfg.levels["sell"])):
        rets = []
        for i in range(1, len(scores) - n):
            if scores[i] is None or scores[i - 1] is None:
                continue
            if inside(scores[i]) and not inside(scores[i - 1]):
                rets.append((s.closes[i + n] / s.closes[i] - 1) * 100)
        wins = [r for r in rets if (r > 0 if side == "buy" else r < 0)]
        stats[side] = {
            "signals": len(rets),
            "win_rate": _r(len(wins) / len(rets) * 100, 1) if rets else None,
            "avg_return": _r(sum(rets) / len(rets), 2) if rets else None,
        }
    stats["forward_bars"] = n
    return stats


def analyse(spec: MetalSpec, bars: list[Bar], daily: list[Bar], cfg: Config) -> dict:
    """Full assessment of one metal at its latest bar, plus chart and backtest data."""
    s = compute_series(bars, daily, cfg)
    warm = max(30, int(cfg.ind("macd").get("slow", 26)) + int(cfg.ind("macd").get("signal", 9)))
    scores: list[float | None] = [None] * len(bars)
    for i in range(warm, len(bars)):
        scores[i] = round(score_at(s, i, cfg)[0], 1)
    i = len(bars) - 1
    score, votes, trend, trend_text = score_at(s, i, cfg)
    level = level_for(score, cfg.levels)
    c = s.closes[i]
    a = s.atr[i]
    atr_cfg = cfg.ind("atr")
    sm, tm = float(atr_cfg.get("stop_mult", 2.0)), float(atr_cfg.get("target_mult", 3.0))
    prev_day = daily[-2].close if len(daily) >= 2 else None
    back24 = s.closes[i - 24] if i >= 24 else None

    start = max(0, len(bars) - cfg.chart_bars)
    markers = []
    for j in range(max(start, 1), len(bars)):
        if scores[j] is None or scores[j - 1] is None:
            continue
        lv, plv = level_for(scores[j], cfg.levels), level_for(scores[j - 1], cfg.levels)
        if DIRECTION[lv] and DIRECTION[lv] != DIRECTION[plv]:
            markers.append({"i": j - start, "side": DIRECTION[lv], "price": round(s.closes[j], 2), "score": scores[j]})

    return {
        "name": spec.name, "code": spec.code, "symbol": spec.symbol, "unit": spec.unit,
        "ts": bars[i].ts, "price": round(c, 2),
        "change_24h": _r((c / back24 - 1) * 100) if back24 else None,
        "change_day": _r((c / prev_day - 1) * 100) if prev_day else None,
        "score": round(score, 1), "level": level, "level_name": LEVELS[level][0], "level_hint": LEVELS[level][1],
        "trend": trend, "trend_text": trend_text,
        "votes": [v.__dict__ for v in votes],
        "plan": {
            "atr": _r(a),
            "support": _r(min(x for x in (s.bb_lower[i], s.range_lo[i]) if x is not None)) if s.bb_lower[i] else None,
            "resistance": _r(max(x for x in (s.bb_upper[i], s.range_hi[i]) if x is not None)) if s.bb_upper[i] else None,
            "range_low": _r(s.range_lo[i]), "range_high": _r(s.range_hi[i]),
            "buy_stop": _r(c - sm * a) if a else None, "buy_target": _r(c + tm * a) if a else None,
            "sell_stop": _r(c + sm * a) if a else None, "sell_target": _r(c - tm * a) if a else None,
        },
        "backtest": backtest(s, scores, cfg),
        "chart": {
            "ts": [b.ts for b in bars[start:]],
            "open": [_r(b.open) for b in bars[start:]], "high": [_r(b.high) for b in bars[start:]],
            "low": [_r(b.low) for b in bars[start:]], "close": [_r(b.close) for b in bars[start:]],
            "bb_upper": [_r(x) for x in s.bb_upper[start:]], "bb_mid": [_r(x) for x in s.bb_mid[start:]],
            "bb_lower": [_r(x) for x in s.bb_lower[start:]], "ema": [_r(x) for x in s.ema_trend[start:]],
            "rsi": [_r(x, 1) for x in s.rsi[start:]], "k": [_r(x, 1) for x in s.k[start:]],
            "d": [_r(x, 1) for x in s.d[start:]], "macd_hist": [_r(x, 3) for x in s.macd_hist[start:]],
            "score": scores[start:], "markers": markers,
        },
    }
