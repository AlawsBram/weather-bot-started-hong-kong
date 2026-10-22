"""The loop: read the resolution feed, find buckets that just died, price their NO, act (dry/probe/live).

Every poll (default 5 s): ONE request to aviationweather.gov for every station with a market whose
day window is open now (or opens within 2 h). For every routine report inside a window the day's
extreme is updated; a bucket that becomes impossible is a KILL. For each kill the NO book is read,
a paper fill (or a probe / live order) is recorded in data/decisions.jsonl, and the feed delay
(our first sight of the report minus its observation time) goes to data/feed_lag.csv.
"""
from __future__ import annotations

import csv
import json
import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import requests

from . import alerts, book, config, metar, rules
from .buckets import is_dead, margin_tenths, whole_degrees
from .execution import Executor

log = logging.getLogger("v2")


class Bot:
    def __init__(self, mode: str):
        self.mode = mode
        self.s = requests.Session()
        self.s.headers["User-Agent"] = "deadbucket-v2/0.1 (research; read-only unless configured)"
        self.exe = Executor(mode)
        self.events: dict[str, rules.Event] = {}
        self.extreme: dict[str, tuple[int, metar.Report]] = {}      # slug -> (tenths, report that set it)
        self.seen_reports: set[tuple[str, int, str]] = set()
        self.done: set[tuple[str, str]] = set()                      # (slug, label) already decided
        self.rules_loaded = 0.0
        self.first_poll = True          # the first poll back-fills old reports: their "delay" is meaningless
        self.last_daily = datetime.now(timezone.utc).date()
        self.stats = defaultdict(int)
        config.STATE_DIR.mkdir(parents=True, exist_ok=True)
        if config.DECISIONS_FILE.exists():
            for line in config.DECISIONS_FILE.read_text(encoding="utf-8").splitlines():
                try:
                    d = json.loads(line)
                    self.done.add((d["slug"], d["label"]))
                except (ValueError, KeyError):
                    pass

    # ---------------- rules / discovery ----------------
    def refresh_rules(self):
        try:
            evs = rules.load_events(self.s)
        except requests.RequestException as e:
            alerts.alert("rules_fetch", f"cannot read markets from Gamma: {type(e).__name__}")
            return
        self.events = {e.slug: e for e in evs}
        changes = rules.update_station_table(evs, config.STATIONS_FILE)
        if changes:
            alerts.alert("station_change_" + str(hash(tuple(changes)) % 10 ** 6),
                         "RULES CHANGED (station / source):\n" + "\n".join(changes[:15]), dedup_hours=24)
        unsupported = defaultdict(int)
        for e in evs:
            if not e.supported:
                unsupported[e.style if not e.problems else "problem"] += 1
        sup = sum(e.supported for e in evs)
        log.info("rules: %d events, %d tradeable (metar), others %s", len(evs), sup, dict(unsupported))
        probs = [f"{e.slug}: {e.problems}" for e in evs if e.problems and e.style == "metar"]
        if probs:
            alerts.alert("rules_problems", "METAR events with rule problems (NOT traded):\n" + "\n".join(probs[:10]))
        self.rules_loaded = time.time()
        self.stats["events"], self.stats["tradeable"] = len(evs), sup

    def active_events(self, now: datetime) -> list[rules.Event]:
        soon = now + timedelta(hours=2)
        return [e for e in self.events.values() if e.supported and not all(m.closed for m in e.markets)
                and e.window_start <= soon and now < e.window_end + timedelta(hours=1)]

    # ---------------- one poll ----------------
    def poll(self):
        now = datetime.now(timezone.utc)
        active = self.active_events(now)
        icaos = sorted({e.icao for e in active})
        if not icaos:
            return
        try:
            reports = metar.fetch(self.s, icaos, hours=2)
        except (requests.RequestException, ValueError) as e:
            self.stats["feed_errors"] += 1
            if self.stats["feed_errors"] % 20 == 1:
                alerts.alert("feed_error", f"METAR feed error: {type(e).__name__} (x{self.stats['feed_errors']})")
            return
        self.stats["feed_errors"] = 0
        by_icao = defaultdict(list)
        for r in reports:
            k = (r.icao, int(r.obs_time.timestamp()), r.raw)
            if k in self.seen_reports:
                continue
            self.seen_reports.add(k)
            by_icao[r.icao].append(r)
            self.log_feed_lag(r, backfill=self.first_poll)
        self.first_poll = False
        if len(self.seen_reports) > 50000:
            self.seen_reports = set(list(self.seen_reports)[-20000:])
        for e in active:
            for r in by_icao.get(e.icao, []):
                if not r.routine or r.tenths_c is None or not (e.window_start <= r.obs_time < e.window_end):
                    continue
                cur = self.extreme.get(e.slug)
                better = cur is None or (r.tenths_c > cur[0] if e.kind == "highest" else r.tenths_c < cur[0])
                if better:
                    self.extreme[e.slug] = (r.tenths_c, r)
                    self.on_new_extreme(e, r)

    def log_feed_lag(self, r: metar.Report, backfill: bool = False):
        new = not config.FEED_LAG_FILE.exists()
        with config.FEED_LAG_FILE.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["first_seen_utc", "icao", "obs_time_utc", "lag_s", "backfill", "routine", "tenths_c", "raw"])
            w.writerow([datetime.fromtimestamp(r.seen, timezone.utc).isoformat(timespec="milliseconds"), r.icao,
                        r.obs_time.isoformat(), round(r.seen - r.obs_time.timestamp(), 1), backfill, r.routine, r.tenths_c, r.raw])

    # ---------------- kills ----------------
    def on_new_extreme(self, e: rules.Event, r: metar.Report):
        t = r.tenths_c
        for m in e.markets:
            if (e.slug, m.label) in self.done or m.closed or not is_dead(m.bucket, e.kind, t):
                continue
            self.done.add((e.slug, m.label))
            self.stats["kills"] += 1
            self.decide(e, m, r)

    def decide(self, e: rules.Event, m: rules.Market, r: metar.Report):
        t0 = time.time()
        margin = margin_tenths(m.bucket, e.kind, r.tenths_c)
        rec = {"ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"), "mode": self.mode,
               "slug": e.slug, "city": e.city, "kind": e.kind, "icao": e.icao, "label": m.label, "unit": e.unit,
               "report_obs_utc": r.obs_time.isoformat(), "report_raw": r.raw, "tenths_c": r.tenths_c,
               "whole": whole_degrees(r.tenths_c, e.unit), "margin_tenths": margin,
               "feed_lag_s": round(r.seen - r.obs_time.timestamp(), 1), "market_id": m.market_id,
               "condition_id": m.condition_id, "no_token": m.no_token, "outcome": None}
        try:
            asks = book.no_asks(self.s, m.no_token)
            f = book.fill(asks, config.STAKE_USD, config.MAX_NO_PRICE)
            rec.update(no_best_ask=f["best_ask"], no_depth_shares_under_max=f["depth_shares"],
                       paper_shares=round(f["shares"], 4), paper_cost=round(f["cost"], 4),
                       paper_avg_price=f["avg_price"], book_ms=round(1000 * (time.time() - t0)))
        except (requests.RequestException, ValueError) as ex:
            rec.update(book_error=type(ex).__name__)
            f = None
        actionable = (f and f["shares"] > 0 and f["best_ask"] is not None
                      and config.MIN_NO_PRICE <= f["best_ask"] <= config.MAX_NO_PRICE
                      and (margin or 0) >= config.MIN_MARGIN_TENTHS)
        rec["actionable"] = bool(actionable)
        if actionable and self.mode == "probe" and not config.KILL_FILE.exists():
            try:
                rec["probe"] = self.exe.probe(m.no_token, config.PROBE_PRICE, config.PROBE_SHARES)
            except Exception as ex:      # noqa: BLE001 - never let an order error kill the loop
                rec["probe"] = {"error": f"{type(ex).__name__}: {ex}"[:300]}
        elif actionable and self.mode == "live" and not config.KILL_FILE.exists():
            if self.exe.orders_today >= config.LIVE_MAX_ORDERS_PER_DAY:
                rec["live"] = "skipped: daily order cap"
            else:
                usd = min(config.STAKE_USD, config.LIVE_MAX_USD_PER_ORDER)
                shares = max(5.0, min(usd / f["best_ask"], f["depth_shares"]))
                try:
                    rec["live"] = self.exe.buy_no(m.no_token, f["best_ask"], shares)
                except Exception as ex:  # noqa: BLE001
                    rec["live"] = {"error": f"{type(ex).__name__}: {ex}"[:300]}
        rec["decide_ms"] = round(1000 * (time.time() - t0))
        with config.DECISIONS_FILE.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        log.info("KILL %s %s %s: %s dead (whole %s, margin %s/10) NO ask %s depth %s -> %s",
                 e.city, e.kind, e.day, m.label, rec["whole"], margin, rec.get("no_best_ask"),
                 rec.get("no_depth_shares_under_max"), "ACT" if actionable else "log only")
        if self.mode != "dry" and actionable:
            alerts.alert(f"act_{e.slug}_{m.label}", f"{self.mode.upper()} {e.city} {e.kind} {e.day}: {m.label} dead "
                                                      f"(reading {r.tenths_c / 10} C), NO ask {rec.get('no_best_ask')}: "
                                                      f"{json.dumps(rec.get(self.mode))[:400]}", dedup_hours=0)

    # ---------------- housekeeping ----------------
    def daily_summary(self):
        today = datetime.now(timezone.utc).date()
        if today == self.last_daily:
            return
        self.last_daily = today
        lags = []
        if config.FEED_LAG_FILE.exists():
            rows = list(csv.DictReader(config.FEED_LAG_FILE.open(encoding="utf-8")))
            lags = sorted(float(r["lag_s"]) for r in rows if r["first_seen_utc"][:10] == (today - timedelta(days=1)).isoformat()
                          and r.get("backfill") != "True")
        n = self.stats["kills"]
        self.stats["kills"] = 0
        med = f"{lags[len(lags) // 2] / 60:.1f} min" if lags else "n/a"
        alerts.alert(f"daily_{today}", f"daily ({self.mode}): {n} dead buckets found, {self.stats['tradeable']} tradeable "
                                       f"markets, feed delay median {med} over {len(lags)} reports", dedup_hours=20)

    def run(self):
        log.info("start mode=%s poll=%ss stake=$%s max NO %s", self.mode, config.POLL_SECONDS, config.STAKE_USD, config.MAX_NO_PRICE)
        alerts.alert("start", f"started in {self.mode.upper()} mode (poll {config.POLL_SECONDS}s).\nWhat v2 is: {config.DESCRIPTION}", dedup_hours=1)
        while True:
            if time.time() - self.rules_loaded > config.RULES_REFRESH_HOURS * 3600:
                self.refresh_rules()
            t0 = time.time()
            try:
                self.poll()
            except Exception as ex:      # noqa: BLE001
                log.exception("poll failed")
                alerts.alert("poll_crash", f"poll failed: {type(ex).__name__}: {ex}"[:300])
            (config.STATE_DIR / "heartbeat").write_text(datetime.now(timezone.utc).isoformat(timespec="seconds"))
            self.daily_summary()
            time.sleep(max(0.5, config.POLL_SECONDS - (time.time() - t0)))
