"""The decisive measurement: the SECOND the text page shows a new whole degree, what does the dead
bucket's order book look like? (best NO ask, depth at or under 97c, best YES bid).

Called by recorder.py for every new text-page reading. One CLOB request per kill - a few a day.
Output: data/kills-YYYY-MM-DD.csv (HKT date).
"""
import csv
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from hkbot import config as hkc
from hkbot import sources
from hkbot.buckets import cutoff_value, parse_label

HKT = timezone(timedelta(hours=8))
CLOB_BOOK = "https://clob.polymarket.com/book"
FIELDS = ["seen_hkt", "reading_hkt", "max_c", "new_whole_degree", "dead_bucket", "no_best_ask",
          "no_depth_shares_le_97c", "no_depth_usd_le_97c", "yes_best_bid", "yes_best_ask", "book_ms", "error"]


class KillWatcher:
    def __init__(self, data_dir: Path):
        self.data = data_dir
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "hk-live-recorder/1.1 (read-only research)"
        self.day = None
        self.markets = []
        self.last_k = None

    def _load(self, day):
        ev = self.s.get(hkc.GAMMA_EVENTS_URL, params={"slug": sources.event_slug(day)}, timeout=20).json()
        ev = ev[0] if isinstance(ev, list) and ev else (ev if isinstance(ev, dict) else None)
        mk = []
        for m in (ev or {}).get("markets", []):
            try:
                b = parse_label(m.get("groupItemTitle") or "")
            except ValueError:
                continue
            toks = m.get("clobTokenIds")
            toks = json.loads(toks) if isinstance(toks, str) else (toks or [])
            if len(toks) == 2:
                mk.append((b, m["groupItemTitle"], str(toks[0]), str(toks[1])))
        self.markets, self.day, self.last_k = mk, day, None

    def on_reading(self, reading_dt: datetime, max_c: float, seen: float) -> None:
        day = reading_dt.date()
        if day != self.day:
            self._load(day)
        k = cutoff_value(max_c)
        if self.last_k is None:           # first reading after a (re)start: baseline only
            self.last_k = k
            return
        if k <= self.last_k:
            return
        for deg in range(self.last_k + 1, k + 1):
            for b, label, yes_tok, no_tok in self.markets:
                if b.hi == deg:            # bucket [deg-1, deg) just became impossible
                    self._log(reading_dt, max_c, seen, deg, label, yes_tok, no_tok)
        self.last_k = k

    def _book(self, token):
        r = self.s.get(CLOB_BOOK, params={"token_id": token}, timeout=10)
        r.raise_for_status()
        d = r.json()
        asks = sorted((float(a["price"]), float(a["size"])) for a in d.get("asks", []))
        bids = sorted(((float(a["price"]), float(a["size"])) for a in d.get("bids", [])), reverse=True)
        return asks, bids

    def _log(self, reading_dt, max_c, seen, deg, label, yes_tok, no_tok):
        t0 = time.time()
        row = dict(seen_hkt=datetime.fromtimestamp(seen, HKT).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                   reading_hkt=reading_dt.strftime("%H:%M"), max_c=max_c, new_whole_degree=deg, dead_bucket=label,
                   no_best_ask="", no_depth_shares_le_97c="", no_depth_usd_le_97c="", yes_best_bid="", yes_best_ask="",
                   book_ms="", error="")
        try:
            no_asks, _ = self._book(no_tok)
            yes_asks, yes_bids = self._book(yes_tok)
            row.update(no_best_ask=no_asks[0][0] if no_asks else "",
                       no_depth_shares_le_97c=round(sum(s for p, s in no_asks if p <= 0.97), 1),
                       no_depth_usd_le_97c=round(sum(p * s for p, s in no_asks if p <= 0.97), 2),
                       yes_best_bid=yes_bids[0][0] if yes_bids else "", yes_best_ask=yes_asks[0][0] if yes_asks else "")
        except (requests.RequestException, ValueError, KeyError) as e:
            row["error"] = type(e).__name__
        row["book_ms"] = round(1000 * (time.time() - t0))
        f = self.data / f"kills-{datetime.fromtimestamp(seen, HKT):%Y-%m-%d}.csv"
        new = not f.exists()
        with f.open("a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=FIELDS)
            if new:
                w.writeheader()
            w.writerow(row)
        print(f"[{datetime.now(timezone.utc):%FT%TZ}] KILL {label} (max {max_c} -> {deg}): NO ask {row['no_best_ask']} "
              f"depth<=97c {row['no_depth_shares_le_97c']} sh, book {row['book_ms']} ms {row['error']}", flush=True)
