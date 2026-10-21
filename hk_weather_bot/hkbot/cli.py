"""Command line entry point.

    python -m hkbot run                 # predict + paper-trade tomorrow (all versions)
    python -m hkbot run --date today    # same for today, using live max-so-far
    python -m hkbot observe             # log today's max-since-midnight
    python -m hkbot settle              # score finished days
    python -m hkbot report              # compare versions
    python -m hkbot backtest --days 30  # walk-forward model test vs real HKO readings
    python -m hkbot calibrate --days 45 # tune uncertainty + source biases
    python -m hkbot snapshot            # one 30-min round: feeds, order books, pages, screenshots, cross-checks
    python -m hkbot sources             # how accurate each logged source has been (bias, error size, weight)
    python -m hkbot manual google 2026-10-22 31   # log a number you read by hand (e.g. Google Weather)
    python -m hkbot health              # alert if snapshots stopped or disk is low
    python -m hkbot backup              # consistent copy of data/ledger.sqlite
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import date, datetime, timedelta

from . import alerts, config, sources
from .backtest import calibrate, gather, save_calibration, summarise, walk_forward
from .buckets import winning_bucket
from .config import get_strategy
from .ledger import db
from .model import blend_with_market, bucket_probs, combine, load_calibration, load_priors
from .scoring import brier, log_loss, trade_pnl
from .strategy import find_trades
from .snapshot import cost_to_buy, no_levels, run_snapshot


def _parse_date(s: str | None) -> date:
    if s in (None, "tomorrow"):
        return sources.tomorrow_hkt()
    if s == "today":
        return sources.today_hkt()
    return date.fromisoformat(s)


def _versions(arg: str | None) -> list[str]:
    return [v.strip() for v in arg.split(",")] if arg else list(config.STRATEGIES)


def _pct(x):
    return "  -  " if x is None else f"{x*100:5.1f}"


# ----------------------------------------------------------------------------
def cmd_run(a) -> int:
    target = _parse_date(a.date)
    now = sources.now_hkt()
    run_ts = now.isoformat(timespec="seconds")
    print(f"\n=== Hong Kong max temperature — {target:%a %d %b %Y}  (run {now:%Y-%m-%d %H:%M} HKT) ===")

    market = sources.fetch_market(target)
    if market is None or not market.buckets:
        print("No Polymarket market found for that date (not listed yet, or network blocked).")
        return 1
    print(f"Market: {market.url}")
    if market.closed:
        print("Market is closed — nothing to trade. Use `settle`.")
        return 0

    # Step 2-3: gather every forecast source.
    raw: dict[str, float] = {}
    hko = sources.fetch_hko_9day()
    if target in hko:
        raw["hko_official"] = hko[target]
    for name, series in sources.fetch_model_forecasts(target, target).items():
        if target in series:
            raw[name] = series[target]

    obs, hours_left = None, None
    if target == now.date():
        obs = sources.fetch_hko_max_since_midnight()
        hours_left = max(0.0, 17.0 - (now.hour + now.minute / 60))
        print(f"Live HKO max since midnight: {obs if obs is not None else 'unavailable'} "
              f"(~{hours_left:.1f} h of heating left)")

    print("Forecasts (raw):", ", ".join(f"{k} {v:.1f}" for k, v in raw.items()) or "none")
    if not raw:
        print("No forecasts available — aborting.")
        return 1

    cal = load_calibration()
    priors = load_priors()
    market_mid = [mb.mid for mb in market.buckets]
    buckets = [mb.bucket for mb in market.buckets]

    by_label = {mb.bucket.label: mb for mb in market.buckets}
    book_cache: dict = {}
    summary: list[str] = []

    def real_cost(t):
        """Walk the live order book for this trade's $ stake and add the taker fee."""
        mb = by_label[t.label]
        if not book_cache:
            book_cache.update(sources.fetch_books(
                [x for b in market.buckets for x in (b.yes_token, b.no_token) if x]))
        yb, nb = book_cache.get(mb.yes_token or ""), book_cache.get(mb.no_token or "")
        levels = (yb or {}).get("asks", []) if t.side == "YES" else no_levels(yb, nb)
        c = cost_to_buy(levels, t.stake, mb.fee_rate)
        if c["all_in"] is None:                      # no book: top price + fee
            fee = sources.taker_fee_per_share(t.price, mb.fee_rate)
            c = {"avg": t.price, "fee": fee, "all_in": t.price + fee, "shares": t.stake / (t.price + fee)}
        return c

    with db() as con:
        for k, v in raw.items():
            con.execute("INSERT OR REPLACE INTO forecasts VALUES (?,?,?,?)", (run_ts, target.isoformat(), k, v))
        if obs is not None:
            con.execute("INSERT OR REPLACE INTO observations VALUES (?,?,?)", (run_ts, target.isoformat(), obs))

        for vname in _versions(a.versions):
            overrides = {}
            if "sigma_scale" in cal and vname != "v1_naive":
                overrides["sigma_scale"] = cal["sigma_scale"]
            strat = get_strategy(vname, **overrides)
            fc = combine(raw, strat, priors)
            if fc is None:
                print(f"\n[{vname}] no usable sources")
                continue
            model_p = bucket_probs(buckets, fc, strat, obs, hours_left)
            final_p = blend_with_market(model_p, market_mid, strat.market_blend)
            ideas = find_trades(market.buckets, final_p, strat)

            print(f"\n[{vname}] {strat.description}")
            print(f"  best guess {fc.mean:.2f} C, uncertainty ±{fc.sigma:.2f} C")
            for n in fc.notes:
                print("   ", n)
            print(f"  {'bucket':<16}{'bid':>6}{'ask':>6}{'mkt%':>7}{'model%':>8}{'final%':>8}")
            for mb, mp, fp in zip(market.buckets, model_p, final_p):
                if max(mp, fp, mb.mid or 0) < 0.01:
                    continue
                print(f"  {mb.bucket.label:<16}{_pct(mb.yes_bid):>6}{_pct(mb.yes_ask):>6}"
                      f"{_pct(mb.mid):>7}{mp*100:8.1f}{fp*100:8.1f}")

            for mb, mp, fp in zip(market.buckets, model_p, final_p):
                con.execute(
                    "INSERT OR REPLACE INTO predictions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (run_ts, target.isoformat(), vname, mb.bucket.label, mb.bucket.lo, mb.bucket.hi,
                     mp, fp, mb.mid, mb.yes_bid, mb.yes_ask, fc.mean, fc.sigma))

            already = con.execute("SELECT COUNT(*) FROM trades WHERE target_date=? AND version=?",
                                  (target.isoformat(), vname)).fetchone()[0]
            if not ideas:
                print("  -> no trade (no edge big enough after the spread)")
                summary.append(f"{vname}: no trade (best guess {fc.mean:.1f}±{fc.sigma:.1f})")
            elif already and not a.retrade:
                print(f"  -> {len(ideas)} idea(s), but {already} paper trade(s) already placed for this day (use --retrade)")
                for t in ideas:
                    print("     idea:", t.describe())
            else:
                for t in ideas:
                    c = real_cost(t)
                    print("  -> PAPER", t.describe(),
                          f"| real cost {c['all_in']*100:.1f}c/share incl. fee {c['fee']*100:.2f}c")
                    con.execute(
                        "INSERT INTO trades (run_ts,target_date,version,label,side,price,prob,edge,shares,stake,"
                        "exec_price,fee_per_share,exec_shares) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (run_ts, target.isoformat(), vname, t.label, t.side, t.price, t.prob, t.edge, t.shares,
                         t.stake, c["all_in"], c["fee"], c["shares"]))
                    summary.append(f"{vname}: PAPER {t.side} {t.label} @ {t.price*100:.1f}c "
                                   f"(real {c['all_in']*100:.1f}c with fee), ours {t.prob*100:.0f}%, "
                                   f"edge {t.edge*100:+.0f} pts")
    print()
    if a.notify:
        head = (f"Run for {target:%a %d %b} at {now:%H:%M} HKT (paper only)\n"
                f"Forecasts: " + ", ".join(f"{k} {v:.1f}" for k, v in raw.items()))
        if obs is not None:
            head += f"\nHKO max so far today: {obs:.1f}"
        alerts.alert(f"run:{target}:{now:%H%M}", head + "\n" + "\n".join(summary), cls="report")
    return 0


