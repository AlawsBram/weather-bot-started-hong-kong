"""One snapshot round (run every 30 minutes by the VPS schedule).

Each round saves, for TODAY and TOMORROW (Hong Kong time):
  * every forecast number from the official data feeds (HKO 9-day, ECMWF / GFS / ICON via Open-Meteo),
  * HKO's live "max since midnight" and current temperature,
  * the full Polymarket order book of every bucket (every bid and ask with sizes) + the raw market data,
  * what $10 would really cost per bucket (walking the book, taker fee included),
  * screenshots + the numbers read off the pages (Polymarket every round, HKO pages every 3 h and
    right after the ~11:30 / ~16:30 HKT bulletins),
  * a cross-check of every feed number against the number shown on the page.

Files go to data/snapshots/<YYYY-MM-DDTHH-MM>HKT/ and the numbers into data/ledger.sqlite.
Anything that fails or disagrees raises a Telegram alert (rate-limited, see alerts.py).
"""
from __future__ import annotations

import gzip
import json
import logging
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from . import alerts, config, sources
from .ledger import db
from .pages import Browser

log = logging.getLogger(__name__)
MODEL_NAMES = tuple(config.MODEL_SOURCES.values())


# ----------------------------------------------------------------------------
# Order-book maths
# ----------------------------------------------------------------------------

def no_levels(yes_book: dict | None, no_book: dict | None) -> list:
    """Ask levels for buying NO: the NO token's own asks, else the mirror of the YES bids."""
    if no_book and no_book.get("asks"):
        return no_book["asks"]
    if yes_book and yes_book.get("bids"):
        return [[round(1 - p, 6), s] for p, s in yes_book["bids"]]
    return []


def cost_to_buy(levels: list, usd: float, fee_rate: float | None) -> dict:
    """What `usd` really buys: average book price, taker fee, all-in price per share, shares."""
    avg, shares = sources.fill_price(levels, usd)
    if avg is None:
        return {"avg": None, "fee": None, "all_in": None, "shares": 0.0, "full": False}
    fee = sources.taker_fee_per_share(avg, fee_rate)
    return {"avg": avg, "fee": fee, "all_in": avg + fee, "shares": usd / (avg + fee),
            "full": shares * avg >= usd - 0.01}


def depth_within(levels: list, cents: float = 0.02) -> float:
    if not levels:
        return 0.0
    best = levels[0][0]
    return sum(p * s for p, s in levels if p <= best + cents + 1e-9)


def quote_rows(snap: sources.MarketSnapshot, books: dict, usd: float = 10.0) -> list[dict]:
    rows = []
    for mb in snap.buckets:
        yb, nb = books.get(mb.yes_token or ""), books.get(mb.no_token or "")
        y_asks = (yb or {}).get("asks", [])
        y_bids = (yb or {}).get("bids", [])
        n_asks = no_levels(yb, nb)
        cy, cn = cost_to_buy(y_asks, usd, mb.fee_rate), cost_to_buy(n_asks, usd, mb.fee_rate)
        rows.append({
            "label": mb.bucket.label,
            "yes_bid": y_bids[0][0] if y_bids else mb.yes_bid,
            "yes_ask": y_asks[0][0] if y_asks else mb.yes_ask,
            "yes_bid_size": y_bids[0][1] if y_bids else None,
            "yes_ask_size": y_asks[0][1] if y_asks else None,
            "no_ask": n_asks[0][0] if n_asks else None,
            "no_ask_size": n_asks[0][1] if n_asks else None,
            "yes_cost_10": cy["all_in"], "no_cost_10": cn["all_in"],
            "depth_yes_usd": depth_within(y_asks), "depth_no_usd": depth_within(n_asks),
            "fee_rate": mb.fee_rate, "volume": mb.volume,
        })
    return rows


# ----------------------------------------------------------------------------
# Schedule helpers
# ----------------------------------------------------------------------------

