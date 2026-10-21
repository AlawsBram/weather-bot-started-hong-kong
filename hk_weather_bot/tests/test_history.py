"""The history backtest must never peek. These tests try to catch it cheating."""
import json
import math
import random
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from hkbot import config
from hkbot import history as H
from hkbot.buckets import parse_label
from hkbot.config import get_strategy
from hkbot.sources import HKT

LADDER = ["26°C or below"] + [f"{k}°C" for k in range(27, 36)] + ["36°C or higher"]
BUCKETS = [parse_label(x) for x in LADDER]


def test_decision_times():
    d = date(2026, 10, 19)
    assert H.decision_dt(d, "evening") == datetime(2026, 10, 18, 18, 0, tzinfo=HKT)
    assert H.decision_dt(d, "morning") == datetime(2026, 10, 19, 9, 0, tzinfo=HKT)


def test_price_at_never_uses_future_points():
    ts = 1_000_000
    hist = [{"t": ts - 7200, "p": 0.30}, {"t": ts - 60, "p": 0.40}, {"t": ts + 60, "p": 0.99}]
    assert H.price_at(hist, ts) == 0.40
    assert H.price_at([{"t": ts + 1, "p": 0.99}], ts) is None
    assert H.price_at([{"t": ts - 40 * 3600, "p": 0.5}], ts) is None      # stale


def test_previous_runs_parsing():
    times = [f"2026-10-19T{h:02d}:00" for h in range(24)]
    temps = [27 + (3 if 12 <= h <= 15 else 0) + h / 100 for h in range(24)]
    for key in ("temperature_2m_previous_day1", "temperature_2m_previous_day1_ecmwf_ifs025"):
        out = H.parse_previous_runs({"hourly": {"time": times, key: temps}}, 1)
        assert out == {date(2026, 10, 19): pytest.approx(30.15)}
    assert H.parse_previous_runs({"hourly": {"time": times[:10], "temperature_2m_previous_day1": temps[:10]}}, 1) == {}
    assert H.parse_previous_runs({"hourly": {"time": times, "temperature_2m_previous_day2": temps}}, 1) == {}


def test_bias_ignores_target_day_and_day_before():
    d = date(2026, 10, 19)
    actuals = {d - timedelta(days=i): 30.0 for i in range(0, 40)}
    fc = {d - timedelta(days=i): 29.0 for i in range(0, 40)}        # 1 deg cool normally
    fc[d] = 0.0                                                      # absurd errors on D and D-1
    fc[d - timedelta(days=1)] = 0.0
    st = H.lagged_stats(d, actuals, {"ecmwf": fc})["ecmwf"]
    assert st["bias"] == pytest.approx(-1.0)


def _snap(day, forecasts, market_raw, actual, pm_winner=None):
    tot = sum(p for p in market_raw if p)
    return H.DaySnapshot(day, BUCKETS, market_raw, [(p or 0) / tot for p in market_raw], forecasts,
                         dict(config.DEFAULT_PRIORS), _actual=actual, _pm_winner=pm_winner)


def test_prediction_is_identical_whatever_the_answer():
    mk = [0.001, 0.001, 0.002, 0.03, 0.15, 0.53, 0.29, 0.045, 0.001, 0.001, 0.001]
    a = _snap(date(2026, 10, 19), {"ecmwf": 30.0, "gfs": 28.0}, mk, 31.4)
    b = replace(a, _actual=34.9)
    s = get_strategy("v2_audited")
    assert H.predict(a, s)[0] == H.predict(b, s)[0]


def _synthetic(n=120, seed=3, informative=True):
    """Model forecasts carry real information; the market is a noisier, lagging copy."""
    rnd = random.Random(seed)
    start = date(2026, 5, 1)
    snaps = []
    for i in range(n):
        d = start + timedelta(days=i)
        truth = 30 + 2 * math.sin(i / 10) + rnd.gauss(0, 0.8)
        ec = truth - 0.8 + rnd.gauss(0, 0.6) if informative else 30 + rnd.gauss(0, 2)
        gf = truth - 2.0 + rnd.gauss(0, 1.2) if informative else 30 + rnd.gauss(0, 2)
        mkt_mean = truth + rnd.gauss(0, 1.3)
        raw = []
        for b in BUCKETS:
            lo, hi = max(b.lo - 0.05, -50), min(b.hi - 0.05, 60)
            from statistics import NormalDist
            nd = NormalDist(mkt_mean, 1.0)
            raw.append(max(0.001, nd.cdf(hi) - nd.cdf(lo)))
        snaps.append(_snap(d, {"ecmwf": ec, "gfs": gf}, raw, round(truth, 1)))
    return snaps


def test_tuner_never_sees_test_days(monkeypatch):
    snaps = _synthetic()
    train_end = snaps[79].day
    seen = set()
    real_eval = H.evaluate

    def spy(days, strat, spread):
        seen.update(s.day for s in days)
        return real_eval(days, strat, spread)

    monkeypatch.setattr(H, "evaluate", spy)
    H.tune([s for s in snaps if s.day <= train_end], get_strategy("v2_audited"), 0.02)
    assert seen and max(seen) <= train_end