# ----------------------------------------------------------------------------
def cmd_observe(a) -> int:
    now = sources.now_hkt()
    v = sources.fetch_hko_max_since_midnight()
    if v is None:
        print("Could not read HKO max-since-midnight.")
        return 1
    with db() as con:
        con.execute("INSERT OR REPLACE INTO observations VALUES (?,?,?)",
                    (now.isoformat(timespec="seconds"), now.date().isoformat(), v))
    print(f"{now:%Y-%m-%d %H:%M} HKT  HKO max since midnight: {v:.1f} C")
    return 0


# ----------------------------------------------------------------------------
def cmd_settle(a) -> int:
    today = sources.today_hkt()
    with db() as con:
        dates = [r[0] for r in con.execute(
            "SELECT DISTINCT target_date FROM predictions WHERE target_date < ? "
            "AND target_date NOT IN (SELECT target_date FROM scores)", (today.isoformat(),))]
        if not dates:
            print("Nothing to settle.")
            return 0
        for ds in sorted(dates):
            d = date.fromisoformat(ds)
            market = sources.fetch_market(d)
            win = market.winner() if market else None
            # Official reading: HKO's daily report (RYES, 01:30 HKT next day) first, Daily Extract /
            # CLMMAXT as the second witness. If the two ever disagree, say so loudly.
            ryes = sources.fetch_ryes_max(d)
            extract = sources.fetch_hko_daily_max(d.year, d.month).get(d)
            reading = ryes if ryes is not None else extract
            if ryes is not None and extract is not None and abs(ryes - extract) > 0.05:
                alerts.alert(f"reading_disagree:{ds}", f"{ds}: HKO daily report says {ryes:.1f} but the "
                             f"Daily Extract says {extract:.1f}. The market resolves on the Daily Extract.")
                reading = extract
            if win is None:
                print(f"{ds}: not resolved yet on Polymarket"
                      + (f" (HKO reading {reading:.1f})" if reading is not None else ""))
                continue
            consistent = None
            if reading is not None:
                wb = winning_bucket([mb.bucket for mb in market.buckets], reading, cutoff=True)
                consistent = int(wb is not None and wb.label == win.bucket.label)
            con.execute("INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?)",
                        (ds, win.bucket.label, reading, consistent, "polymarket",
                         datetime.now().isoformat(timespec="seconds")))
            msg = f"{ds}: winner {win.bucket.label}"
            if reading is not None:
                msg += f" | HKO {reading:.1f} -> cut-off rule {'CONFIRMED' if consistent else 'MISMATCH — check rules!'}"
            print(msg)
            tg = [msg]
            if reading is not None and not consistent:
                alerts.alert(f"cutoff_mismatch:{ds}",
                             f"RULE CHECK FAILED for {ds}: HKO reading {reading:.1f} but Polymarket settled "
                             f"{win.bucket.label}. The cut-off rule (31.9 -> 31) did not hold. Check the market rules "
                             "before trusting any probabilities.")
            elif reading is None:
                alerts.alert(f"settle_noreading:{ds}",
                             f"{ds} settled on Polymarket ({win.bucket.label}) but no HKO reading was found to "
                             "check the cut-off rule.")

            for vname in [r[0] for r in con.execute(
                    "SELECT DISTINCT version FROM predictions WHERE target_date=?", (ds,))]:
                # Score the FIRST prediction of the day for this version (the 'locked' one).
                first = con.execute("SELECT MIN(run_ts) FROM predictions WHERE target_date=? AND version=?",
                                    (ds, vname)).fetchone()[0]
                rows = con.execute("SELECT * FROM predictions WHERE target_date=? AND version=? AND run_ts=? "
                                   "ORDER BY lo", (ds, vname, first)).fetchall()
                labels = [r["label"] for r in rows]
                if win.bucket.label not in labels:
                    continue
                wi = labels.index(win.bucket.label)
                bot = [r["final_p"] for r in rows]
                mk = [r["market_p"] or 0.0 for r in rows]
                tot = sum(mk) or 1.0
                mk = [m / tot for m in mk]
                pnl = 0.0
                for t in con.execute("SELECT * FROM trades WHERE target_date=? AND version=? AND settled=0",
                                     (ds, vname)).fetchall():
                    won = t["label"] == win.bucket.label
                    quoted = trade_pnl(t["side"], t["price"], t["shares"], won)
                    # Honest P&L: shares really bought for the stake at book-walk price + taker fee.
                    if t["exec_price"] is not None:
                        p = trade_pnl(t["side"], t["exec_price"], t["exec_shares"], won)
                    else:
                        p = quoted
                    pnl += p
                    con.execute("UPDATE trades SET pnl=?, pnl_quoted=?, settled=1 WHERE id=?", (p, quoted, t["id"]))
                con.execute("INSERT OR REPLACE INTO scores VALUES (?,?,?,?,?,?,?,?,?,?)",
                            (ds, vname, first, brier(bot, wi), brier(mk, wi), log_loss(bot, wi),
                             log_loss(mk, wi), bot[wi], mk[wi], pnl))
                verdict = "beat market" if brier(bot, wi) < brier(mk, wi) else "market better"
                line = (f"   [{vname}] Brier bot {brier(bot, wi):.3f} vs market {brier(mk, wi):.3f} "
                        f"({verdict}); P(winner) {bot[wi]*100:.0f}% vs {mk[wi]*100:.0f}%; P&L ${pnl:+.2f}")
                print(line)
                tg.append(line.strip())
            if a.notify:
                alerts.alert(f"settle:{ds}", "Settled (paper)\n" + "\n".join(tg), cls="report")
    return 0