def _state() -> dict:
    p = config.STATE_DIR / "snapshot_state.json"
    try:
        return json.loads(p.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(st: dict) -> None:
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    (config.STATE_DIR / "snapshot_state.json").write_text(json.dumps(st, indent=1))


def hko_pages_due(now: datetime, last_ts: float | None, force: bool = False) -> bool:
    """HKO website: at most every HKO_PAGE_EVERY_HOURS, plus right after the two bulletins."""
    if force or not last_ts:
        return True
    since = now.timestamp() - last_ts
    if since >= config.HKO_PAGE_EVERY_HOURS * 3600 - 300:
        return True
    for hhmm in config.HKO_PAGE_EXTRA_HKT:
        h, m = map(int, hhmm.split(":"))
        slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
        if 0 <= (now - slot).total_seconds() < 25 * 60 and since > 25 * 60:
            return True
    return False


# ----------------------------------------------------------------------------
# The round
# ----------------------------------------------------------------------------

def run_snapshot(force_pages: bool = False, no_pages: bool = False) -> int:
    t0 = time.time()
    now = sources.now_hkt()
    run_ts = now.isoformat(timespec="seconds")
    today, tomorrow = now.date(), now.date() + timedelta(days=1)
    targets = [today, tomorrow]
    folder = config.SNAPSHOT_DIR / now.strftime("%Y-%m-%dT%H-%MHKT")
    folder.mkdir(parents=True, exist_ok=True)
    st = _state()
    status: dict[str, str] = {}          # source -> "ok" or error text
    rec: dict = {"run_ts": run_ts, "targets": [d.isoformat() for d in targets]}

    # ---- 1. Forecast feeds -------------------------------------------------------------
    fnd = sources.fetch_hko_9day()
    status["hko_9day_feed"] = "ok" if fnd else "no data from the HKO open-data 9-day feed"
    web_fnd = sources.fetch_hko_web_fnd()
    status["hko_9day_page_datafile"] = "ok" if web_fnd else "no data from the 9-day page's data file"
    models = sources.fetch_model_forecasts(today, tomorrow)
    for name in MODEL_NAMES:
        status[f"model_{name}"] = "ok" if name in models else f"Open-Meteo returned nothing for {name}"
    obs = sources.fetch_hko_max_since_midnight()
    status["hko_max_since_midnight"] = "ok" if obs is not None else "HKO max-since-midnight not readable"
    cur = sources.fetch_hko_current_temp()
    status["hko_current_temp"] = "ok" if cur is not None else "HKO current temperature not readable"
    flw = sources.fetch_hko_local_forecast()
    status["hko_local_forecast"] = "ok" if flw.get("max") is not None else "HKO Local Weather Forecast max not readable"
    warn = sources.fetch_hko_warnings()
    status["hko_warnings"] = "ok" if "_error" not in warn else "HKO warning summary unreadable"

    fc: dict[str, dict[str, float]] = {}
    fallback_used: list[str] = []
    for d in targets:
        row = {}
        if d in fnd:
            row["hko_official"] = fnd[d]
        elif d in web_fnd:                              # fallback 1: the page's own data file
            row["hko_official"] = web_fnd[d]
            fallback_used.append(f"{d}: HKO forecast taken from the page data file")
        for name, series in models.items():
            if d in series:
                row[name] = series[d]
        # HKO's Local Weather Forecast covers only today or tomorrow; logged as its own source
        # ("hko_local") so it is scored separately, the same way the history backtest uses it.
        if flw.get("target") == d and flw.get("max") is not None:
            row["hko_local"] = flw["max"]
        fc[d.isoformat()] = row
    rec.update(hko_local_forecast={**flw, "target": str(flw.get("target"))},
               hko_warnings={k: v for k, v in warn.items() if k != "_error"})
    rec.update(forecasts=fc, hko_9day_feed={k.isoformat(): v for k, v in fnd.items()},
               hko_9day_page_datafile={k.isoformat(): v for k, v in web_fnd.items()},
               max_since_midnight=obs, current_temp=cur)

    # ---- 2. Markets + full order books ---------------------------------------------------
    snaps: dict[date, sources.MarketSnapshot] = {}
    quotes: dict[str, list[dict]] = {}
    for d in targets:
        raw = sources.fetch_market_raw(d)
        if not raw:
            status[f"market_{d}"] = "no Polymarket event found"
            continue
        snap = sources.parse_gamma_event(raw, d)
        snaps[d] = snap
        tokens = [t for mb in snap.buckets for t in (mb.yes_token, mb.no_token) if t]
        books = sources.fetch_books(tokens) if not snap.closed else {}
        missing = [t for t in tokens if t not in books]
        status[f"market_{d}"] = "ok" if (snap.closed or not missing) else f"{len(missing)} of {len(tokens)} order books missing"
        quotes[d.isoformat()] = quote_rows(snap, books) if books else []
        with gzip.open(folder / f"market_{d.isoformat()}.json.gz", "wt", encoding="utf-8") as f:
            json.dump({"gamma_event": raw, "books": books}, f)

    # ---- 3. Pages: numbers as shown + screenshots ---------------------------------------
    pages: list = []
    if not no_pages:
        do_hko = hko_pages_due(now, st.get("last_hko_pages"), force_pages)
        with Browser() as br:
            for d, snap in snaps.items():
                if snap.closed:
                    continue
                labels = [mb.bucket.label for mb in snap.buckets]
                pages.append((d, br.polymarket(folder / f"polymarket_{d.isoformat()}.jpg", snap.url, labels)))
            if do_hko:
                pages.append((None, br.hko_fnd(folder / "hko_9day.jpg", today)))
                y = today - timedelta(days=1)
                pages.append((None, br.hko_extract(folder / f"hko_daily_extract_{y:%Y-%m}.jpg", y.year, y.month)))
                st["last_hko_pages"] = now.timestamp()
    rec["pages"] = [{"target": d.isoformat() if d else None, **pr.__dict__} for d, pr in pages]

    # fallback 2: the 9-day page itself, if both data feeds were empty
    fnd_page = next((pr for _, pr in pages if pr.name == "hko_fnd_page" and pr.ok), None)
    for d in targets:
        if "hko_official" not in fc[d.isoformat()] and fnd_page and d.isoformat() in fnd_page.values:
            fc[d.isoformat()]["hko_official"] = fnd_page.values[d.isoformat()]
            fallback_used.append(f"{d}: HKO forecast read off the 9-day web page")
    rec["fallback_used"] = fallback_used

    # ---- 4. Cross-checks ------------------------------------------------------------------
    checks: list[tuple[str, str, float | None, float | None, bool]] = []
    for d, v in fnd.items():                                   # feed vs the page's data file
        if d in web_fnd:
            checks.append(("hko_9day_feed_vs_page_datafile", d.isoformat(), v, web_fnd[d],
                           abs(v - web_fnd[d]) <= config.XCHECK_TEMP_C))
    if fnd_page:                                               # feed vs the visible page
        for ds, v in fnd_page.values.items():
            fv = fnd.get(date.fromisoformat(ds))
            if fv is not None:
                checks.append(("hko_9day_feed_vs_page", ds, fv, v, abs(fv - v) <= config.XCHECK_TEMP_C))
    ext_page = next((pr for _, pr in pages if pr.name == "hko_extract_page" and pr.ok), None)
    if ext_page:
        y = today - timedelta(days=1)
        ext_feed = sources.fetch_daily_extract(y.year, y.month)
        clm = sources.fetch_clmmaxt(y.year, y.month)
        for ds, v in ext_page.values.items():
            dd = date.fromisoformat(ds)
            if dd in ext_feed:
                checks.append(("daily_extract_datafile_vs_page", ds, ext_feed[dd], v,
                               abs(ext_feed[dd] - v) <= config.XCHECK_TEMP_C))
            if dd in clm:
                checks.append(("clmmaxt_vs_daily_extract_page", ds, clm[dd], v, abs(clm[dd] - v) <= config.XCHECK_TEMP_C))
        rec["daily_extract_datafile"] = {k.isoformat(): v for k, v in ext_feed.items()}
    page_prices: dict[tuple[str, str], dict] = {}
    for d, pr in pages:
        if pr.name != "polymarket_page" or not pr.ok:
            continue
        for q in quotes.get(d.isoformat(), []):
            pv = pr.values.get(q["label"])
            if not pv:
                continue
            page_prices[(d.isoformat(), q["label"])] = pv
            for side, feed_v in (("buy_yes", q["yes_ask"]), ("buy_no", q["no_ask"])):
                if pv.get(side) is not None and feed_v is not None:
                    checks.append((f"polymarket_api_vs_page_{d.isoformat()}", f"{q['label']} {side}",
                                   feed_v, pv[side], abs(feed_v - pv[side]) <= config.XCHECK_PRICE_POINTS + 1e-9))
    # The documented daily report (RYES) vs the Daily Extract file, for yesterday, once both exist.
    yday = today - timedelta(days=1)
    ryes = sources.fetch_ryes_max(yday)
    if ryes is not None:
        ext = sources.fetch_daily_extract(yday.year, yday.month).get(yday)
        rec["ryes_yesterday"] = ryes
        if ext is not None:
            checks.append(("ryes_vs_daily_extract", yday.isoformat(), ryes, ext,
                           abs(ryes - ext) <= config.XCHECK_TEMP_C))
    rec["xchecks"] = [dict(check=c, item=i, feed=f, page=p, ok=o) for c, i, f, p, o in checks]
    mismatches = [c for c in checks if not c[4]]

    # ---- 5. Save --------------------------------------------------------------------------
    (folder / "snapshot.json").write_text(json.dumps({**rec, "status": status}, indent=1, default=str))
    src_failed = {k: v for k, v in status.items() if v != "ok"}
    pg_failed = {pr.name + (f"_{d}" if d else ""): pr.error for d, pr in pages if not pr.ok}
    with db() as con:
        for ds, row in fc.items():
            for src, v in row.items():
                con.execute("INSERT OR REPLACE INTO forecasts VALUES (?,?,?,?)", (run_ts, ds, src, v))
        if obs is not None:
            con.execute("INSERT OR REPLACE INTO observations VALUES (?,?,?)", (run_ts, today.isoformat(), obs))
        for ds, rows in quotes.items():
            for q in rows:
                pv = page_prices.get((ds, q["label"]), {})
                con.execute("INSERT OR REPLACE INTO market_quotes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (run_ts, ds, q["label"], q["yes_bid"], q["yes_ask"], q["yes_bid_size"],
                             q["yes_ask_size"], q["no_ask"], q["no_ask_size"], q["yes_cost_10"], q["no_cost_10"],
                             q["depth_yes_usd"], q["depth_no_usd"], q["fee_rate"], q["volume"],
                             pv.get("buy_yes"), pv.get("buy_no")))
        for c, i, f, p, o in checks:
            con.execute("INSERT OR REPLACE INTO xchecks VALUES (?,?,?,?,?,?)", (run_ts, c, i, f, p, int(o)))
        con.execute("INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?,?,?)",
                    (run_ts, folder.name, len(status) - len(src_failed), json.dumps(src_failed),
                     sum(pr.ok for _, pr in pages), json.dumps(pg_failed), len(mismatches), round(time.time() - t0, 1)))

    # ---- 6. Alerts ------------------------------------------------------------------------
    _alerts(now, status, pages, checks, mismatches, fallback_used, snaps, st)
    _save_state(st)
    alerts.heartbeat("snapshot", folder=folder.name, sources_failed=list(src_failed), pages_failed=list(pg_failed),
                     mismatches=len(mismatches), seconds=round(time.time() - t0, 1))

    # ---- 7. Console summary -----------------------------------------------------------------
    print(f"snapshot {folder.name}: {len(status) - len(src_failed)}/{len(status)} sources ok, "
          f"{sum(pr.ok for _, pr in pages)}/{len(pages)} pages ok, {len(checks)} cross-checks "
          f"({len(mismatches)} mismatched), {time.time() - t0:.0f} s")
    for ds, row in fc.items():
        print(f"  {ds}: " + ", ".join(f"{k} {v:.1f}" for k, v in row.items()))
    for k, v in {**src_failed, **pg_failed}.items():
        print(f"  PROBLEM {k}: {v}")
    for c in mismatches:
        print(f"  MISMATCH {c[0]} {c[1]}: feed {c[2]} vs page {c[3]}")
    return 0


