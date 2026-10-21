"""Tests built from the real data we checked by hand (Oct 2026)."""
import math
import random
from datetime import date, timedelta

import pytest

from hkbot.backtest import calibrate, summarise, walk_forward
from hkbot.buckets import Bucket, parse_label, winning_bucket
from hkbot.config import DEFAULT_PRIORS, get_strategy
from hkbot.model import blend_with_market, bucket_probs, combine
from hkbot.scoring import brier, trade_pnl
from hkbot.sources import MarketBucket, parse_clmmaxt, parse_gamma_event
from hkbot.strategy import find_trades

LADDER = ["26°C or below", "27°C", "28°C", "29°C", "30°C", "31°C", "32°C", "33°C",
          "34°C", "35°C", "36°C or higher"]
BUCKETS = [parse_label(x) for x in LADDER]

# (day, HKO Absolute Daily Max, bucket the Polymarket market settled on)
OCT_2026 = [
    (2, 31.7, "31°C"), (4, 30.6, "30°C"), (9, 30.6, "30°C"), (15, 29.7, "29°C"),   # decisive days
    (6, 28.2, "28°C"), (8, 29.0, "29°C"), (11, 31.0, "31°C"), (12, 31.3, "31°C"),
    (13, 31.0, "31°C"), (14, 30.0, "30°C"),
]


def test_labels():
    assert parse_label("31°C") == Bucket("31°C", 31, 32)
    b = parse_label("26°C or below")
    assert b.lo == -math.inf and b.hi == 27
    b = parse_label("36°C or higher")
    assert b.lo == 36 and b.hi == math.inf


@pytest.mark.parametrize("day,reading,settled", OCT_2026)
def test_cutoff_rule_matches_real_settlements(day, reading, settled):
    assert winning_bucket(BUCKETS, reading, cutoff=True).label == settled


def test_rounding_would_have_been_wrong_on_decisive_days():
    wrong = [d for d, r, s in OCT_2026[:4] if winning_bucket(BUCKETS, r, cutoff=False).label != s]
    assert wrong == [2, 4, 9, 15]


def test_edge_readings():
    assert winning_bucket(BUCKETS, 31.9).label == "31°C"
    assert winning_bucket(BUCKETS, 32.0).label == "32°C"
    assert winning_bucket(BUCKETS, 26.9).label == "26°C or below"
    assert winning_bucket(BUCKETS, 37.4).label == "36°C or higher"


def test_brier_matches_manual_audit():
    labels = ["29°C", "30°C", "31°C", "32°C", "33°C", "other"]
    you = [0.01, 0.02, 0.40, 0.51, 0.05, 0.01]
    mkt = [0.02, 0.15, 0.53, 0.29, 0.044, 0.0]
    wi = labels.index("31°C")
    assert brier(you, wi) == pytest.approx(0.62, abs=0.01)
    assert brier(mkt, wi) == pytest.approx(0.33, abs=0.01)


def test_paper_pnl_matches_manual_audit():
    # 1 share each: YES 32 @31c, NO 31 @48c, NO 30 @89c; 31 won.
    total = (trade_pnl("YES", 0.31, 1, False) + trade_pnl("NO", 0.48, 1, True)
             + trade_pnl("NO", 0.89, 1, False))
    assert total == pytest.approx(-0.68)


# --- Oct 19 replay: inputs exactly as gathered on Oct 18 afternoon ---------------
OCT19_INPUTS = {"hko_official": 32.0, "ecmwf": 30.0, "icon": 30.0, "gfs": 28.0}
# (yes_ask, NO price) from the screenshot -> yes_bid = 1 - NO price
OCT19_BOOK = {
    "26°C or below": (0.001, None), "27°C": (0.001, None), "28°C": (0.002, 0.999),
    "29°C": (0.029, 0.988), "30°C": (0.15, 0.89), "31°C": (0.55, 0.48), "32°C": (0.31, 0.72),
    "33°C": (0.048, 0.959), "34°C": (0.001, None), "35°C": (0.001, None), "36°C or higher": (0.001, None),
}