# ----------------------------------------------------------------------------
def cmd_report(a) -> int:
    with db() as con:
        rows = con.execute(
            "SELECT version, COUNT(*) n, AVG(brier_bot) bb, AVG(brier_market) bm, "
            "AVG(p_winner_bot) pw, SUM(brier_bot < brier_market) wins, SUM(pnl) pnl "
            "FROM scores GROUP BY version ORDER BY bb").fetchall()
        if not rows:
            print("No settled days yet. Run `run` daily and `settle` after markets resolve.")
            return 0
        print(f"\n{'version':<14}{'days':>5}{'Brier bot':>11}{'Brier mkt':>11}{'beat mkt':>10}"
              f"{'P(win)':>8}{'paper P&L':>11}")
        for r in rows:
            print(f"{r['version']:<14}{r['n']:>5}{r['bb']:>11.3f}{r['bm']:>11.3f}"
                  f"{r['wins']:>6}/{r['n']:<3}{r['pw']*100:>7.0f}%{r['pnl']:>+11.2f}")
        trades = con.execute("SELECT version, COUNT(*) n, SUM(pnl>0) w, SUM(stake) st FROM trades "
                             "WHERE settled=1 GROUP BY version").fetchall()
        for t in trades:
            print(f"  {t['version']}: {t['n']} trades, {t['w']} winners, ${t['st']:.0f} staked")
        print("\nRule of thumb: only consider real money once a version beats the market's Brier score\n"
              "over 30+ settled days AND shows positive paper P&L.\n")
    return 0


