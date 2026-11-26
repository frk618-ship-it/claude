"""One-week (5 trading days) price forecast for each metal, from daily closes.

Model, kept deliberately small because a week of a metal price is mostly noise:

* volatility: EWMA (RiskMetrics, lambda 0.94) of daily log returns -> sigma per day;
* expected move: ridge regression of the next 5-day return, in units of 5-day sigma, on four
  standardized factors -- 20-day and 60-day momentum, 5-day reversal and the distance from the
  50-day average -- fitted on that metal's own history, heavily shrunk and capped at +/-0.5 sigma;
* spread: empirical quantiles of the fitted residuals (fat tails included), scaled by sigma*sqrt(k)
  for day k, around a drift that grows linearly to the 5-day expectation.

A walk-forward backtest over the last year (refit every day, only past data) reports the hit rate of
the predicted direction, the median-forecast error against a no-change forecast (random walk) and how
often the 80% range held the actual price, so the page can say plainly how much the model is worth.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

HORIZON = 5
LAMBDA = 0.94
RIDGE = 8.0          # penalty relative to the sample size; large = predictions close to zero
CAP = 0.5            # |expected 5-day move| <= CAP * 5-day sigma
QUANTILES = (0.10, 0.25, 0.50, 0.75, 0.90)
MIN_TRAIN = 250
BACKTEST_DAYS = 250
FACTORS = (
    ("mom20", "20 日動能"),
    ("mom60", "60 日動能"),
    ("rev5", "5 日短線(反轉)"),
    ("ma50", "與 50 日均線距離"),
)
NY = ZoneInfo("America/New_York")


def ewma_sigma(r: np.ndarray, lam: float = LAMBDA) -> np.ndarray:
    """sigma[t] uses returns up to and including t."""
    out = np.empty(len(r))
    var = float(np.var(r[:20])) if len(r) >= 20 else float(np.var(r) or 1e-6)
    for t, x in enumerate(r):
        var = lam * var + (1 - lam) * x * x
        out[t] = math.sqrt(var)
    return out


def features(closes: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(X [n, 4] with NaN where undefined, sigma per day [n], target y [n] = next-5-day z, NaN at the end)."""
    lc = np.log(closes)
    r = np.diff(lc, prepend=lc[0])
    sig = ewma_sigma(r)
    n = len(closes)
    X = np.full((n, len(FACTORS)), np.nan)
    y = np.full(n, np.nan)
    for t in range(60, n):
        s = sig[t] or 1e-9
        ma50 = lc[t - 49:t + 1].mean()
        X[t] = [
            (lc[t] - lc[t - 20]) / (s * math.sqrt(20)),
            (lc[t] - lc[t - 60]) / (s * math.sqrt(60)),
            (lc[t] - lc[t - 5]) / (s * math.sqrt(5)),
            (lc[t] - ma50) / (s * math.sqrt(50)),
        ]
        if t + HORIZON < n:
            y[t] = (lc[t + HORIZON] - lc[t]) / (s * math.sqrt(HORIZON))
    return X, sig, y


