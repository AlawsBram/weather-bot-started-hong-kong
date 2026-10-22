"""CLI:  python -m v2 discover | run [--mode dry|probe|live] | settle | inspect [--slug S | --today]"""
import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_env():
    f = ROOT / "deploy" / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def main():
    load_env()
    from . import config, rules
    ap = argparse.ArgumentParser(prog="v2")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("discover", help="list every active daily-temperature market and its parsed rules")
    r = sub.add_parser("run", help="the loop")
    r.add_argument("--mode", choices=["dry", "probe", "live"], default=config.MODE)
    st = sub.add_parser("settle")
    st.add_argument("--quiet", action="store_true")
    ins = sub.add_parser("inspect", help="read the Rules panel on the web page and compare with the API")
    ins.add_argument("--slug")
    ins.add_argument("--today", action="store_true", help="every supported event whose window is open now")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ")
    logging.Formatter.converter = __import__("time").gmtime

    if a.cmd == "discover":
        evs = rules.load_events()
        changes = rules.update_station_table(evs, config.STATIONS_FILE)
        print(f"{'event':62s} {'style':6s} {'icao':5s} unit window(UTC)                 tradeable")
        for e in sorted(evs, key=lambda x: (x.day, x.city, x.kind)):
            w = f"{e.window_start:%m-%d %H:%M}..{e.window_end:%m-%d %H:%M}" if e.window_start else "-"
            print(f"{e.slug:62s} {e.style:6s} {e.icao or '-':5s} {e.unit or '-':4s} {w:27s} {'yes' if e.supported else 'no ' + ';'.join(e.problems)}")
        print(f"\n{len(evs)} events, {sum(e.supported for e in evs)} tradeable; station table -> {config.STATIONS_FILE}")
        if changes:
            print("CHANGES since last time:\n  " + "\n  ".join(changes))
    elif a.cmd == "run":
        from .bot import Bot
        Bot(a.mode).run()
    elif a.cmd == "settle":
        from . import settle
        settle.main(quiet=a.quiet)
    elif a.cmd == "inspect":
        evs = rules.load_events()
        now = datetime.now(timezone.utc)
        pick = [e for e in evs if e.slug == a.slug] if a.slug else \
            [e for e in evs if e.supported and e.window_start <= now < e.window_end] if a.today else []
        if not pick:
            sys.exit("nothing to inspect (use --slug or --today)")
        bad = 0
        for e in pick:
            try:
                res = rules.inspect_page(e)
            except Exception as ex:      # noqa: BLE001
                res = {"slug": e.slug, "error": f"{type(ex).__name__}: {ex}"[:200]}
            print(res)
            bad += not res.get("match")
        print(f"{len(pick)} pages, {bad} mismatches")


if __name__ == "__main__":
    main()
