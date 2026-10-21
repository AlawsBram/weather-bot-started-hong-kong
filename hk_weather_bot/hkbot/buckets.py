"""Bucket labels and the resolution rule.

Confirmed from past markets (Oct 2, 4, 9, 15 2026): Polymarket's Hong Kong
markets CUT OFF the decimal of the HKO reading. A reading of 31.9 lands in the
"31°C" bucket; only 32.0+ lands in "32°C".
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

INF = math.inf
_NUM = re.compile(r"(-?\d+(?:\.\d+)?)")


@dataclass(frozen=True)
class Bucket:
    label: str
    lo: float          # inclusive lower edge, in whole degrees (or -inf)
    hi: float          # exclusive upper edge, in whole degrees (or +inf)

    def contains(self, reading: float, cutoff: bool = True) -> bool:
        value = cutoff_value(reading) if cutoff else round_half_up(reading)
        return self.lo <= value < self.hi


def parse_label(label: str) -> Bucket:
    """'31°C' -> [31, 32);  '26°C or below' -> (-inf, 27);  '36°C or higher' -> [36, inf)."""
    text = label.lower()
    m = _NUM.search(text)
    if not m:
        raise ValueError(f"Cannot parse bucket label: {label!r}")
    k = int(float(m.group(1)))
    if "below" in text or "lower" in text or "less" in text:
        return Bucket(label, -INF, k + 1)
    if "higher" in text or "above" in text or "more" in text:
        return Bucket(label, k, INF)
    return Bucket(label, k, k + 1)


def cutoff_value(reading: float) -> int:
    """31.9 -> 31.  Uses one-decimal precision first so 31.999999 float noise can't leak."""
    return math.floor(round(reading, 1) + 1e-9)


def round_half_up(reading: float) -> int:
    return math.floor(round(reading, 1) + 0.5)


def winning_bucket(buckets: list[Bucket], reading: float, cutoff: bool = True) -> Bucket | None:
    for b in buckets:
        if b.contains(reading, cutoff):
            return b
    return None


def continuous_edges(b: Bucket, cutoff: bool = True) -> tuple[float, float]:
    """Edges on the real-number temperature line used by the probability model.

    HKO publishes one decimal. With the cut-off rule, bucket k holds readings
    k.0 .. k.9, i.e. true temperatures in [k - 0.05, k + 0.95).
    With rounding (the v1 mistake) bucket k holds [k - 0.5, k + 0.5).
    """
    shift = 0.05 if cutoff else 0.5
    lo = b.lo - shift if b.lo != -INF else -INF
    hi = b.hi - shift if b.hi != INF else INF
    return lo, hi