# ----------------------------------------------------------------------------
def _bt_setup(a):
    with db() as con:
        start, end, actual, fcsts = gather(con, a.days)
    test_days = [end - timedelta(days=i) for i in range(a.days)][::-1]
    print(f"Actual HKO readings: {len(actual)} days | sources: "
          + ", ".join(f"{k} ({len(v)}d)" for k, v in fcsts.items()))
    if not actual or not fcsts:
        print("Missing data (network blocked or API changed). Check config endpoints.")
    return end, actual, fcsts, test_days


def cmd_backtest(a) -> int:
    end, actual, fcsts, test_days = _bt_setup(a)
    cal = load_calibration()
    print(f"\nWalk-forward backtest, {a.days} days to {end} (model only, no market blend):")
    print(f"{'version':<14}{'days':>5}{'Brier':>8}{'logloss':>9}{'P(win)':>8}{'top hit':>9}")
    for vname in _versions(a.versions):
        ov = {"sigma_scale": cal["sigma_scale"]} if "sigma_scale" in cal and vname != "v1_naive" else {}
        res = walk_forward(get_strategy(vname, **ov), actual, fcsts, test_days)
        s = summarise(res)
        if not s["days"]:
            print(f"{vname:<14}  no days scored")
            continue
        print(f"{vname:<14}{s['days']:>5}{s['avg_brier']:>8.3f}{s['avg_logloss']:>9.3f}"
              f"{s['avg_p_winner']*100:>7.0f}%{s['top_pick_hit_rate']*100:>8.0f}%")
        if a.verbose:
            for r in res:
                print(f"   {r.day}  actual {r.actual:.1f} -> {r.winner:<8} guess {r.mean:.1f}±{r.sigma:.1f} "
                      f"top {r.top_pick:<8} P(win) {r.p_winner*100:4.0f}%  Brier {r.brier:.3f}")
    return 0