def oct19_market():
    out = []
    for lab in LADDER:
        ask, no = OCT19_BOOK[lab]
        bid = None if no is None else round(1 - no, 4)
        mid = ask if bid is None else (ask + bid) / 2
        out.append(MarketBucket(parse_label(lab), bid, ask, mid, None))
    return out


def replay(version):
    strat = get_strategy(version)
    market = oct19_market()
    fc = combine(OCT19_INPUTS, strat, dict(DEFAULT_PRIORS))
    model_p = bucket_probs(BUCKETS, fc, strat)
    final_p = blend_with_market(model_p, [m.mid for m in market], strat.market_blend)
    return fc, final_p, find_trades(market, final_p, strat)


def test_oct19_v2_beats_v1_and_avoids_the_32_trap():
    wi = LADDER.index("31°C")
    _, p1, t1 = replay("v1_naive")
    fc2, p2, t2 = replay("v2_audited")
    # v1 makes the same mistake we made by hand: favours 32.
    assert p1[LADDER.index("32°C")] > p1[wi]
    # v2 favours 31 and scores better than v1.
    assert max(range(len(p2)), key=p2.__getitem__) == wi
    assert brier(p2, wi) < brier(p1, wi)
    # v1 reproduces the manual trades exactly (NO 31, YES 32).
    assert {("31°C", "NO"), ("32°C", "YES")} <= {(t.label, t.side) for t in t1}
    # v2 must not repeat the core mistake: buying YES on 32 off a rounded "32" forecast.
    assert ("32°C", "YES") not in {(t.label, t.side) for t in t2}
    # 30 gets a fair share now (manual v1 had 2%).
    assert p2[LADDER.index("30°C")] >= 0.08


def test_intraday_floor():
    strat = get_strategy("v2_audited")
    fc = combine(OCT19_INPUTS, strat, dict(DEFAULT_PRIORS))
    p = bucket_probs(BUCKETS, fc, strat, obs_max_so_far=31.4, hours_left=1.0)
    assert sum(p[:5]) == 0                        # 30 and below impossible once 31.4 recorded
    assert p[LADDER.index("31°C")] > 0.8


def test_gamma_parse():
    ev = {"slug": "highest-temperature-in-hong-kong-on-october-19-2026", "closed": True, "markets": [
        {"groupItemTitle": "31°C", "outcomePrices": "[\"1\", \"0\"]", "closed": True, "bestBid": "", "bestAsk": ""},
        {"groupItemTitle": "32°C", "outcomePrices": "[\"0\", \"1\"]", "closed": True},
        {"groupItemTitle": "26°C or below", "outcomePrices": "[\"0\", \"1\"]", "closed": True},
    ]}
    snap = parse_gamma_event(ev, date(2026, 10, 19))
    assert [b.bucket.label for b in snap.buckets][0] == "26°C or below"
    assert snap.winner().bucket.label == "31°C"


def test_clmmaxt_parse():
    payload = {"type": ["Year", "Month", "Day", "Value", "data Completeness"],
               "data": [["2026", "10", "15", "29.7", "C"], ["2026", "10", "16", "***", ""]]}
    assert parse_clmmaxt(payload) == {date(2026, 10, 15): 29.7}


def test_walk_forward_and_calibrate_run():
    rnd = random.Random(1)
    start = date(2026, 8, 1)
    actual, fc = {}, {"hko_official": {}, "ecmwf": {}, "gfs": {}}
    for i in range(80):
        d = start + timedelta(days=i)
        t = 30 + 2 * math.sin(i / 9) + rnd.gauss(0, 0.6)
        actual[d] = round(t, 1)
        fc["hko_official"][d] = round(t + 0.4 + rnd.gauss(0, 0.7))
        fc["ecmwf"][d] = t - 1.0 + rnd.gauss(0, 1.0)
        fc["gfs"][d] = t - 2.5 + rnd.gauss(0, 1.5)
    days = [start + timedelta(days=i) for i in range(40, 80)]
    strat = get_strategy("v2_audited")
    s2 = summarise(walk_forward(strat, actual, fc, days))
    s1 = summarise(walk_forward(get_strategy("v1_naive"), actual, fc, days))
    assert s2["days"] == 40 and s2["avg_brier"] < s1["avg_brier"]
    best, table = calibrate(strat, actual, fc, days)
    assert best in [t[0] for t in table]
