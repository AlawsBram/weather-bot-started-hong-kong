"""v3 loop: one thread per HKO source (the text page is the trigger), kills -> book -> paper fill.

Records (all under data/, HKT dates):
  polls-<day>.csv      every single poll: source, HTTP status, stamp shown, fetch ms   (the report's "record every poll")
  versions-<day>.csv   first sight of every NEW reading per source + delay after its stamp
  readings-<day>.csv   at every new trigger reading: the favourite bucket's YES bid/ask and what is pre-armed
  kills-<day>.csv      the dead bucket's full book the second it dies
  decisions.jsonl      the paper (or probe/live) trade for every dead bucket
"""
from __future__ import annotations

import csv
import json
import logging
import threading
import time
from datetime import date, datetime, timezone

import requests

from . import alerts, config, market, sources
from .execution import Executor
from .sources import HKT

log = logging.getLogger("v3")
VERSION_FIELDS = ["first_seen_utc", "first_seen_hkt", "source", "reading_hkt", "temp", "max", "min", "lag_s", "http_last_modified", "poll_ms"]


def _append(path, fields, row):
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerow(row)


class Bot:
    def __init__(self, mode: str):
        self.mode = mode
        self.exe = Executor(mode)
        self.sess = {}
        for src in sources.SOURCES:
            s = requests.Session()
            s.headers["User-Agent"] = "hk-deadbucket-v3/0.1 (read-only research; low rate)"
            self.sess[src] = s
        self.clob = requests.Session()
        self.etag, self.lastmod, self.last = {}, {}, {}
        self.err_since = {}
        self.last_new_any = time.time()
        self.lock = threading.Lock()
        self.day: date | None = None
        self.markets: list[market.Market] = []
        self.last_whole: int | None = None
        self.done: set[tuple[str, str]] = set()
        self.stats = {"kills": 0, "polls": 0}
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        if config.DECISIONS_FILE.exists():
            for line in config.DECISIONS_FILE.read_text(encoding="utf-8").splitlines():
                try:
                    d = json.loads(line)
                    self.done.add((d["day"], d["label"]))
                except (ValueError, KeyError):
                    pass
        self._load_last_versions()

    def _load_last_versions(self):
        f = config.DATA_DIR / f"versions-{datetime.now(HKT):%Y-%m-%d}.csv"
        if f.exists():
            for row in csv.DictReader(f.open(encoding="utf-8")):
                self.last[row["source"]] = (row["reading_hkt"], row["temp"], row["max"], row["min"])

    # ---------------- polling ----------------
    def poll(self, src: str, url: str):
        h = {}
        params = None
        if src == "text_nocache":
            h = {"Cache-Control": "no-cache", "Pragma": "no-cache"}
            params = {"_": int(time.time() * 1000)}      # unique URL each poll -> the CDN cannot serve a cached copy
        else:
            if src in self.etag:
                h["If-None-Match"] = self.etag[src]
            if src in self.lastmod:
                h["If-Modified-Since"] = self.lastmod[src]
        t0 = time.time()
        try:
            r = self.sess[src].get(url, headers=h, params=params, timeout=10)
            status = r.status_code
        except requests.RequestException as e:
            self.err_since.setdefault(src, t0)
            self._log_poll(src, t0, type(e).__name__, "")
            return
        seen = time.time()
        got = None
        if status == 200:
            self.err_since.pop(src, None)
            if r.headers.get("ETag"):
                self.etag[src] = r.headers["ETag"]
            if r.headers.get("Last-Modified"):
                self.lastmod[src] = r.headers["Last-Modified"]
            try:
                got = sources.parse(src, r.content.decode("utf-8-sig", errors="replace"))
            except (ValueError, KeyError, TypeError):
                got = None
        elif status == 304:
            self.err_since.pop(src, None)
        else:
            self.err_since.setdefault(src, t0)
        self._log_poll(src, t0, status, got[0].strftime("%H:%M") if got else "")
        if not got:
            return
        dt, temp, mx, mn = got
        key = (dt.strftime("%Y-%m-%d %H:%M"), *("" if v is None else f"{v:.1f}" for v in (temp, mx, mn)))
        if self.last.get(src) == key:
            return
        self.last[src] = key
        self.last_new_any = seen
        hk = datetime.fromtimestamp(seen, HKT)
        with self.lock:
            _append(config.DATA_DIR / f"versions-{hk:%Y-%m-%d}.csv", VERSION_FIELDS, dict(
                first_seen_utc=datetime.fromtimestamp(seen, timezone.utc).isoformat(timespec="milliseconds"),
                first_seen_hkt=hk.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], source=src, reading_hkt=key[0],
                temp=key[1], max=key[2], min=key[3], lag_s=round(seen - dt.timestamp(), 1),
                http_last_modified=r.headers.get("Last-Modified", ""), poll_ms=round(1000 * (seen - t0))))
        if src == sources.TRIGGER and mx is not None:
            try:
                self.on_trigger(dt, mx, seen)
            except Exception as e:      # noqa: BLE001
                log.exception("trigger handling failed")
                alerts.alert("trigger_error", f"trigger handling failed: {type(e).__name__}: {e}"[:300])

    def _log_poll(self, src, t0, status, stamp):
        self.stats["polls"] += 1
        hk = datetime.fromtimestamp(t0, HKT)
        with self.lock:
            _append(config.DATA_DIR / f"polls-{hk:%Y-%m-%d}.csv", ["t_hkt", "source", "status", "stamp", "ms"],
                    dict(t_hkt=hk.strftime("%H:%M:%S.%f")[:-3], source=src, status=status, stamp=stamp, ms=round(1000 * (time.time() - t0))))

    # ---------------- trigger: a new text-page reading ----------------
    def on_trigger(self, reading_dt: datetime, max_c: float, seen: float):
        day = reading_dt.date()
        if day != self.day:
            self.markets = market.load_day(self.clob, day)
            self.day, self.last_whole = day, None
            log.info("markets for %s: %d buckets", day, len(self.markets))
        whole = sources.cutoff_value(max_c)
        hk = datetime.fromtimestamp(seen, HKT)
        fav = market.containing(self.markets, whole)
        row = dict(seen_hkt=hk.strftime("%H:%M:%S.%f")[:-3], reading_hkt=reading_dt.strftime("%H:%M"), max_c=max_c, whole=whole,
                   favourite=fav.label if fav else "", fav_yes_bid="", fav_yes_ask="", armed_next_kill="", armed_no_ask="")
        if fav:
            try:
                asks, bids = market.book(self.clob, fav.yes_token)
                row.update(fav_yes_bid=bids[0][0] if bids else "", fav_yes_ask=asks[0][0] if asks else "")
                # pre-arm: the favourite is the next bucket to die (when the max reaches whole+1)
                no_asks, _ = market.book(self.clob, fav.no_token)
                price = min(config.MAX_NO_PRICE, no_asks[0][0]) if no_asks else config.MAX_NO_PRICE
                row["armed_no_ask"] = no_asks[0][0] if no_asks else ""
                try:
                    armed = self.exe.prearm(fav.no_token, fav.label, price, max(5.0, config.STAKE_USD / price))
                    row["armed_next_kill"] = armed["label"]
                except Exception as e:       # noqa: BLE001 - a pre-arm (signing) problem must never block the kill logic below
                    row["armed_next_kill"] = f"prearm failed: {type(e).__name__}"
                    log.warning("prearm failed: %s: %s", type(e).__name__, e)
            except (requests.RequestException, ValueError) as e:
                row["fav_yes_bid"] = type(e).__name__
        with self.lock:
            _append(config.DATA_DIR / f"readings-{hk:%Y-%m-%d}.csv", list(row), row)
        if self.last_whole is None:
            self.last_whole = whole            # first reading after a (re)start: baseline only
            return
        if whole > self.last_whole:
            for deg in range(self.last_whole + 1, whole + 1):
                for m in market.killed_by(self.markets, deg):
                    if (str(day), m.label) not in self.done:
                        self.done.add((str(day), m.label))
                        self.decide(day, m, deg, reading_dt, max_c, seen)
            self.last_whole = whole

    # ---------------- a bucket just died ----------------
    def decide(self, day: date, m: market.Market, deg: int, reading_dt: datetime, max_c: float, seen: float):
        t0 = time.time()
        self.stats["kills"] += 1
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "mode": self.mode, "version": "v3",
               "day": str(day), "label": m.label, "new_whole": deg, "max_c": max_c, "reading_hkt": reading_dt.strftime("%H:%M"),
               "seen_hkt": datetime.fromtimestamp(seen, HKT).strftime("%H:%M:%S.%f")[:-3],
               "publish_lag_s": round(seen - reading_dt.timestamp(), 1), "market_id": m.market_id,
               "condition_id": m.condition_id, "no_token": m.no_token, "outcome": None}
        try:
            no_asks, _ = market.book(self.clob, m.no_token)
            yes_asks, yes_bids = market.book(self.clob, m.yes_token)
            f = market.fill(no_asks, config.STAKE_USD, config.MAX_NO_PRICE)
            rec.update(no_best_ask=f["best_ask"], no_depth_shares_le_max=round(f["depth_shares"], 1), no_depth_usd_le_max=round(f["depth_usd"], 2),
                       yes_best_bid=yes_bids[0][0] if yes_bids else None, yes_best_ask=yes_asks[0][0] if yes_asks else None,
                       paper_shares=round(f["shares"], 4), paper_cost=round(f["cost"], 4), paper_avg_price=f["avg_price"],
                       book_ms=round(1000 * (time.time() - t0)))
            with self.lock:
                _append(config.DATA_DIR / f"kills-{datetime.fromtimestamp(seen, HKT):%Y-%m-%d}.csv",
                        ["seen_hkt", "reading_hkt", "max_c", "new_whole_degree", "dead_bucket", "no_best_ask", "no_depth_shares_le_97c",
                         "no_depth_usd_le_97c", "yes_best_bid", "yes_best_ask", "paper_shares", "paper_cost", "book_ms"],
                        dict(seen_hkt=rec["seen_hkt"], reading_hkt=rec["reading_hkt"], max_c=max_c, new_whole_degree=deg, dead_bucket=m.label,
                             no_best_ask=f["best_ask"], no_depth_shares_le_97c=rec["no_depth_shares_le_max"], no_depth_usd_le_97c=rec["no_depth_usd_le_max"],
                             yes_best_bid=rec["yes_best_bid"], yes_best_ask=rec["yes_best_ask"], paper_shares=rec["paper_shares"],
                             paper_cost=rec["paper_cost"], book_ms=rec["book_ms"]))
        except (requests.RequestException, ValueError) as e:
            rec.update(book_error=type(e).__name__)
            f = None
        actionable = bool(f and f["shares"] > 0 and f["best_ask"] is not None and config.MIN_NO_PRICE <= f["best_ask"] <= config.MAX_NO_PRICE)
        rec["actionable"] = actionable
        if actionable and self.mode == "probe" and not config.KILL_FILE.exists():
            try:
                rec["probe"] = self.exe.probe(m.no_token, config.PROBE_PRICE, config.PROBE_SHARES)
            except Exception as ex:      # noqa: BLE001
                rec["probe"] = {"error": f"{type(ex).__name__}: {ex}"[:300]}
        elif actionable and self.mode == "live" and not config.KILL_FILE.exists():
            if self.exe.orders_today >= config.LIVE_MAX_ORDERS_PER_DAY:
                rec["live"] = "skipped: daily order cap"
            else:
                usd = min(config.STAKE_USD, config.LIVE_MAX_USD_PER_ORDER)
                try:
                    rec["live"] = self.exe.fire(m.no_token, f["best_ask"], max(5.0, min(usd / f["best_ask"], f["depth_shares"])))
                except Exception as ex:  # noqa: BLE001
                    rec["live"] = {"error": f"{type(ex).__name__}: {ex}"[:300]}
        rec["decide_ms"] = round(1000 * (time.time() - t0))
        with config.DECISIONS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
        log.info("KILL %s dead (max %.1f -> %d, published %.0fs after stamp): NO ask %s depth %s sh -> %s",
                 m.label, max_c, deg, rec["publish_lag_s"], rec.get("no_best_ask"), rec.get("no_depth_shares_le_max"),
                 "paper buy" if actionable else "nothing to buy")
        what = (f"paper: {rec.get('paper_shares')} NO at avg {round(rec.get('paper_avg_price') or 0, 4)} (${rec.get('paper_cost')})"
                if actionable else f"nothing under {config.MAX_NO_PRICE:.2f} (best NO ask {rec.get('no_best_ask')})")
        alerts.alert(f"kill_{day}_{m.label}",
                     f"KILL {day}: {m.label} is dead (HKO max {max_c} at {rec['reading_hkt']}, seen {rec['seen_hkt'][:8]}, "
                     f"{rec['publish_lag_s'] / 60:.1f} min after the stamp). NO ask {rec.get('no_best_ask')}, depth "
                     f"{rec.get('no_depth_shares_le_max')} sh -> {what}" + (f"\n{self.mode}: {json.dumps(rec.get(self.mode), default=str)[:300]}" if self.mode != "dry" else ""),
                     dedup_hours=0)

    # ---------------- threads / housekeeping ----------------
    def source_loop(self, src, url):
        n = 0
        while True:
            self.poll(src, url)
            n += 1
            time.sleep(sources.interval(src))

    def check_health(self):
        hk = datetime.now(HKT)
        if not (config.DAY_START + 0.5 <= hk.hour + hk.minute / 60 <= config.DAY_END):
            return
        if time.time() - self.last_new_any > config.STALL_MIN * 60:
            alerts.alert("stall", f"no new HKO reading from any source for {(time.time() - self.last_new_any) / 60:.0f} min (daytime)")
        for src, t in list(self.err_since.items()):
            if time.time() - t > 15 * 60:
                alerts.alert(f"err_{src}", f"source {src} failing for {(time.time() - t) / 60:.0f} min")

    def run(self):
        log.info("start %s mode=%s", config.NAME, self.mode)
        alerts.alert("start", f"started in {self.mode.upper()} mode.\nWhat v3 is: {config.DESCRIPTION}", dedup_hours=1)
        for src, url in sources.SOURCES.items():
            threading.Thread(target=self.source_loop, args=(src, url), name=src, daemon=True).start()
        last_day = datetime.now(HKT).date()
        n = 0
        while True:
            (config.STATE_DIR / "heartbeat").write_text(datetime.now(timezone.utc).isoformat(timespec="seconds"))
            n += 1
            if n % 12 == 0:
                self.check_health()
            today = datetime.now(HKT).date()
            if today != last_day:
                alerts.alert(f"daily_{today}", f"daily: {self.stats['kills']} dead buckets yesterday, {self.stats['polls']} polls. "
                                              f"v3 = {config.DESCRIPTION[:120]}...", dedup_hours=20)
                self.stats = {"kills": 0, "polls": 0}
                self.exe.orders_today = 0          # the live daily order cap counts per HKT day
                last_day = today
            time.sleep(5)