def cmd_calibrate(a) -> int:
    end, actual, fcsts, test_days = _bt_setup(a)
    strat = get_strategy(config.DEFAULT_STRATEGY)
    best, table = calibrate(strat, actual, fcsts, test_days)
    print(f"\nUncertainty-scale search ({config.DEFAULT_STRATEGY}), lower Brier is better:")
    for s, summ in table:
        mark = "  <- best" if s == best else ""
        if summ.get("days"):
            print(f"  scale {s:>4}: Brier {summ['avg_brier']:.3f}  P(win) {summ['avg_p_winner']*100:.0f}%{mark}")
    cal = save_calibration(actual, fcsts, end, best)
    print("\nSource error stats (forecast - actual, deg C):")
    for k, v in cal["sources"].items():
        if v.get("n"):
            print(f"  {k:<13} n={v['n']:>3}  bias {v['bias']:+.2f}  sigma {v['sigma']:.2f}  MAE {v['mae']:.2f}")
    print(f"\nSaved to {config.CALIBRATION_PATH}")
    return 0


# ----------------------------------------------------------------------------
def cmd_snapshot(a) -> int:
    return run_snapshot(force_pages=a.force_pages, no_pages=a.no_pages)


def cmd_manual(a) -> int:
    """Log a number read by hand, e.g. Google Weather's max for a day. It is scored like any source."""
    d = _parse_date(a.date)
    src = a.source if a.source.endswith("_manual") else f"{a.source}_manual"
    run_ts = sources.now_hkt().isoformat(timespec="seconds")
    with db() as con:
        con.execute("INSERT OR REPLACE INTO forecasts VALUES (?,?,?,?)", (run_ts, d.isoformat(), src, a.value))
    print(f"logged {src} = {a.value} for {d} at {run_ts}")
    return 0