def _alerts(now, status, pages, checks, mismatches, fallback_used, snaps, st) -> None:
    # Feeds
    for name, v in status.items():
        key = f"source:{name}"
        if name.startswith("market_") and name.endswith(now.date().isoformat()) and v != "ok":
            continue  # today's market may be closed/archived late in the day; tomorrow's matters
        if v == "ok":
            alerts.resolve(key, f"{name} is working again.")
        else:
            n = st.setdefault("fail_counts", {}).get(key, 0) + 1
            st["fail_counts"][key] = n
            if n >= 2:          # two rounds in a row (an hour) before bothering you
                alerts.alert(key, f"Data feed problem: {name}\n{v}\n(failed {n} rounds in a row; "
                                  "the bot keeps going with the other sources)")
            continue
        st.setdefault("fail_counts", {})[key] = 0
    # Pages
    for d, pr in pages:
        key = f"page:{pr.name}"
        if pr.ok:
            st.setdefault("page_fails", {})[key] = 0
            alerts.resolve(key, f"Page reader {pr.name} is working again.")
        else:
            n = st.setdefault("page_fails", {}).get(key, 0) + 1
            st["page_fails"][key] = n
            if n >= 2:
                alerts.alert(key, f"Could not read the page {pr.url}\n{pr.error}\n"
                                  f"({n} tries in a row; the site may be blocking the VPS or changed its layout. "
                                  "Numbers still come from the data feeds.)")
    # Cross-checks: HKO ones alert at once (they must match exactly); prices need 2 rounds in a row
    by_check: dict[str, list] = {}
    for c in mismatches:
        by_check.setdefault(c[0], []).append(c)
    seen = {c[0] for c in checks}
    for name in seen:
        key = f"xcheck:{name.rsplit('_20', 1)[0]}"
        bad = by_check.get(name, [])
        if not bad:
            st.setdefault("xcheck_fails", {})[key] = 0
            alerts.resolve(key, f"Cross-check {name} agrees again.")
            continue
        n = st.setdefault("xcheck_fails", {}).get(key, 0) + 1
        st["xcheck_fails"][key] = n
        is_price = name.startswith("polymarket")
        if is_price and (n < 2 or len(bad) < 2):
            continue
        lines = "\n".join(f"  {c[1]}: feed {c[2]} vs page {c[3]}" for c in bad[:6])
        alerts.alert(key, f"Cross-check mismatch: {name}\n{lines}\n"
                          + ("Prices on the page and in the API disagree two rounds in a row."
                             if is_price else "The data feed and the page show different numbers. "
                             "A source changed; the forecasts logged now may be wrong."))
    if fallback_used:
        alerts.alert("fallback:hko_forecast", "Fallback in use:\n" + "\n".join(fallback_used))
    # Tomorrow's market should exist by midday
    tm = now.date() + timedelta(days=1)
    if now.hour >= 12 and tm not in snaps:
        alerts.alert(f"market_missing:{tm}", f"No Polymarket market found yet for {tm} "
                                             f"({sources.event_slug(tm)}). It may not be listed, or the slug changed.")
    # Yesterday's official reading should be on the Daily Extract by 14:00
    y = now.date() - timedelta(days=1)
    if now.hour >= 14 and not st.get("extract_seen", {}).get(y.isoformat()):
        val = sources.fetch_daily_extract(y.year, y.month).get(y)
        if val is not None:
            st.setdefault("extract_seen", {})[y.isoformat()] = val
        else:
            alerts.alert(f"extract_late:{y}", f"HKO Daily Extract still has no reading for {y} at "
                                              f"{now:%H:%M} HKT. The market cannot settle until it does.")

