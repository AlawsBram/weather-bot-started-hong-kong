#!/usr/bin/env python3
"""
reward_pool_logger.py  --  READ-ONLY daily logger of Polymarket Liquidity Rewards
pools for the daily-temperature markets (per city per event-day), plus a yes/no
check whether today's BTC up/down 5m / 15m rounds are rewarded.

- Standard library only (runs on system python3.12).
- No API keys, no .env, no orders, no Telegram. It only reads two public APIs
  and appends CSV files under ./data/.

Outputs (relative to the folder this script lives in):
  data/reward_pools.csv                 one row per (date_utc, city, event_date, type)
  data/reward_markets_<date>.csv.gz     raw per-market rows for that run
  data/btc_rewarded.csv                 one row per BTC slug checked
  logs/run_<date>.log                   plain-text log, one file per UTC day
  logs/run.log                          symlink/copy pointing at the latest day's log
"""
import csv
import datetime as dt
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOG_DIR = os.path.join(BASE_DIR, "logs")

CLOB_REWARDS_URL = "https://clob.polymarket.com/rewards/markets/current"
GAMMA_EVENTS_URL = "https://gamma-api.polymarket.com/events"
TEMPERATURE_TAG_ID = 103040

USER_AGENT = "reward-pool-logger/1.0 (read-only research logger; python-urllib)"
HTTP_TIMEOUT = 30
HTTP_RETRIES = 3
PAGE_PAUSE = 0.1

SLUG_RE = re.compile(
    r"^(?P<type>highest|lowest)-temperature-in-(?P<city>.+?)-on-"
    r"(?P<month>[a-z]+)-(?P<day>\d{1,2})-(?P<year>\d{4})$"
)
MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July",
     "August", "September", "October", "November", "December"], start=1)}

_log_fh = None


# --------------------------------------------------------------------------- log
def _open_log(date_str):
    global _log_fh
    os.makedirs(LOG_DIR, exist_ok=True)
    path = os.path.join(LOG_DIR, "run_%s.log" % date_str)
    _log_fh = open(path, "a", encoding="utf-8")
    # keep logs/run.log pointing at today's file (symlink on Linux, best effort)
    latest = os.path.join(LOG_DIR, "run.log")
    try:
        if os.path.islink(latest) or os.path.exists(latest):
            os.remove(latest)
        os.symlink(os.path.basename(path), latest)
    except OSError:
        pass
    # rotation: drop per-day logs older than 60 days
    cutoff = time.time() - 60 * 86400
    for name in os.listdir(LOG_DIR):
        if name.startswith("run_") and name.endswith(".log"):
            p = os.path.join(LOG_DIR, name)
            try:
                if os.path.getmtime(p) < cutoff:
                    os.remove(p)
            except OSError:
                pass


def log(msg):
    line = "%s %s" % (dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), msg)
    print(line, flush=True)
    if _log_fh:
        _log_fh.write(line + "\n")
        _log_fh.flush()