def fit(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Ridge coefficients (no intercept: a week's drift is not estimable) and the residuals."""
    lam = RIDGE * len(y)
    beta = np.linalg.solve(X.T @ X + lam * np.eye(X.shape[1]), X.T @ y)
    return beta, y - X @ beta


def _rows(X: np.ndarray, y: np.ndarray, upto: int) -> tuple[np.ndarray, np.ndarray]:
    """Training rows whose 5-day outcome is known by day `upto` (no look-ahead)."""
    idx = np.arange(upto - HORIZON + 1)
    ok = idx[~np.isnan(X[idx]).any(axis=1) & ~np.isnan(y[idx])]
    return X[ok], y[ok]


def predict_z(X: np.ndarray, y: np.ndarray, t: int) -> tuple[float, np.ndarray, np.ndarray] | None:
    Xt, yt = _rows(X, y, t)
    if len(yt) < MIN_TRAIN or np.isnan(X[t]).any():
        return None
    beta, resid = fit(Xt, yt)
    z = float(np.clip(X[t] @ beta, -CAP, CAP))
    return z, beta, resid


def backtest(closes: np.ndarray, X: np.ndarray, sig: np.ndarray, y: np.ndarray, days: int = BACKTEST_DAYS) -> dict | None:
    n = len(closes)
    hits = total = covered = 0
    err_model, err_rw = [], []
    for t in range(max(MIN_TRAIN + HORIZON + 60, n - HORIZON - days), n - HORIZON):
        p = predict_z(X, y, t)
        if p is None:
            continue
        z, _, resid = p
        s5 = sig[t] * math.sqrt(HORIZON)
        lo, med, hi = (z + np.quantile(resid, q) for q in (0.10, 0.50, 0.90))
        actual = math.log(closes[t + HORIZON] / closes[t])
        if z != 0 and actual != 0:
            total += 1
            hits += int((z > 0) == (actual > 0))
        covered += int(lo * s5 <= actual <= hi * s5)
        err_model.append(abs(math.exp(med * s5) - math.exp(actual)))
        err_rw.append(abs(1 - math.exp(actual)))
    if not err_model:
        return None
    mae_m, mae_rw = float(np.mean(err_model)) * 100, float(np.mean(err_rw)) * 100
    return {
        "n": len(err_model),
        "hit_rate": round(hits / total * 100, 1) if total else None,
        "mae_model_pct": round(mae_m, 2),
        "mae_rw_pct": round(mae_rw, 2),
        "skill_pct": round((1 - mae_m / mae_rw) * 100, 1) if mae_rw else None,
        "coverage80": round(float(covered) / len(err_model) * 100, 1),
    }


def next_weekdays(last_ts: int, k: int = HORIZON) -> list[str]:
    d = datetime.fromtimestamp(last_ts, timezone.utc).astimezone(NY).date()
    out = []
    while len(out) < k:
        d += timedelta(days=1)
        if d.weekday() < 5:
            out.append(d.isoformat())
    return out


def forecast(daily, run_backtest: bool = True, center: dict[str, float] | None = None) -> dict | None:
    """Forecast from daily bars (oldest first); None when the history is too short.

    ``center`` (date -> price) replaces the statistical centre line with an outside model's
    day-by-day prediction (the silver AI champion, see ``silver_ai.py``). The bands keep this
    model's volatility and residual quantiles around the new centre; days the outside model does
    not cover fall back to the statistical drift.
    """
    closes = np.array([b.close for b in daily], dtype=float)
    if len(closes) < MIN_TRAIN + 80:
        return None
    X, sig, y = features(closes)
    t = len(closes) - 1
    p = predict_z(X, y, t)
    if p is None:
        return None
    z, beta, resid = p
    s = float(sig[t])
    qs = {q: float(np.quantile(resid, q)) for q in QUANTILES}
    base = float(closes[t])
    days = next_weekdays(daily[-1].ts)
    stat_z = z
    if center:
        last_day = days[-1]
        if last_day in center and center[last_day] > 0:
            # the outside model's 5-day move, in the same sigma units the bands and p_up use
            z = math.log(center[last_day] / base) / (s * math.sqrt(HORIZON))
    path = []
    for k, day in enumerate(days, start=1):
        sk = s * math.sqrt(k)
        if center and center.get(day, 0) > 0:
            drift = math.log(center[day] / base)
        else:
            drift = stat_z * s * math.sqrt(HORIZON) * k / HORIZON
        row = {"date": day}
        for q, v in qs.items():
            row[f"p{int(q * 100)}"] = round(base * math.exp(drift + (v - qs[0.5]) * sk), 2)
        path.append(row)
    s5 = s * math.sqrt(HORIZON)
    # P(5-day return > 0) from the residual distribution shifted by the expectation
    p_up = float(np.mean(z + (resid - qs[0.5]) > 0)) * 100
    contrib = X[t] * beta
    drivers = [{"key": key, "name": name, "value": round(float(X[t][i]), 2), "effect": round(float(contrib[i]), 3)}
               for i, (key, name) in enumerate(FACTORS)]
    last = path[-1]
    stat = None
    if center:   # what the statistical model alone would have said, for comparison on the page
        stat = {"expected_pct": round((math.exp(stat_z * s5) - 1) * 100, 2),
                "p_up": round(float(np.mean(stat_z + (resid - qs[0.5]) > 0)) * 100, 1)}
    return {
        "horizon_days": HORIZON,
        "base_price": round(base, 2),
        "base_date": datetime.fromtimestamp(daily[-1].ts, timezone.utc).astimezone(NY).date().isoformat(),
        "path": path,
        "expected_pct": round((last["p50"] / base - 1) * 100, 2) if center else round((math.exp(z * s5) - 1) * 100, 2),
        "median": last["p50"],
        "range50": [last["p25"], last["p75"]],
        "range80": [last["p10"], last["p90"]],
        "p_up": round(p_up, 1),
        "sigma_week_pct": round(s5 * 100, 2),
        "z": round(z, 3),
        "drivers": drivers,
        "history": [{"date": datetime.fromtimestamp(b.ts, timezone.utc).astimezone(NY).date().isoformat(), "close": round(b.close, 2)}
                    for b in daily[-60:]],
        "backtest": backtest(closes, X, sig, y) if run_backtest else None,
        "stat": stat,
    }
