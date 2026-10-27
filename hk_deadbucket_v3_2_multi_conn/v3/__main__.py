"""CLI: python -m v3 run [--mode dry|probe|live] | settle | reconcile [--date D] | status"""
import argparse
import logging
import os
import sys
import time
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
    from . import config
    ap = argparse.ArgumentParser(prog="v3")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--mode", choices=["dry", "probe", "live"], default=config.MODE)
    st = sub.add_parser("settle")
    st.add_argument("--quiet", action="store_true")
    rc = sub.add_parser("reconcile")
    rc.add_argument("--date")
    rc.add_argument("--quiet", action="store_true")
    sub.add_parser("status")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%SZ")
    logging.Formatter.converter = time.gmtime
    if a.cmd == "run":
        from .bot import Bot
        Bot(a.mode).run()
    elif a.cmd == "settle":
        from . import settle
        settle.main(quiet=a.quiet)
    elif a.cmd == "reconcile":
        sys.argv = [sys.argv[0]] + (["--date", a.date] if a.date else []) + (["--quiet"] if a.quiet else [])
        from . import reconcile
        reconcile.main()
    elif a.cmd == "status":
        hb = config.STATE_DIR / "heartbeat"
        print(config.NAME, "-", config.DESCRIPTION)
        print("heartbeat:", hb.read_text() if hb.exists() else "none")
        for f in sorted(config.DATA_DIR.glob("*.csv"))[-6:]:
            print(f.name, sum(1 for _ in f.open(encoding="utf-8")) - 1, "rows")


if __name__ == "__main__":
    main()
