import math
import random

from metal_alert import forecast as F
from metal_alert.yahoo import Bar


def _bars(n=1300, seed=1, drift=0.0):
    rnd = random.Random(seed)
    p, out = 1000.0, []
    for i in range(n):
        p *= math.exp(drift + rnd.gauss(0, 0.012))
        out.append(Bar(1_600_000_000 + i * 86400, p, p, p, p))
    return out


def test_forecast_shape_and_ordering():
    f = F.forecast(_bars())
    assert f["horizon_days"] == 5 and len(f["path"]) == 5 and len(f["history"]) == 60
    for r in f["path"]:
        assert r["p10"] < r["p25"] < r["p50"] < r["p75"] < r["p90"]
    # the spread widens with the horizon
    w = [r["p90"] - r["p10"] for r in f["path"]]
    assert w == sorted(w)
    assert abs(f["z"]) <= F.CAP
    assert 0 <= f["p_up"] <= 100
    assert f["range80"] == [f["path"][-1]["p10"], f["path"][-1]["p90"]]


def test_random_walk_backtest_is_calibrated_and_skill_free():
    bt = F.forecast(_bars(seed=7))["backtest"]
    assert bt["n"] >= 200
    assert 65 <= bt["coverage80"] <= 92          # the 80% band holds about 80% of outcomes
    assert abs(bt["skill_pct"]) < 5               # no edge on pure noise, and no harm either


def test_short_history_gives_no_forecast():
    assert F.forecast(_bars(n=200)) is None


def test_next_weekdays_skip_weekends():
    friday = 1_764_342_000  # 2025-11-28 (Fri) 15:00 UTC
    assert F.next_weekdays(friday, 3) == ["2025-12-01", "2025-12-02", "2025-12-03"]
