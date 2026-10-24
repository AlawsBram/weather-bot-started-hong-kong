"""Daily (10:00 HKT) check of yesterday: (1) SPEED - when each source first showed each new whole degree,
(2) MATCH - does each source's highest reading, cut-off rule applied, equal HKO's official max (RYES)?
(3) KILLS - for each dead bucket, what the book offered the second it died. One Telegram summary."""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from . import alerts, config
from .sources import HKT, SOURCES, cutoff_value, reading_day

HKBOT = Path(os.environ.get("HKBOT_DIR", Path.home() / "hkbot"))
if str(HKBOT) not in sys.path:
    sys.path.insert(0, str(HKBOT))


def official_max(day: date):
    from hkbot import config as hkc
    from hkbot.hkarchive import parse_ryes
    try:
        r = requests.get(hkc.HKO_OPENDATA_CLIMATE, params={"dataType": "RYES", "date": day.strftime("%Y%m%d"), "lang": "en", "station": "HKO"}, timeout=30)
        return parse_ryes(r.json()) if r.ok else None
    except (requests.RequestException, ValueError):
        return None


def load(day: date) -> list[dict]:
    rows = []
    for d in (day, day + timedelta(days=1)):        # the day's 00:00 stamp is first seen (and filed) on the next day
        f = config.DATA_DIR / f"versions-{d}.csv"
        if f.exists():
            rows += list(csv.DictReader(f.open(encoding="utf-8")))
    for r in rows:
        r["seen"] = datetime.fromisoformat(r["first_seen_utc"])
        r["reading"] = datetime.strptime(r["reading_hkt"], "%Y-%m-%d %H:%M").replace(tzinfo=HKT)
        r["hi"] = float(r["max"]) if r["max"] else float(r["temp"])
    return [r for r in rows if reading_day(r["reading"]) == day]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    day = date.fromisoformat(a.date) if a.date else (datetime.now(HKT) - timedelta(days=1)).date()
    rows = load(day)
    off = official_max(day)
    lines = [f"reconcile {day}"]
    if not rows:
        lines.append("no recordings for this day")
    else:
        lines.append(f"official max (RYES): {off if off is not None else 'not out yet'}" + (f" -> settles as {cutoff_value(off)}" if off is not None else ""))
        for s in SOURCES:
            sr = [r for r in rows if r["source"] == s]
            if not sr:
                continue
            mx = max(r["hi"] for r in sr)
            match = "?" if off is None else ("yes" if cutoff_value(mx) == cutoff_value(off) else ("BELOW" if cutoff_value(mx) < cutoff_value(off) else "ABOVE"))
            lags = sorted(float(r["lag_s"]) for r in sr)
            lines.append(f"  {s:10s} highest {mx:.1f} -> {cutoff_value(mx)}  match {match}; {len(sr)} readings, median delay {lags[len(lags) // 2] / 60:.1f} min")
        top = max(cutoff_value(r["hi"]) for r in rows)
        early = [cutoff_value(r["hi"]) for r in rows if r["reading"].hour < 9]
        start = min(early) if early else top
        for k in range(start + 1, top + 1):
            first = {}
            for s in SOURCES:
                hit = next((r for r in sorted((r for r in rows if r["source"] == s), key=lambda r: r["seen"]) if cutoff_value(r["hi"]) >= k), None)
                if hit:
                    first[s] = hit
            if first:
                best = min(first.values(), key=lambda r: r["seen"])
                ref = first.get("csv_maxmin")
                lines.append(f"  {k}C first via {best['source']} at {best['first_seen_hkt'][11:19]} (stamp {best['reading_hkt'][11:]}, "
                             f"+{float(best['lag_s']) / 60:.1f} min)" + (f", {(ref['seen'] - best['seen']).total_seconds():.0f}s before the CSV" if ref else ""))
    kf = config.DATA_DIR / f"kills-{day}.csv"
    if kf.exists():
        for k in csv.DictReader(kf.open(encoding="utf-8")):
            lines.append(f"  KILL {k['dead_bucket']} at {k['seen_hkt'][11:19]}: NO ask {k['no_best_ask']}, depth<=97c {k['no_depth_shares_le_97c']} sh (${k['no_depth_usd_le_97c']}), paper {k['paper_shares']} sh")
    text = "\n".join(lines)
    print(text)
    if not a.quiet:
        alerts.alert(f"reconcile_{day}", text, dedup_hours=20)


if __name__ == "__main__":
    main()
