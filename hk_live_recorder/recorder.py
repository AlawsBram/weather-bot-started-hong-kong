"""HK live recorder: WHEN does each HKO source publish each new Observatory reading?

Read-only. No orders, no keys. Polls public HKO pages and logs the first moment each new reading
appears, so we can measure (1) the delay from HKO's reading time to publication, per source, and
(with reconcile.py) (2) whether those readings match the official daily max.

Sources (all public), each polled in its OWN thread so a slow one never delays a fast one:
  text_hko    www.hko.gov.hk text-only "latest readings" page       (temp + max/min) - fastest so far
  text_wx     www.weather.gov.hk mirror of the same page
  csv_maxmin  data.weather.gov.hk latest_since_midnight_maxmin.csv (max / min since midnight)
  csv_1min    data.weather.gov.hk latest_1min_temperature.csv      (HK Observatory air temp; 10-min steps)
  rhrread     HKO open-data current weather report (whole degrees, ~hourly)
Polite: one keep-alive connection per source, conditional requests, small pages; night = 60 s.
Output: data/versions-YYYY-MM-DD.csv (HKT date), one row per NEW reading per source.
"""
import csv
import json
import os
import re
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
STATE = DATA / "state"
HKBOT = Path(os.environ.get("HKBOT_DIR", Path.home() / "hkbot"))
sys.path.insert(0, str(HKBOT))
HKT = timezone(timedelta(hours=8))

SOURCES = {
    "text_hko": "https://www.hko.gov.hk/textonly/v2/forecast/text_readings_e.htm",
    "text_wx": "https://www.weather.gov.hk/wxinfo/ts/text_readings_e.htm",
    "csv_maxmin": "https://data.weather.gov.hk/weatherAPI/hko_data/regional-weather/latest_since_midnight_maxmin.csv",
    "csv_1min": "https://data.weather.gov.hk/weatherAPI/hko_data/regional-weather/latest_1min_temperature.csv",
    "rhrread": "https://data.weather.gov.hk/weatherAPI/opendata/weather.php?dataType=rhrread&lang=en",
}
DAY_INTERVAL = {"text_hko": 2, "text_wx": 10, "csv_maxmin": 10, "csv_1min": 10, "rhrread": 30}   # seconds, inside the window
# HKO stamps readings every 10 min (:00, :10, ...) and publishes them 7:15-8:45 later (measured 2026-10-22).
# Fast polling is only needed in that window; outside it a slow heartbeat still catches anything unusual.
WINDOW_FROM, WINDOW_TO = 5.5, 9.75     # minutes after each 10-minute stamp
OUTSIDE_INTERVAL = 20
DAY_START, DAY_END = 6.5, 20.5          # HKT hours with fast polling (HK markets are daily HIGHS)
NIGHT_INTERVAL = 60
STALL_MIN = 25                          # no new reading from ANY source for this long by day -> alert
FIELDS = ["first_seen_utc", "first_seen_hkt", "source", "reading_hkt", "temp", "max", "min",
          "lag_s", "http_last_modified", "poll_ms"]


def parse(src, text):
    """-> (reading_dt HKT, temp, max, min) or None."""
    if src in ("csv_1min", "csv_maxmin"):
        for line in text.splitlines():
            p = [x.strip() for x in line.split(",")]
            if len(p) >= 3 and p[1] == "HK Observatory":
                dt = datetime.strptime(p[0], "%Y%m%d%H%M").replace(tzinfo=HKT)
                if src == "csv_1min":
                    return dt, float(p[2]), None, None
                return dt, None, float(p[2]), float(p[3])
        return None
    if src.startswith("text_"):
        flat = re.sub(r"<[^>]+>", " ", text)
        m = re.search(r"recorded at (\d{1,2}):(\d{2}) Hong Kong Time (\d{1,2}) (\w+) (\d{4})", flat)
        r = re.search(r"HK Observatory\s+(-?\d+\.\d)\s+\d+\s+(-?\d+\.\d)\s*/\s*(-?\d+\.\d)", flat)
        if not (m and r):
            return None
        dt = datetime.strptime(f"{m.group(3)} {m.group(4)} {m.group(5)} {m.group(1)}:{m.group(2)}",
                               "%d %B %Y %H:%M").replace(tzinfo=HKT)
        return dt, float(r.group(1)), float(r.group(2)), float(r.group(3))
    if src == "rhrread":
        d = json.loads(text)
        dt = datetime.fromisoformat(d["temperature"]["recordTime"])
        v = next((x["value"] for x in d["temperature"]["data"] if x["place"] == "Hong Kong Observatory"), None)
        return (dt.astimezone(HKT), float(v), None, None) if v is not None else None
    return None


def is_day():
    hk = datetime.now(HKT)
    return DAY_START <= hk.hour + hk.minute / 60 <= DAY_END


def in_publish_window():
    """True from stamp+5:30 to stamp+9:45 of every 10-minute cycle."""
    hk = datetime.now(HKT)
    m = (hk.minute % 10) + hk.second / 60
    return WINDOW_FROM <= m <= WINDOW_TO


def interval(src):
    if not is_day():
        return NIGHT_INTERVAL
    return DAY_INTERVAL[src] if in_publish_window() else max(DAY_INTERVAL[src], OUTSIDE_INTERVAL)