def cmd_sources(a) -> int:
    """Accuracy of every logged source vs the real HKO reading, by how far ahead it was logged."""
    with db() as con:
        rows = con.execute("SELECT f.run_ts, f.target_date, f.source, f.value, r.hko_reading "
                           "FROM forecasts f JOIN results r ON r.target_date = f.target_date "
                           "WHERE r.hko_reading IS NOT NULL").fetchall()
    if not rows:
        print("No settled days with logged forecasts yet. Keep the snapshots running.")
        return 0
    from collections import defaultdict
    from .model import source_stats, priors_from_stats, trust
    groups = defaultdict(list)
    for r in rows:
        run_day = date.fromisoformat(r["run_ts"][:10])
        lead = (date.fromisoformat(r["target_date"]) - run_day).days
        tag = "day before" if lead >= 1 else "same day"
        groups[(r["source"], tag)].append(r["value"] - r["hko_reading"])
    print(f"\n{'source':<16}{'logged':<12}{'n':>6}{'bias':>8}{'error':>8}{'MAE':>7}")
    for (src, tag), errs in sorted(groups.items()):
        st = source_stats(errs)
        print(f"{src:<16}{tag:<12}{st['n']:>6}{st['bias']:>+8.2f}{st['sigma']:>8.2f}{st['mae']:>7.2f}")
    print("\nbias = average (forecast - actual); positive runs warm. error = typical miss (std).")
    print("Each snapshot logs every source; many rows per day are expected (one per 30-min round).")
    cal = load_calibration().get("sources", {})
    if cal:
        pri = priors_from_stats(cal)
        v2, v3 = get_strategy("v2_audited"), get_strategy("v3_auto_weights")
        print("\nWeights now (from the last `calibrate`): relative trust = multiplier / error^2")
        for s, p in pri.items():
            print(f"  {s:<14} n={p.n:<4} error {p.sigma:.2f}  v2 trust {trust(p, v2)/p.sigma**2:6.2f}  "
                  f"v3 trust {trust(p, v3)/p.sigma**2:6.2f}")
    return 0


def cmd_health(a) -> int:
    import shutil
    hb = alerts.read_heartbeat("snapshot")
    age = (time.time() - hb["ts"]) / 60 if hb.get("ts") else None
    if age is None or age > config.SNAPSHOT_STALE_MIN:
        alerts.alert("health:snapshot_stale", f"No snapshot for {'ever' if age is None else f'{age:.0f} min'} "
                     "(expected every 30 min). Check the hkbot-snapshot timer on the VPS.")
    else:
        alerts.resolve("health:snapshot_stale", f"Snapshots are running again (last {age:.0f} min ago).")
    free = shutil.disk_usage(config.DATA_DIR if config.DATA_DIR.exists() else ".").free / 1e9
    if free < config.DISK_MIN_FREE_GB:
        alerts.alert("health:disk", f"Disk almost full on the VPS: {free:.1f} GB free. Snapshots will start failing.")
    print(f"last snapshot {age if age is None else round(age)} min ago, disk free {free:.1f} GB")
    alerts.heartbeat("health", snapshot_age_min=age, disk_free_gb=round(free, 1))
    return 0


def cmd_backup(a) -> int:
    from .ledger import backup
    dest = config.BACKUP_DIR / f"ledger-{sources.now_hkt():%Y%m%d-%H%M}.sqlite"
    backup(dest)
    print(f"backup written: {dest} ({dest.stat().st_size/1e6:.2f} MB)")
    return 0


def cmd_notify_test(a) -> int:
    ok, detail = alerts.send_telegram("🌡️ HK weather bot\nTest message: alerts reach this thread.")
    print("telegram:", ok, detail)
    return 0 if ok else 1