# -------------------------------------------------------------------------- http
def http_get_json(url, params=None):
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    last_err = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            req = urllib.request.Request(url, headers={
                "User-Agent": USER_AGENT, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
                body = resp.read()
            return json.loads(body.decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
                ValueError, OSError) as e:
            last_err = e
            wait = 2 ** attempt  # 2, 4, 8 s
            log("E_HTTP attempt %d/%d failed for %s : %r ; retry in %ds"
                % (attempt, HTTP_RETRIES, url[:120], e, wait))
            time.sleep(wait)
    raise RuntimeError("HTTP gave up on %s : %r" % (url, last_err))


# ----------------------------------------------------------------- CLOB rewards
def fetch_rewarded_markets():
    """Return dict condition_id -> {pool, max_spread, min_size, n_configs}."""
    out = {}
    cursor = ""
    pages = 0
    while True:
        params = {"limit": 100}
        if cursor:
            params["next_cursor"] = cursor
        d = http_get_json(CLOB_REWARDS_URL, params)
        pages += 1
        data = d.get("data") or []
        for item in data:
            cid = item.get("condition_id")
            if not cid:
                continue
            cfgs = item.get("rewards_config") or []
            pool = item.get("total_daily_rate")
            if pool is None:
                pool = sum(float(c.get("rate_per_day") or 0) for c in cfgs)
            out[cid] = {
                "pool": float(pool or 0),
                "max_spread": item.get("rewards_max_spread"),
                "min_size": item.get("rewards_min_size"),
                "n_configs": len(cfgs),
                "start_date": (cfgs[0].get("start_date") if cfgs else ""),
                "end_date": (cfgs[0].get("end_date") if cfgs else ""),
            }
        nxt = d.get("next_cursor") or ""
        if pages % 10 == 0:
            log("  clob rewards: page %d, %d markets so far" % (pages, len(out)))
        if not data or not nxt or nxt == "LTE=" or nxt == cursor:
            break
        cursor = nxt
        time.sleep(PAGE_PAUSE)
    log("clob rewards: %d pages, %d rewarded markets" % (pages, len(out)))
    return out


# ---------------------------------------------------------------- gamma events
def fetch_temperature_events():
    events = []
    offset = 0
    while True:
        batch = http_get_json(GAMMA_EVENTS_URL, {
            "tag_id": TEMPERATURE_TAG_ID, "active": "true", "closed": "false",
            "limit": 100, "offset": offset})
        if not isinstance(batch, list):
            raise RuntimeError("gamma events: unexpected payload %r" % (str(batch)[:200],))
        events.extend(batch)
        if len(batch) < 100:
            break
        offset += 100
        time.sleep(PAGE_PAUSE)
    log("gamma temperature events: %d active events" % len(events))
    return events


def parse_slug(slug):
    m = SLUG_RE.match(slug or "")
    if not m:
        return None
    month = MONTHS.get(m.group("month"))
    if not month:
        return None
    try:
        event_date = dt.date(int(m.group("year")), month, int(m.group("day")))
    except ValueError:
        return None
    return m.group("type"), m.group("city").replace("-", " "), event_date.isoformat()


def fee_rate_of(market):
    fs = market.get("feeSchedule")
    if isinstance(fs, str):
        try:
            fs = json.loads(fs)
        except ValueError:
            fs = None
    if isinstance(fs, dict) and fs.get("rate") is not None:
        try:
            return float(fs.get("rate"))
        except (TypeError, ValueError):
            pass
    return ""


# ------------------------------------------------------------------ BTC check
def check_btc_rewarded(rewards, now):
    epoch = int(now.timestamp())
    slugs = [
        "btc-updown-5m-%d" % (epoch - epoch % 300),
        "btc-updown-15m-%d" % (epoch - epoch % 900),
    ]
    rows = []
    for slug in slugs:
        rewarded = "no"
        found = "no"
        cids = []
        try:
            ev = http_get_json(GAMMA_EVENTS_URL, {"slug": slug})
            if isinstance(ev, list) and ev:
                found = "yes"
                for m in ev[0].get("markets") or []:
                    cid = m.get("conditionId")
                    if cid:
                        cids.append(cid)
                if any(c in rewards and rewards[c]["pool"] > 0 for c in cids):
                    rewarded = "yes"
        except Exception as e:  # noqa: BLE001
            log("E_BTC %s : %r" % (slug, e))
            found = "error"
        rows.append({"date_utc": now.date().isoformat(), "slug": slug,
                     "event_found": found, "rewarded": rewarded,
                     "condition_ids": ";".join(cids)})
        time.sleep(PAGE_PAUSE)
    return rows


# --------------------------------------------------------------------- csv io
def append_csv(path, fieldnames, rows, key_fields=("date_utc",)):
    """Append rows; if the file already holds rows with the same key (default:
    same date_utc) they are replaced, so re-running on the same UTC day never
    duplicates rows (manual runs, Persistent= catch-ups)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    new_keys = {tuple(str(r.get(k, "")) for k in key_fields) for r in rows}
    kept = []
    if os.path.exists(path) and os.path.getsize(path) > 0:
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if tuple(str(r.get(k, "")) for k in key_fields) in new_keys:
                    continue
                kept.append(r)
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for r in kept:
            w.writerow(r)
        for r in rows:
            w.writerow(r)
    os.replace(tmp, path)


# ------------------------------------------------------------------------ main
def main():
    now = dt.datetime.now(dt.timezone.utc)
    date_str = now.date().isoformat()
    _open_log(date_str)
    log("=== reward_pool_logger start (date_utc=%s) ===" % date_str)

    rewards = fetch_rewarded_markets()
    events = fetch_temperature_events()

    pool_rows = []
    market_rows = []
    skipped = 0
    for ev in events:
        parsed = parse_slug(ev.get("slug"))
        if not parsed:
            skipped += 1
            log("  skip non-matching slug: %s" % ev.get("slug"))
            continue
        ev_type, city, event_date = parsed
        markets = ev.get("markets") or []
        n_rewarded = 0
        pool_total = 0.0
        max_spread = ""
        min_size = ""
        fee_rates = set()
        top_q, top_pool = "", 0.0
        for m in markets:
            cid = m.get("conditionId") or ""
            r = rewards.get(cid)
            pool = r["pool"] if r else 0.0
            fee = fee_rate_of(m)
            if fee != "":
                fee_rates.add(fee)
            if r:
                if pool > 0:
                    n_rewarded += 1
                pool_total += pool
                if max_spread == "" and r["max_spread"] is not None:
                    max_spread = r["max_spread"]
                if min_size == "" and r["min_size"] is not None:
                    min_size = r["min_size"]
                if pool > top_pool:
                    top_pool, top_q = pool, m.get("question", "")
            market_rows.append({
                "date_utc": date_str, "condition_id": cid,
                "slug": m.get("slug", ""), "event_slug": ev.get("slug", ""),
                "city": city, "event_date": event_date, "event_type": ev_type,
                "question": m.get("question", ""),
                "pool": pool if r else "",
                "max_spread": r["max_spread"] if r else "",
                "min_size": r["min_size"] if r else "",
                "fee_rate": fee,
                "rewarded": "yes" if (r and pool > 0) else "no",
            })
        fee_rate_str = ";".join(str(x) for x in sorted(fee_rates)) if fee_rates else ""
        pool_rows.append({
            "date_utc": date_str, "city": city, "event_date": event_date,
            "event_type": ev_type, "event_slug": ev.get("slug", ""),
            "n_markets": len(markets), "n_rewarded": n_rewarded,
            "pool_usd_per_day_total": round(pool_total, 4),
            "max_spread": max_spread, "min_size": min_size,
            "fee_rate_from_gamma": fee_rate_str,
            "top_bucket_question": top_q, "top_bucket_pool": top_pool,
        })

    pool_rows.sort(key=lambda r: (r["city"], r["event_date"], r["event_type"]))

    pool_fields = ["date_utc", "city", "event_date", "event_type", "event_slug",
                   "n_markets", "n_rewarded", "pool_usd_per_day_total",
                   "max_spread", "min_size", "fee_rate_from_gamma",
                   "top_bucket_question", "top_bucket_pool"]
    append_csv(os.path.join(DATA_DIR, "reward_pools.csv"), pool_fields, pool_rows)

    market_fields = ["date_utc", "condition_id", "slug", "event_slug", "city",
                     "event_date", "event_type", "question", "pool", "max_spread",
                     "min_size", "fee_rate", "rewarded"]
    os.makedirs(DATA_DIR, exist_ok=True)
    gz_path = os.path.join(DATA_DIR, "reward_markets_%s.csv.gz" % date_str)
    with gzip.open(gz_path, "wt", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=market_fields)
        w.writeheader()
        for r in market_rows:
            w.writerow(r)

    btc_rows = check_btc_rewarded(rewards, now)
    append_csv(os.path.join(DATA_DIR, "btc_rewarded.csv"),
               ["date_utc", "slug", "event_found", "rewarded", "condition_ids"], btc_rows,
               key_fields=("date_utc", "slug"))

    # ------------------------------------------------------------- summary
    by_city = {}
    for r in pool_rows:
        by_city.setdefault(r["city"], []).append(r["pool_usd_per_day_total"])
    # Two views of "pool per city":
    #  - per event  = one city, one event date, highest OR lowest (~$100 for NYC)
    #  - city total = sum over ALL of that city's active events (today + the next
    #    2-3 days, highest + lowest). This is the number quoted as "NYC $604".
    city_event = {c: max(v) for c, v in by_city.items()}
    city_total = {c: sum(v) for c, v in by_city.items()}
    city_days = {r["city"]: set() for r in pool_rows}
    for r in pool_rows:
        city_days[r["city"]].add(r["event_date"])
    top = sorted(city_total.items(), key=lambda kv: -kv[1])[:10]
    log("SUMMARY events=%d rows=%d skipped=%d cities=%d markets=%d rewarded_markets=%d"
        % (len(events), len(pool_rows), skipped, len(by_city), len(market_rows),
           sum(1 for m in market_rows if m["rewarded"] == "yes")))
    log("SUMMARY top10 cities by TOTAL pool/day over all active events: "
        + ", ".join("%s $%.0f (%d dates)" % (c, v, len(city_days[c])) for c, v in top))
    log("SUMMARY largest single-event pool/day: "
        + ", ".join("%s $%.0f" % kv for kv in
                    sorted(city_event.items(), key=lambda kv: -kv[1])[:5]))
    log("SUMMARY hong kong: total $%.0f over %d dates, per event $%.0f"
        % (city_total.get("hong kong", 0.0), len(city_days.get("hong kong", ())),
           city_event.get("hong kong", 0.0)))
    zero = sorted(c for c, v in city_total.items() if v == 0)
    log("SUMMARY zero-pool cities: %s" % (", ".join(zero) if zero else "none"))
    spreads = sorted({str(r["max_spread"]) for r in pool_rows if r["max_spread"] != ""})
    sizes = sorted({str(r["min_size"]) for r in pool_rows if r["min_size"] != ""})
    log("SUMMARY max_spread values: %s ; min_size values: %s" % (spreads, sizes))
    fee_by_city = {}
    for r in pool_rows:
        fee_by_city.setdefault(r["city"], set()).update(
            x for x in r["fee_rate_from_gamma"].split(";") if x != "")
    n_zero_fee = sum(1 for c, s in fee_by_city.items() if s <= {"0.0", "0"})
    nonzero = {c: sorted(s) for c, s in fee_by_city.items() if not s <= {"0.0", "0"}}
    log("SUMMARY fee rate 0 for %d of %d cities; non-zero: %s"
        % (n_zero_fee, len(fee_by_city), nonzero if nonzero else "none"))
    for b in btc_rows:
        log("SUMMARY BTC %s found=%s rewarded=%s" % (b["slug"], b["event_found"], b["rewarded"]))
    log("files: %s ; %s ; %s" % (os.path.join(DATA_DIR, "reward_pools.csv"), gz_path,
                                 os.path.join(DATA_DIR, "btc_rewarded.csv")))
    log("=== done ===")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log("E_FATAL %r" % (e,))
        sys.exit(1)