class Poller:
    def __init__(self):
        self.sess = {}
        for src in SOURCES:
            s = requests.Session()
            s.headers["User-Agent"] = "hk-live-recorder/1.1 (read-only research; low rate)"
            self.sess[src] = s
        self.etag, self.lastmod, self.last = {}, {}, {}
        self.err_since = {}
        self.last_new_any = time.time()
        self.lock = threading.Lock()
        self.load_last()
        try:
            from kills import KillWatcher
            self.kills = KillWatcher(DATA)
        except Exception as e:                 # noqa: BLE001 - the recorder must run even without hkbot
            print(f"kill watcher disabled: {type(e).__name__}: {e}", flush=True)
            self.kills = None

    def load_last(self):
        """Avoid logging the already-known reading again after a restart."""
        f = DATA / f"versions-{datetime.now(HKT):%Y-%m-%d}.csv"
        if f.exists():
            for row in csv.DictReader(f.open(encoding="utf-8")):
                self.last[row["source"]] = (row["reading_hkt"], row["temp"], row["max"], row["min"])

    def poll(self, src, url):
        h = {}
        if src in self.etag:
            h["If-None-Match"] = self.etag[src]
        if src in self.lastmod:
            h["If-Modified-Since"] = self.lastmod[src]
        t0 = time.time()
        try:
            r = self.sess[src].get(url, headers=h, timeout=10)
        except requests.RequestException as e:
            self.err_since.setdefault(src, t0)
            return f"{type(e).__name__}"
        seen = time.time()
        if r.status_code == 304:
            self.err_since.pop(src, None)
            return None
        if r.status_code != 200:
            self.err_since.setdefault(src, t0)
            return f"HTTP {r.status_code}"
        self.err_since.pop(src, None)
        if r.headers.get("ETag"):
            self.etag[src] = r.headers["ETag"]
        if r.headers.get("Last-Modified"):
            self.lastmod[src] = r.headers["Last-Modified"]
        try:
            got = parse(src, r.content.decode("utf-8-sig", errors="replace"))
        except (ValueError, KeyError, TypeError):
            got = None
        if not got:
            return "parse failed"
        dt, temp, mx, mn = got
        key = (dt.strftime("%Y-%m-%d %H:%M"), *("" if v is None else f"{v:.1f}" for v in (temp, mx, mn)))
        if self.last.get(src) == key:
            return None
        self.last[src] = key
        self.last_new_any = seen
        now_hkt = datetime.fromtimestamp(seen, HKT)
        row = dict(first_seen_utc=datetime.fromtimestamp(seen, timezone.utc).isoformat(timespec="milliseconds"),
                   first_seen_hkt=now_hkt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3], source=src,
                   reading_hkt=key[0], temp=key[1], max=key[2], min=key[3],
                   lag_s=round(seen - dt.timestamp(), 1), http_last_modified=r.headers.get("Last-Modified", ""),
                   poll_ms=round(1000 * (seen - t0)))
        f = DATA / f"versions-{now_hkt:%Y-%m-%d}.csv"
        with self.lock:
            new = not f.exists()
            with f.open("a", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=FIELDS)
                if new:
                    w.writeheader()
                w.writerow(row)
        if src == "text_hko" and mx is not None and self.kills is not None:
            try:
                self.kills.on_reading(dt, mx, seen)     # the moment a bucket dies: read its book
            except Exception as e:                      # noqa: BLE001
                print(f"[{datetime.now(timezone.utc):%FT%TZ}] kill watcher error: {type(e).__name__}: {e}", flush=True)
        return None

    def source_loop(self, src, url):
        n = 0
        while True:
            err = self.poll(src, url)
            if err and n % 30 == 0:
                print(f"[{datetime.now(timezone.utc):%FT%TZ}] {src}: {err}", flush=True)
            n += 1
            time.sleep(interval(src))

    def check_health(self):
        hk = datetime.now(HKT)
        if not (DAY_START + 0.5 <= hk.hour + hk.minute / 60 <= DAY_END):
            return
        from hkbot import alerts
        if time.time() - self.last_new_any > STALL_MIN * 60:
            alerts.alert("live_recorder_stall", f"HK live recorder: no new HKO reading from any source for "
                                                f"{(time.time() - self.last_new_any) / 60:.0f} min (daytime).")
        for src, t in list(self.err_since.items()):
            if time.time() - t > 15 * 60:
                alerts.alert(f"live_recorder_err_{src}", f"HK live recorder: source {src} failing for "
                                                         f"{(time.time() - t) / 60:.0f} min.")

    def run(self):
        STATE.mkdir(parents=True, exist_ok=True)
        print(f"[{datetime.now(timezone.utc):%FT%TZ}] recorder start (one thread per source: "
              + ", ".join(f"{s} {i}s" for s, i in DAY_INTERVAL.items())
              + f"; fast only {WINDOW_FROM}-{WINDOW_TO} min after each 10-min stamp, else {OUTSIDE_INTERVAL}s)", flush=True)
        for src, url in SOURCES.items():
            threading.Thread(target=self.source_loop, args=(src, url), name=src, daemon=True).start()
        n = 0
        while True:
            (STATE / "heartbeat").write_text(datetime.now(timezone.utc).isoformat(timespec="seconds"))
            n += 1
            if n % 12 == 0:
                self.check_health()
            time.sleep(5)


if __name__ == "__main__":
    Poller().run()