# ----------------------------------------------------------------------------
def cmd_history(a) -> int:
    from . import history as H

    end = date.fromisoformat(a.end) if a.end else sources.today_hkt() - timedelta(days=2)
    start = date.fromisoformat(a.start)
    train_end = date.fromisoformat(a.train_end) if a.train_end else start + (end - start) * 2 / 3
    if not (start < train_end < end):
        print("Need start < train-end < end.")
        return 1
    when = {"evening": "18:00 HKT the day before", "morning": "09:00 HKT on the day",
            "noon": "12:00 HKT on the day", "afternoon": "14:00 HKT on the day"}[a.decision]
    print(f"\n=== No-peek history backtest: {start} .. {end} | decision: {a.decision} ({when}) | "
          f"HKO forecast {'on' if not a.no_hko else 'OFF'}, live max-so-far "
          f"{'on' if not a.no_obs and a.decision != 'evening' else 'OFF'}, taker fee {a.fee:.2f} ===")
    H.FEE_RATE = a.fee
    markets, fcsts, actuals, obs = H.load_range(start, end, a.decision, a.refresh,
                                                use_hko=not a.no_hko, use_obs=not a.no_obs)
    snaps, skipped = H.build_snapshots(markets, fcsts, actuals, obs, a.decision)
    if len(snaps) < 20:
        print(f"Only {len(snaps)} usable days — need at least 20. Skipped examples:")
        for s in skipped[:10]:
            print("   ", s)
        return 1
    rep = H.run_history(snaps, skipped, train_end, a.decision, a.spread, get_strategy(config.DEFAULT_STRATEGY))

    print(f"\nTraining: {len(rep.train_days)} days (to {train_end}) | "
          f"Test: {len(rep.test_days)} days (after {train_end}, never seen by the tuner)")
    t = rep.tuned
    print(f"Tuned on training only -> uncertainty scale {t.sigma_scale}, market blend {t.market_blend} "
          f"(1 = ignore market), min edge {t.min_edge*100:.0f} pts. Spread penalty {a.spread*100:.0f}c "
          f"+ taker fee {a.fee:.2f}*p*(1-p).")
    if t.market_blend == 0.0:
        print("  NOTE: the tuner chose to copy the market — the model added nothing on training data.")

    hdr = f"{'':<16}{'set':<6}{'days':>5}{'Brier bot':>10}{'Brier mkt':>10}{'beat':>8}{'P(win) bot':>11}" \
          f"{'mkt':>6}{'trades':>8}{'won':>5}{'P&L $':>9}"
    print("\n" + hdr)
    for name, r in rep.rows.items():
        for part in ("train", "test"):
            s = r[part]
            if not s.get("days"):
                continue
            print(f"{name if part == 'train' else '':<16}{part:<6}{s['days']:>5}{s['brier_bot']:>10.3f}"
                  f"{s['brier_mkt']:>10.3f}{s['beat_mkt_days']:>5}/{s['days']:<3}{s['p_win_bot']*100:>9.0f}%"
                  f"{s['p_win_mkt']*100:>5.0f}%{s['trades']:>8}{s['winning_trades']:>5}{s['pnl']:>+9.2f}")

    test = rep.rows["tuned (frozen)"]["test"]
    print("\nVERDICT (test period only):")
    edge_prob = test["brier_bot"] < test["brier_mkt"]
    print(f"  Probabilities: {'BETTER' if edge_prob else 'WORSE'} than the market "
          f"({test['brier_bot']:.3f} vs {test['brier_mkt']:.3f}, lower is better)")
    print(f"  Paper trading: {test['trades']} trades, P&L ${test['pnl']:+.2f} at ${t.stake_usd:.0f}/trade")
    if test["days"] < 30:
        print("  Caution: fewer than 30 test days — treat as indicative only.")

    if rep.rule_mismatches:
        print("\nWARNING — days where Polymarket's winner does NOT match the cut-off rule:")
        for m in rep.rule_mismatches:
            print("   ", m)
    else:
        print("\nCut-off rule matched Polymarket's settled winner on every day checked.")
    if skipped:
        print(f"Skipped {len(skipped)} day(s) (missing data), e.g. {skipped[0]}")

    tag = ("" if not a.no_hko else "_nohko") + ("" if not a.no_obs else "_noobs")
    out = config.HISTORY_DIR / f"backtest from {sources.today_hkt()} to {start} - {a.decision}{tag}.csv"
    H.write_csv(out, rep.test_scores["tuned (frozen)"])
    print(f"\nPer-day test results: {out}\n")
    return 0


