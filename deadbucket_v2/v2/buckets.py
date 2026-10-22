"""Bucket labels and the EXACT 'dead bucket' test from the METAR rules.

Rules (quoted from the markets): the qualifying value is in tenths of a degree Celsius; for Celsius
markets it is rounded to the nearest whole degree, half rounded UP (13.9 -> 14, 16.5 -> 17, -1.7 -> -2,
-8.5 -> -8); for Fahrenheit markets it is converted with F = C*9/5 + 32 first, then rounded the same way
(18.3C = 64.94F -> 65; -22.5C = -8.5F -> -8). Exact rational arithmetic, no floating point.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from fractions import Fraction

LABEL_RE = re.compile(r"^\s*(-?\d+)\s*(?:-\s*(-?\d+))?\s*°?\s*([CF])\s*(or below|or higher)?\s*$", re.I)


@dataclass(frozen=True)
class Bucket:
    label: str
    lo: int | None      # inclusive whole degrees; None = open-ended below
    hi: int | None      # inclusive whole degrees; None = open-ended above
    unit: str           # C | F


def parse_label(label: str) -> Bucket:
    m = LABEL_RE.match(label.replace("°", "°").replace("�", "°"))
    if not m:
        raise ValueError(f"unrecognised bucket label {label!r}")
    a, b, unit, tail = int(m.group(1)), m.group(2), m.group(3).upper(), (m.group(4) or "").lower()
    if tail == "or below":
        return Bucket(label, None, a, unit)
    if tail == "or higher":
        return Bucket(label, a, None, unit)
    return Bucket(label, a, int(b) if b is not None else a, unit)


def whole_degrees(tenths_c: int, unit: str) -> int:
    """The whole-degree value the rules assign to a reading of `tenths_c` tenths of a degree C."""
    v = Fraction(tenths_c, 10)
    if unit == "F":
        v = v * 9 / 5 + 32
    return math.floor(v + Fraction(1, 2))          # round half up, also for negatives (-8.5 -> -8)


def contains(b: Bucket, whole: int) -> bool:
    return (b.lo is None or whole >= b.lo) and (b.hi is None or whole <= b.hi)


def is_dead(b: Bucket, kind: str, extreme_tenths_c: int) -> bool:
    """Can bucket `b` still win, given the day's extreme so far?
    highest market: the max can only rise, so every bucket BELOW the current max's whole value is dead.
    lowest market : the min can only fall, so every bucket ABOVE the current min's whole value is dead."""
    w = whole_degrees(extreme_tenths_c, b.unit)
    if kind == "highest":
        return b.hi is not None and w > b.hi
    if kind == "lowest":
        return b.lo is not None and w < b.lo
    raise ValueError(kind)


def margin_tenths(b: Bucket, kind: str, extreme_tenths_c: int) -> int | None:
    """How many tenths of a degree C the extreme is PAST the kill line (safety margin against a
    corrected report). None if not dead."""
    if not is_dead(b, kind, extreme_tenths_c):
        return None
    step = 1 if kind == "highest" else -1
    t = extreme_tenths_c
    while is_dead(b, kind, t - step):
        t -= step
    return abs(extreme_tenths_c - t)
