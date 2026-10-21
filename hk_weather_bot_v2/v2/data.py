"""Data for v2, with a seal on the test period.

v2 reuses v1's downloaders and disk cache (read-only) from the sibling folder `hk_weather_bot/`,
so every number is the same real data v1 was tested on: Polymarket CLOB price history,
Open-Meteo as-issued forecasts, HKO's archived bulletins and max-since-midnight readings,
and HKO's official daily max.

THE SEAL: test days (after TRAIN_END) can only be loaded once `results/FROZEN_v2.json` exists.
That file is written by `freeze` after all tuning, with a hash of the chosen settings. Anything
designed or tuned before it exists can only have seen training days.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT.parent / "hk_weather_bot"
sys.path.insert(0, str(V1))

from hkbot import config as v1config  # noqa: E402
from hkbot import hkarchive  # noqa: E402
from hkbot import history as H  # noqa: E402
from hkbot.sources import HKT  # noqa: E402

START = date(2026, 4, 15)
TRAIN_END = date(2026, 8, 31)
END = date(2026, 10, 19)
RESULTS = ROOT / "results"
FROZEN = RESULTS / "FROZEN_v2.json"
PRICE_LAG_MIN = 60     # the CLOB hourly price point the backtest sees is 60 min before the decision


class SealError(RuntimeError):
    pass


def load(decision: str, split: str, progress=lambda *_: None):
    """-> list of v1 DaySnapshot for 'train' or 'test'.

    Live readings are taken at the SAME moment as the price (decision - 60 min), the fairness fix
    found on 2026-10-21: otherwise the bot sees an hour of heat the price never saw."""
    if split == "test" and not FROZEN.exists():
        raise SealError("Test period is sealed until `python -m v2 freeze` writes FROZEN_v2.json")
    lag = timedelta(minutes=PRICE_LAG_MIN)
    orig_obs, orig_hko = hkarchive.max_so_far_at, hkarchive.hko_forecast_at
    # both HKO inputs are cut off at the price moment, so nothing is newer than the price
    hkarchive.max_so_far_at = lambda c, d, dt: orig_obs(c, d, dt - lag)
    hkarchive.hko_forecast_at = lambda c, d, dt: current_hko_forecast(c, d, dt - lag)
    old_peak = H.PEAK_HOUR
    H.PEAK_HOUR = old_peak + PRICE_LAG_MIN / 60          # hours_left counted from the price moment
    try:
        end = TRAIN_END if split == "train" else END
        markets, fcsts, actuals, obs = H.load_range(START, end, decision, progress=progress)
        snaps, skipped = H.build_snapshots(markets, fcsts, actuals, obs, decision)
    finally:
        hkarchive.max_so_far_at, hkarchive.hko_forecast_at = orig_obs, orig_hko
        H.PEAK_HOUR = old_peak
    if split == "train":
        snaps = [s for s in snaps if s.day <= TRAIN_END]
    else:
        snaps = [s for s in snaps if s.day > TRAIN_END]
    return snaps, skipped


def current_hko_forecast(cache, target: date, when: datetime):
    """HKO's forecast max for `target` from the ONE bulletin current at `when` (what a live bot
    would see). If that bulletin is for a different day or gives no number -> None."""
    want = "tomorrow" if when.date() < target else "today"
    ts = hkarchive.latest_at_or_before(hkarchive._versions(cache, hkarchive.LOCAL_FORECAST_URL, when.date()), when)
    if not ts:
        return None, None
    period, v = hkarchive.parse_local_forecast(hkarchive._file_text(cache, hkarchive.LOCAL_FORECAST_URL, ts) or "")
    return (v, ts) if period == want else (None, ts)


def price_series(day: date, decision: str) -> dict:
    """{bucket label: [(unix ts, price)]} for the 3 days up to the decision (cache only)."""
    from hkbot import sources
    from hkbot.buckets import parse_label
    cache = H.Cache(v1config.HISTORY_DIR)
    ev = H._data(cache.get(v1config.GAMMA_EVENTS_URL, {"slug": sources.event_slug(day)}))
    ev = ev[0] if isinstance(ev, list) and ev else ev
    ts = int(H.decision_dt(day, decision).timestamp())
    out = {}
    for m in (ev or {}).get("markets", []):
        label = m.get("groupItemTitle") or ""
        try:
            parse_label(label)
        except ValueError:
            continue
        tok = H._json_list(m.get("clobTokenIds"))
        if not tok:
            continue
        hist = H._data(cache.get(v1config.CLOB_PRICES_HISTORY, {
            "market": tok[0], "startTs": ts - 3 * 86400, "endTs": ts, "fidelity": 60}))
        out[label] = [(p["t"], float(p["p"])) for p in (hist or {}).get("history", []) if p["t"] <= ts]
    return out