# ----------------------------------------------------------------------------
def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="hkbot", description="Hong Kong temperature paper-trading bot")
    p.add_argument("-v", "--debug", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="predict and paper-trade a day")
    r.add_argument("--date", default="tomorrow", help="tomorrow | today | YYYY-MM-DD")
    r.add_argument("--versions", help="comma list, default all")
    r.add_argument("--retrade", action="store_true", help="allow more paper trades for a day already traded")
    r.add_argument("--notify", action="store_true", help="send the result to Telegram")
    r.set_defaults(fn=cmd_run)

    sub.add_parser("observe", help="log HKO max since midnight").set_defaults(fn=cmd_observe)
    se = sub.add_parser("settle", help="score resolved days")
    se.add_argument("--notify", action="store_true", help="send the result to Telegram")
    se.set_defaults(fn=cmd_settle)

    sn = sub.add_parser("snapshot", help="one round: feeds, order books, pages, screenshots, cross-checks")
    sn.add_argument("--force-pages", action="store_true", help="also read + screenshot the HKO pages now")
    sn.add_argument("--no-pages", action="store_true", help="feeds and order books only (no browser)")
    sn.set_defaults(fn=cmd_snapshot)
    mn = sub.add_parser("manual", help="log a hand-read forecast, e.g. Google Weather")
    mn.add_argument("source", help="e.g. google (stored as google_manual)")
    mn.add_argument("date", help="target date: tomorrow | today | YYYY-MM-DD")
    mn.add_argument("value", type=float, help="forecast max in deg C")
    mn.set_defaults(fn=cmd_manual)
    sub.add_parser("sources", help="accuracy and weight of each logged source").set_defaults(fn=cmd_sources)
    sub.add_parser("health", help="alert if snapshots stopped or disk is low").set_defaults(fn=cmd_health)
    sub.add_parser("backup", help="copy the ledger to data/backups/").set_defaults(fn=cmd_backup)
    sub.add_parser("notify-test", help="send one Telegram test message").set_defaults(fn=cmd_notify_test)
    sub.add_parser("report", help="compare strategy versions").set_defaults(fn=cmd_report)

    b = sub.add_parser("backtest", help="walk-forward test against real HKO readings")
    b.add_argument("--days", type=int, default=30)
    b.add_argument("--versions")
    b.add_argument("--verbose", action="store_true")
    b.set_defaults(fn=cmd_backtest)

    h = sub.add_parser("history", help="no-peek backtest on real past Polymarket markets")
    h.add_argument("--start", default="2026-04-15", help="first market day (YYYY-MM-DD)")
    h.add_argument("--end", help="last market day (default: 2 days ago)")
    h.add_argument("--train-end", help="last training day (default: first 2/3 of the range)")
    h.add_argument("--decision", choices=["evening", "morning", "noon", "afternoon"], default="evening")
    h.add_argument("--no-hko", action="store_true", help="leave out HKO's own (archived) forecast")
    h.add_argument("--no-obs", action="store_true", help="leave out the live max-so-far reading")
    h.add_argument("--fee", type=float, default=0.05, help="taker fee rate (weather markets: 0.05)")
    h.add_argument("--spread", type=float, default=0.02, help="extra cost per share vs historical price")
    h.add_argument("--refresh", action="store_true", help="ignore the download cache")
    h.set_defaults(fn=cmd_history)

    c = sub.add_parser("calibrate", help="fit source biases and uncertainty")
    c.add_argument("--days", type=int, default=45)
    c.set_defaults(fn=cmd_calibrate)

    a = p.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if a.debug else logging.WARNING, format="%(levelname)s %(message)s")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