def test_run_history_finds_real_edge_and_reports():
    snaps = _synthetic()
    rep = H.run_history(snaps, [], snaps[79].day, "evening", 0.02, get_strategy("v2_audited"))
    te = rep.rows["tuned (frozen)"]["test"]
    assert te["days"] == 40
    assert te["brier_bot"] < te["brier_mkt"]           # informative model beats noisy market
    assert rep.tuned.market_blend > 0                   # so the tuner kept the model


def test_useless_model_gets_ignored():
    snaps = _synthetic(informative=False)
    rep = H.run_history(snaps, [], snaps[79].day, "evening", 0.02, get_strategy("v2_audited"))
    assert rep.tuned.market_blend <= 0.25               # tuner learns to lean on the market


# --------------------------------------------------------------------------------------
# Full command, offline: fake API responses, real parsing + CLI
# --------------------------------------------------------------------------------------

def _fake_world(start, end, seed=7):
    rnd = random.Random(seed)
    truth = {}
    d = start - timedelta(days=40)
    i = 0
    while d <= end:
        truth[d] = round(30 + 2 * math.sin(i / 9) + rnd.gauss(0, 0.7), 1)
        d += timedelta(days=1)
        i += 1
    return truth


def test_history_command_offline(monkeypatch, tmp_path, capsys):
    from hkbot import cli
    start, end = date(2026, 6, 1), date(2026, 8, 31)
    truth = _fake_world(start, end)
    rnd = random.Random(1)
    monkeypatch.setattr(config, "HISTORY_DIR", tmp_path / "history")

    def fake_get(self, url, params):
        if url == config.HKO_OPENDATA_CLIMATE:
            rows = [[str(d.year), str(d.month), str(d.day), f"{v:.1f}", "C"]
                    for d, v in truth.items() if d.year == params["year"] and d.month == params["month"]]
            return {"data": {"data": rows}}
        if url == config.OPEN_METEO_PREVIOUS_RUNS:
            s, e = date.fromisoformat(params["start_date"]), date.fromisoformat(params["end_date"])
            off = {"ecmwf_ifs025": -0.8, "gfs_seamless": -2.0, "icon_seamless": -0.5}[params["models"]]
            times, temps, d = [], [], s
            while d <= e:
                peak = truth.get(d, 30.0) + off + rnd.gauss(0, 0.7)
                for h in range(24):
                    times.append(f"{d}T{h:02d}:00")
                    temps.append(peak - (0 if h == 14 else 3))
                d += timedelta(days=1)
            return {"data": {"hourly": {"time": times, params["hourly"]: temps}}}
        if url == config.GAMMA_EVENTS_URL:
            slug = params["slug"]
            mdate = datetime.strptime(slug.split("-on-")[1], "%B-%d-%Y").date()
            if mdate not in truth:
                return {"data": []}
            mk = [{"groupItemTitle": lab, "clobTokenIds": json.dumps([f"{slug}|{lab}", "no"]),
                   "closed": True, "outcomePrices": json.dumps(["0", "1"])} for lab in LADDER]
            win = next(i for i, b in enumerate(BUCKETS) if b.contains(truth[mdate]))
            mk[win]["outcomePrices"] = json.dumps(["1", "0"])
            return {"data": [{"slug": slug, "closed": True, "markets": mk}]}
        if url == config.CLOB_PRICES_HISTORY:
            if "startTs" not in params:
                return {"data": {"history": []}}
            slug, lab = params["market"].split("|")
            mdate = datetime.strptime(slug.split("-on-")[1], "%B-%d-%Y").date()
            from statistics import NormalDist
            nd = NormalDist(truth[mdate] + random.Random(slug).gauss(0, 1.2), 1.1)
            b = parse_label(lab)
            p = max(0.001, nd.cdf(min(b.hi - 0.05, 60)) - nd.cdf(max(b.lo - 0.05, -50)))
            ts = params["endTs"]
            return {"data": {"history": [{"t": ts - 3600, "p": round(p, 3)},
                                         {"t": ts + 3600, "p": 1.0 if b.contains(truth[mdate]) else 0.0}]}}
        if "historical-archive" in url or params.get("dataType") == "RYES":
            return {"data": None}            # archive / daily report: nothing in this fake world
        raise AssertionError(url)

    monkeypatch.setattr(H.Cache, "get", fake_get)
    monkeypatch.setattr(H.Cache, "get_text", lambda self, url, params: None)
    rc = cli.main(["history", "--start", str(start), "--end", str(end), "--train-end", "2026-07-31"])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "never seen by the tuner" in out and "VERDICT" in out
    assert "Cut-off rule matched" in out
    assert list((tmp_path / "history").glob(f"backtest from * to {start} - evening.csv"))
