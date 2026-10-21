"""Tests for the VPS snapshot additions, built from real responses captured on 2026-10-21."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from hkbot import sources
from hkbot.config import DEFAULT_PRIORS, SourcePrior, get_strategy
from hkbot.model import combine, priors_from_stats, trust
from hkbot.pages import parse_extract_rows, parse_fnd_items, parse_polymarket_text
from hkbot.snapshot import cost_to_buy, depth_within, hko_pages_due, no_levels

HKT = ZoneInfo("Asia/Hong_Kong")

# Daily Extract data file, as served on 2026-10-21 (trimmed). Oct 19 = 31.9 settled "31°C".
DAILY_EXTRACT = {"stn": {"data": [{"month": 10, "dayData": [
    ["15", "1015.9", "29.7", "27.6", "26.6", "22.1", "72", "46", "  0.0"],
    ["19", "1015.4", "31.9", "28.5", "26.5", "23.3", "74", "20", "  0.0"],
    ["Mean/Total", "1015.4", "30.5", "27.8", "26.0", "22.3", "72", "58", " 77.4"],
    ["Normal", "1014.0", "28.1", "25.7", "23.9", "20.2", "73", "58", "120.3"],
]}]}}


def test_daily_extract_parse():
    assert sources.parse_daily_extract(DAILY_EXTRACT, 2026) == {date(2026, 10, 15): 29.7, date(2026, 10, 19): 31.9}


def test_web_fnd_parse():
    payload = {"DYN_DAT_MINDS_FND": {
        "Day1ForecastDate": {"Value_Eng": "20261021"}, "Day1MaxTemp": {"Value_Eng": "33"},
        "Day2ForecastDate": {"Value_Eng": "20261022"}, "Day2MaxTemp": {"Value_Eng": "32"}}}
    assert sources.parse_web_fnd(payload) == {date(2026, 10, 21): 33.0, date(2026, 10, 22): 32.0}


def test_since_midnight_label_is_hk_observatory(monkeypatch):
    csv_text = ("Date time,Automatic Weather Station,Maximum Air Temperature Since Midnight(degree Celsius),"
                "Minimum Air Temperature Since Midnight(degree Celsius)\n"
                "202610210140,Chek Lap Kok,28.4,27.6\n202610210140,HK Observatory,27.8,27.4\n")

    class R:
        content = csv_text.encode("utf-8-sig")
    monkeypatch.setattr(sources, "_get", lambda *a, **k: R())
    assert sources.fetch_hko_max_since_midnight() == 27.8


def test_fnd_page_items():
    items = ["21 Oct (Wed) 26 | 33°C 55-90% Low Light winds force 2 ...",
             "22 Oct (Thu) 25 | 32°C 50-85% Low North to northeast ...", "garbage"]
    assert parse_fnd_items(items, 2026, date(2026, 10, 21)) == {date(2026, 10, 21): 33.0, date(2026, 10, 22): 32.0}
    # a January day seen in December belongs to next year
    assert parse_fnd_items(["2 Jan (Sat) 15 | 19°C"], 2026, date(2026, 12, 30)) == {date(2027, 1, 2): 19.0}


def test_extract_page_rows():
    rows = ["Day Hong Kong Observatory", "Absolute Daily Max (deg. C) Mean (deg. C)",
            "01 1012.7 33.6 30.4 28.3 25.3 75 42 0.0", "19 1015.4 31.9 28.5 26.5 23.3 74 20 0.0",
            "Mean/Total 1015.4 30.5 27.8"]
    assert parse_extract_rows(rows, 2026, 10) == {date(2026, 10, 1): 33.6, date(2026, 10, 19): 31.9}


PM_TEXT = ('Highest temperature in Hong Kong on October 21?\n28°C or below\n\n$193 Vol.\n\n<1%\n\nBuy Yes\n0.2¢\n'
           'Buy No\n99.9¢\n\n29°C\n\n$802 Vol.\n\n<1%\n\nBuy Yes\n0.4¢\nBuy No\n99.9¢\n\n31°C\n\n$3,805 Vol.\n\n7%\n\n'
           '43%\nBuy Yes\n7.9¢\nBuy No\n93.0¢\n\n32°C\n\n$2,659 Vol.\n\n42%\n\n8%\nBuy Yes\n43¢\nBuy No\n59¢\n\n'
           '38°C or higher\n\n$115 Vol.\n\n<1%\n\nBuy Yes\n0.1¢\nBuy No\n--\nThis market will resolve ...')


def test_polymarket_page_text():
    got = parse_polymarket_text(PM_TEXT, ["28°C or below", "29°C", "31°C", "32°C", "38°C or higher"])
    assert got["28°C or below"] == pytest.approx({"buy_yes": 0.002, "buy_no": 0.999})
    assert got["31°C"] == pytest.approx({"buy_yes": 0.079, "buy_no": 0.93})
    assert got["32°C"] == pytest.approx({"buy_yes": 0.43, "buy_no": 0.59})
    assert got["38°C or higher"] == {"buy_yes": 0.001, "buy_no": None}


def test_fee_and_book_walk():
    # Book for 31°C YES on 2026-10-21 (best ask first)
    asks = [[0.079, 62.29], [0.08, 127.32], [0.089, 121], [0.09, 226.9]]
    avg, shares = sources.fill_price(asks, 10.0)
    assert 0.079 < avg < 0.08 and shares * avg == pytest.approx(10.0)
    assert sources.taker_fee_per_share(0.5, 0.05) == pytest.approx(0.0125)
    c = cost_to_buy(asks, 10.0, 0.05)
    assert c["all_in"] == pytest.approx(avg + 0.05 * avg * (1 - avg))
    assert c["shares"] == pytest.approx(10.0 / c["all_in"])
    # thin book: only $1 available
    c = cost_to_buy([[0.5, 2.0]], 10.0, 0.05)
    assert c["full"] is False
    assert depth_within(asks) == pytest.approx(0.079 * 62.29 + 0.08 * 127.32 + 0.089 * 121 + 0.09 * 226.9)


def test_no_levels_mirror_yes_bids_when_no_book():
    yb = {"bids": [[0.07, 29], [0.063, 28]], "asks": []}
    assert no_levels(yb, None) == [[0.93, 29], [0.937, 28]]
    assert no_levels(yb, {"asks": [[0.92, 5]]}) == [[0.92, 5]]


def test_auto_weights_fade_and_v2_unchanged():
    v2, v3 = get_strategy("v2_audited"), get_strategy("v3_auto_weights")
    gfs = SourcePrior(bias=-2.0, sigma=2.0, weight=0.3, n=0)
    assert trust(gfs, v2) == 0.3 and trust(gfs, v3) == 0.3          # no data yet: same as v2
    measured = SourcePrior(bias=-2.0, sigma=2.0, weight=0.3, n=45)
    assert trust(measured, v2) == 0.3                                # v2 never changes
    assert trust(measured, v3) == pytest.approx(0.3 + 0.9 * 0.7)     # k = 45/50 = 0.9
    inputs = {"hko_official": 32.0, "ecmwf": 30.0, "icon": 30.0, "gfs": 28.0}
    assert combine(inputs, v2, dict(DEFAULT_PRIORS)).mean == pytest.approx(
        combine(inputs, v3, dict(DEFAULT_PRIORS)).mean)               # identical until measured
    pri = priors_from_stats({"gfs": {"n": 45, "bias": -1.0, "sigma": 1.0}})
    assert pri["gfs"].n == 45 and combine(inputs, v3, pri).mean != combine(inputs, v2, pri).mean


def test_hko_pages_schedule():
    base = datetime(2026, 10, 21, 9, 0, tzinfo=HKT)
    assert hko_pages_due(base, None)                                           # first run
    assert not hko_pages_due(base + timedelta(minutes=30), base.timestamp())   # 30 min later: no
    assert hko_pages_due(base + timedelta(hours=3), base.timestamp())          # 3 h later: yes
    t = datetime(2026, 10, 21, 11, 45, tzinfo=HKT)                             # right after the 11:30 bulletin
    assert hko_pages_due(t, (t - timedelta(hours=1)).timestamp())
    assert not hko_pages_due(t, (t - timedelta(minutes=5)).timestamp())


def test_gamma_parse_keeps_tokens_and_fee():
    ev = {"slug": "x", "closed": False, "markets": [{
        "groupItemTitle": "31°C", "outcomePrices": "[\"0.07\", \"0.93\"]", "bestBid": 0.07, "bestAsk": 0.079,
        "clobTokenIds": "[\"111\", \"222\"]", "feesEnabled": True,
        "feeSchedule": {"exponent": 1, "rate": 0.05, "takerOnly": True, "rebateRate": 0.25}}]}
    mb = sources.parse_gamma_event(ev, date(2026, 10, 21)).buckets[0]
    assert (mb.yes_token, mb.no_token, mb.fee_rate) == ("111", "222", 0.05)
