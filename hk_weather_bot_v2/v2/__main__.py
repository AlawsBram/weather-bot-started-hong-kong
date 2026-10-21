"""python -m v2 tune     -> training days only: walk-forward check + final settings, then FREEZE
   python -m v2 test     -> opens the sealed test period ONCE with the frozen settings
"""
from __future__ import annotations

import csv
import sys
from dataclasses import asdict, replace
from datetime import date

from . import backtest as B
from . import data
from .model import V2, History

DECISIONS = ("evening", "morning", "noon", "afternoon")
TODAY = date.today().isoformat()


def fmt(sm):
    return (f"days {sm['days']:3d} | Brier bot {sm['brier_bot']:.3f} vs mkt {sm['brier_mkt']:.3f} | "
            f"log-loss bot {sm['ll_bot']:.3f} vs mkt {sm['ll_mkt']:.3f} | "
            f"trades {sm['trades']:3d} | P&L ${sm['pnl']:+.2f}")


def write_rows(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def cmd_tune():
    if data.FROZEN.exists():
        sys.exit("Already frozen. Settings cannot be re-tuned after the freeze (that would be peeking).")
    cfgs, notes, lines = {}, {}, []
    for dec in DECISIONS:
        train, _ = data.load(dec, "train")
        hist = History(train)
        lines.append(f"\n===== {dec}: {len(train)} training days =====")
        lines.append("Walk-forward inside training (settings picked on earlier months only):")
        wf_tot = []
        for m, cfg, sm, rows in B.walk_forward(train, hist):
            lines.append(f"  month {m}: {fmt(sm)}   <- {cfg.key()}")
            wf_tot += rows
        lines.append(f"  ALL 3 months: {fmt(B.summary(wf_tot))}")
        final = B.tune(train, hist)
        sm = B.summary(B.score(train, final, hist))
        mkt = B.summary(B.score(train, V2(), hist))
        lines.append(f"Final settings (all training days): {final.key()}")
        lines.append(f"  on training days:  {fmt(sm)}")
        lines.append(f"  market alone:      {fmt(mkt)}")
        cfgs[dec] = asdict(final)
        notes[dec] = dict(walk_forward=B.summary(wf_tot), train=sm)
    blob = B.freeze(cfgs, notes)
    lines.append(f"\nFROZEN at {blob['frozen_at_utc']} sha {blob['sha']} -> {data.FROZEN}")
    text = "\n".join(lines)
    print(text)
    (data.RESULTS / f"v2 tuning on training days only - {TODAY}.txt").write_text(text, encoding="utf-8")


def cmd_test():
    cfgs, blob = B.load_frozen()
    lines = [f"Frozen settings sha {blob['sha']} (frozen {blob['frozen_at_utc']}). Test period opened now."]
    for dec in DECISIONS:
        train, _ = data.load(dec, "train")
        test, _ = data.load(dec, "test")
        hist = History(train + test)        # day D still only learns from days <= D-2
        cfg = cfgs[dec]
        rows = B.score(test, cfg, hist)
        sm = B.summary(rows)
        mkt = B.summary(B.score(test, V2(), hist))
        lines.append(f"\n===== {dec}: TEST {date(2026, 9, 1)} .. {data.END} =====")
        lines.append(f"  settings: {cfg.key()}")
        lines.append(f"  v2:           {fmt(sm)}")
        lines.append(f"  market alone: {fmt(mkt)}")
        lines.append(f"  beat the market on {sm.get('beat_days', 0)}/{sm['days']} days")
        if rows:
            write_rows(data.RESULTS / f"backtest from {TODAY} to {data.START} - v2 {dec} (test days).csv", rows)
    text = "\n".join(lines)
    print(text)
    (data.RESULTS / f"backtest from {TODAY} to {data.START} - v2 TEST RESULT.txt").write_text(text, encoding="utf-8")


if __name__ == "__main__":
    {"tune": cmd_tune, "test": cmd_test}[sys.argv[1]]()
