"""Turn forecasts into bucket probabilities — the manual method, automated.

Steps (same as the worksheet we used by hand):
  1. Bias-correct each source (subtract its average warm/cool error).
  2. Weighted average -> best guess (trust HKO most).
  3. Uncertainty = typical source error + how much sources disagree.
  4. Spread probability over buckets using the CUT-OFF rule (k.0..k.9 -> k).
  5. Give every bucket a source points to at least a fair minimum (lesson: 30C at 2% was too low).
  6. If it's the day itself, nothing below the max-so-far can win.
  7. Blend with the market (lesson: when you disagree a lot, the market usually knows something).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from statistics import NormalDist

from . import config
from .buckets import Bucket, continuous_edges, cutoff_value, round_half_up
from .config import SourcePrior, Strategy

_N = NormalDist()


@dataclass
class Forecast:
    mean: float
    sigma: float
    corrected: dict[str, float]          # bias-corrected value per source
    weights: dict[str, float]
    notes: list[str] = field(default_factory=list)


def source_stats(errors: list[float]) -> dict:
    """errors = forecast - actual for past days."""
    n = len(errors)
    if n == 0:
        return {"n": 0}
    bias = sum(errors) / n
    rms = math.sqrt(sum((e - bias) ** 2 for e in errors) / max(1, n - 1)) if n > 1 else 1.0
    return {"n": n, "bias": round(bias, 3), "sigma": round(rms, 3),
            "mae": round(sum(abs(e) for e in errors) / n, 3)}


def priors_from_stats(stats: dict[str, dict]) -> dict[str, SourcePrior]:
    """Blend measured stats with the default priors; the more days, the more we trust data."""
    priors = dict(config.DEFAULT_PRIORS)
    for name, v in stats.items():
        n = v.get("n", 0)
        if n == 0:
            continue
        base = priors.get(name, SourcePrior())
        k = n / (n + 5)
        priors[name] = SourcePrior(
            bias=(1 - k) * base.bias + k * v["bias"],
            sigma=max(0.4, (1 - k) * base.sigma + k * v["sigma"]),
            weight=base.weight,
            n=n,
        )
    return priors


def load_calibration() -> dict:
    if config.CALIBRATION_PATH.exists():
        try:
            return json.loads(config.CALIBRATION_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def load_priors() -> dict[str, SourcePrior]:
    return priors_from_stats(load_calibration().get("sources", {}))


def trust(p: SourcePrior, strat: Strategy) -> float:
    """Extra trust multiplier on top of 1/sigma^2.

    Without auto_weights: the hand-written weight (HKO 1.5, GFS 0.3 ...), as before.
    With auto_weights: that hand-written number fades towards 1.0 as measured days come in
    (k = n / (n + 5), the same fade used for bias and sigma), so after a few weeks a source is
    trusted only according to its measured error size.
    """
    if not strat.auto_weights:
        return p.weight
    k = p.n / (p.n + 5) if p.n > 0 else 0.0
    return (1 - k) * p.weight + k * 1.0


def combine(raw: dict[str, float], strat: Strategy, priors: dict[str, SourcePrior] | None = None) -> Forecast | None:
    priors = priors or load_priors()
    usable = {s: v for s, v in raw.items() if s in strat.sources and v is not None}
    if not usable:
        return None
    corrected, weights = {}, {}
    for s, v in usable.items():
        p = priors.get(s, SourcePrior())
        corrected[s] = v - p.bias if strat.use_bias_correction else v
        weights[s] = trust(p, strat) / (p.sigma ** 2)
    wsum = sum(weights.values())
    mean = sum(corrected[s] * weights[s] for s in corrected) / wsum

    # Typical error of the blend (weighted average of source sigmas; errors are correlated,
    # so we do NOT shrink it as if sources were independent).
    base_sigma = sum(priors.get(s, SourcePrior()).sigma * weights[s] for s in corrected) / wsum
    disagreement = math.sqrt(sum(weights[s] * (corrected[s] - mean) ** 2 for s in corrected) / wsum)
    sigma = max(strat.sigma_floor, strat.sigma_scale * math.sqrt(base_sigma ** 2 + disagreement ** 2))
    notes = [f"{s}: raw {usable[s]:.1f} -> corrected {corrected[s]:.1f}" for s in usable]
    return Forecast(mean=mean, sigma=sigma, corrected=corrected, weights=weights, notes=notes)


def _normalise(p: list[float]) -> list[float]:
    t = sum(p)
    return [x / t for x in p] if t > 0 else p


def bucket_probs(
    buckets: list[Bucket],
    fc: Forecast,
    strat: Strategy,
    obs_max_so_far: float | None = None,
    hours_left: float | None = None,
) -> list[float]:
    """Model-only probability for each bucket (sums to 1)."""
    mean, sigma = fc.mean, fc.sigma
    lower_bound = -math.inf

    if strat.use_intraday_obs and obs_max_so_far is not None:
        # The day's max can't be lower than what's already been recorded.
        lower_bound = obs_max_so_far - 0.05
        if hours_left is not None and hours_left <= 2:
            # Peak has passed: the final max is very close to the max so far.
            mean, sigma = max(mean, obs_max_so_far), 0.25
        elif hours_left is not None and hours_left <= 5:
            mean, sigma = max(mean, obs_max_so_far + 0.3), max(0.4, sigma * 0.6)
        else:
            mean = max(mean, obs_max_so_far)

    dist = NormalDist(mean, sigma)
    lb_cdf = dist.cdf(lower_bound) if lower_bound != -math.inf else 0.0
    probs = []
    for b in buckets:
        lo, hi = continuous_edges(b, strat.use_cutoff_rule)
        lo = max(lo, lower_bound)
        if hi <= lo:
            probs.append(0.0)
            continue
        cdf_lo = dist.cdf(lo) if lo != -math.inf else 0.0
        cdf_hi = dist.cdf(hi) if hi != math.inf else 1.0
        probs.append(max(0.0, cdf_hi - cdf_lo))
    probs = [p / (1 - lb_cdf) for p in probs] if lb_cdf < 1 else probs
    probs = _normalise(probs)

    # Lesson 3: any bucket a credible source points at gets a fair minimum.
    if strat.plausible_floor > 0:
        to_bucket = cutoff_value if strat.use_cutoff_rule else round_half_up
        pointed = {to_bucket(v) for v in fc.corrected.values()}
        for i, b in enumerate(buckets):
            reachable = obs_max_so_far is None or b.hi > (obs_max_so_far or -99)
            if reachable and any(b.lo <= k < b.hi for k in pointed):
                probs[i] = max(probs[i], strat.plausible_floor)

    # Never say never — except for buckets the observations already ruled out.
    probs = [
        max(p, strat.tail_floor) if (obs_max_so_far is None or b.hi > obs_max_so_far) else 0.0
        for p, b in zip(probs, buckets)
    ]
    return _normalise(probs)


def blend_with_market(model_p: list[float], market_p: list[float | None], weight_model: float) -> list[float]:
    """Lesson 2: respect the market. weight_model=1 ignores it, 0 copies it."""
    if weight_model >= 1.0 or all(m is None for m in market_p):
        return model_p
    mk = [m if m is not None else 0.0 for m in market_p]
    mk = _normalise(mk) if sum(mk) > 0 else model_p
    return _normalise([weight_model * a + (1 - weight_model) * b for a, b in zip(model_p, mk)])
