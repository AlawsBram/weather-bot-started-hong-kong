"""Where could an edge be? Looked at on TRAINING days only (2026-04-15 .. 2026-08-31).

Run:  python research/explore_train.py
The test period is sealed by v2/data.py, so this script physically cannot read it.
"""
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v2 import data  # noqa: E402
from hkbot.buckets import winning_bucket  # noqa: E402
from hkbot.model import combine  # noqa: E402
from hkbot.config import get_strategy  # noqa: E402


def bucket_mid(b):
    lo = b.lo if b.lo != -math.inf else b.hi - 1
    return lo + 0.5 if b.hi != math.inf else lo + 0.5


def ols(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx if sxx else 0.0
    res = [y - my - b * (x - mx) for x, y in zip(xs, ys)]
    se = math.sqrt(sum(r * r for r in res) / max(1, n - 2) / sxx) if sxx else math.inf
    return b, se


strat = get_strategy("v2_audited")
for decision in ("evening", "morning", "noon", "afternoon"):
    snaps, _ = data.load(decision, "train")
    print(f"\n===== {decision}: {len(snaps)} training days =====")
    cal = defaultdict(lambda: [0, 0, 0.0])           # price bin -> [n, wins, sum price]
    overround, gaps, mkt_err, mod_err, xs, ys = [], [], [], [], [], []
    for s in snaps:
        win = winning_bucket(s.buckets, s._actual, cutoff=True)
        raw = [p for p in s.market_raw if p is not None]
        overround.append(sum(raw))
        for b, p in zip(s.buckets, s.market_raw):
            if p is None:
                continue
            k = min(int(p * 20), 19)                  # 5-cent bins
            c = cal[k]
            c[0] += 1
            c[1] += b.label == win.label
            c[2] += p
        # market's implied expected "bucket temperature" (cut-off value) vs the actual cut-off value
        m_mean = sum(p * bucket_mid(b) for b, p in zip(s.buckets, s.market_p)) - 0.5
        fc = combine(s.forecasts, strat, s.priors)
        act = math.floor(s._actual)
        mkt_err.append(m_mean - act)
        if fc:
            model_floor = fc.mean - 0.45                  # expected floor of a reading around the mean
            mod_err.append(model_floor - act)
            xs.append(model_floor - m_mean)              # model disagrees with market by x
            ys.append(act - m_mean)                      # market turned out wrong by y
    print(f"  bucket prices add up to: avg {sum(overround)/len(overround):.3f}")
    print(f"  market's expected bucket minus actual: avg {sum(mkt_err)/len(mkt_err):+.2f}  "
          f"(abs {sum(map(abs, mkt_err))/len(mkt_err):.2f})")
    if mod_err:
        print(f"  model's expected bucket minus actual:  avg {sum(mod_err)/len(mod_err):+.2f}  "
              f"(abs {sum(map(abs, mod_err))/len(mod_err):.2f})")
        b, se = ols(xs, ys)
        print(f"  when model and market disagree by 1 degree, the market is wrong by {b:+.2f} degrees "
              f"(+-{se:.2f}; 0 = model adds nothing, 1 = model is right)")
    print("  calibration (price bin -> how often that bucket actually won):")
    for k in sorted(cal):
        n, w, sp = cal[k]
        if n >= 15:
            print(f"    {k*5:2d}-{k*5+5:3d}c  n={n:4d}  avg price {sp/n:.3f}  won {w/n:.3f}  "
                  f"(+-{math.sqrt(max(w,1)*(n-w)/n)/n:.3f})")
