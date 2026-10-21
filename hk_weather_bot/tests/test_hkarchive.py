"""Archive readers for the backtest, built from real archived bulletins (2026)."""
from datetime import date, datetime

import pytest

from hkbot import hkarchive as A
from hkbot import history as H
from hkbot.buckets import parse_label
from hkbot.sources import HKT

MORNING = ("<item><description><![CDATA[Weather forecast for today: Mainly fine. Dry and hot during the day "
           "with a maximum temperature of around 32 degrees. Moderate easterly winds. Outlook: Fine for the rest "
           "of this week. Temperatures will range between 26 and 35 degrees.]]></description></item>")
EVENING_RANGE = ("Weather forecast for tonight and tomorrow: Mainly cloudy with a few showers. Temperatures will "
                 "range between 26 and 31 degrees. Moderate easterly winds. Outlook: Hot later.")
EVENING_MAX = ("Weather forecast for tonight and tomorrow: Mainly fine. The minimum temperature will be about 28 "
               "degrees tomorrow. Extremely hot during the day. The maximum temperature will be around 35 degrees "
               "in the urban areas, and a couple of degrees higher in the New Territories. Outlook: ...")
PLURAL = "Weather forecast for today: Showers. The maximum temperatures will be around 29 degrees. Outlook: x"
NO_NUMBER = "Weather forecast for today: Cloudy with rain. Outlook: Temperatures will range between 20 and 40 degrees."


def test_local_forecast_wordings():
    assert A.parse_local_forecast(MORNING) == ("today", 32.0)          # outlook's 35 is ignored
    assert A.parse_local_forecast(EVENING_RANGE) == ("tomorrow", 31.0)
    assert A.parse_local_forecast(EVENING_MAX) == ("tomorrow", 35.0)   # the minimum (28) is not taken
    assert A.parse_local_forecast(PLURAL) == ("today", 29.0)
    assert A.parse_local_forecast(NO_NUMBER) == ("today", None)        # never reads the outlook


def test_since_midnight_csv():
    csv_text = ("﻿Date time,Automatic Weather Station,Maximum Air Temperature Since Midnight(degree Celsius),"
                "Minimum Air Temperature Since Midnight(degree Celsius)\n"
                "202606011200,Chek Lap Kok,31.0,27.1\n202606011200,HK Observatory,30.4,27.6\n")
    ts, v = A.parse_since_midnight(csv_text)
    assert ts == datetime(2026, 6, 1, 12, 0, tzinfo=HKT) and v == 30.4


def test_ryes():
    assert A.parse_ryes({"HKOReadingsMaxTemp": "31.8", "BulletinDate": "20261021"}) == 31.8
    assert A.parse_ryes({}) is None


def test_archive_never_returns_a_file_after_the_decision():
    stamps = ["20260601-0802", "20260601-0902", "20260601-1102", "20260601-1202"]
    assert A.latest_at_or_before(stamps, datetime(2026, 6, 1, 12, 0, tzinfo=HKT)) == "20260601-1102"
    assert A.latest_at_or_before(stamps, datetime(2026, 6, 1, 8, 0, tzinfo=HKT)) is None


class FakeCache:
    def __init__(self, stamps, files):
        self.stamps, self.files = stamps, files

    def get(self, url, params):
        return {"data": {"timestamps": self.stamps}}

    def get_text(self, url, params):
        return {"data": self.files.get(params["time"])}


def test_hko_forecast_picks_the_right_bulletin():
    files = {"20260601-1702": EVENING_RANGE, "20260601-0802": MORNING, "20260601-1802": EVENING_MAX}
    c = FakeCache(list(files), files)
    # evening decision 18:00 on D-1 for target D: the 17:02 "tomorrow" bulletin, never the 18:02 one
    v, ts = A.hko_forecast_at(c, date(2026, 6, 2), datetime(2026, 6, 1, 18, 0, tzinfo=HKT))
    assert (v, ts) == (31.0, "20260601-1702")
    # morning decision for the same day: the 08:02 "today" bulletin
    v, ts = A.hko_forecast_at(c, date(2026, 6, 1), datetime(2026, 6, 1, 9, 0, tzinfo=HKT))
    assert (v, ts) == (32.0, "20260601-0802")


def test_max_so_far_rejects_a_stale_or_future_csv():
    good = "x\n202606011150,HK Observatory,30.4,27.6\n"
    future = "x\n202606011250,HK Observatory,33.0,27.6\n"
    c = FakeCache(["20260601-1150"], {"20260601-1150": good})
    assert A.max_so_far_at(c, date(2026, 6, 1), datetime(2026, 6, 1, 12, 0, tzinfo=HKT))[0] == 30.4
    c = FakeCache(["20260601-1150"], {"20260601-1150": future})       # file claims a later reading
    assert A.max_so_far_at(c, date(2026, 6, 1), datetime(2026, 6, 1, 12, 0, tzinfo=HKT))[0] is None


LADDER = ["26°C or below"] + [f"{k}°C" for k in range(27, 36)] + ["36°C or higher"]
BUCKETS = [parse_label(x) for x in LADDER]


def test_live_reading_rules_out_lower_buckets_in_the_backtest():
    from hkbot import config
    from hkbot.config import get_strategy
    mk = [0.001, 0.001, 0.01, 0.05, 0.25, 0.4, 0.2, 0.08, 0.005, 0.002, 0.001]
    snap = H.DaySnapshot(date(2026, 6, 1), BUCKETS, mk, [m / sum(mk) for m in mk], {"ecmwf": 30.0},
                         dict(config.DEFAULT_PRIORS), obs_max=31.4, hours_left=3.0, _actual=31.6)
    p = H.predict(snap, get_strategy("v2_audited"))[0]
    # model part gives 0 below 31; the market half still holds some weight there (market_blend 0.5)
    model_only = H.predict(snap, get_strategy("v2_audited", market_blend=1.0))[0]
    assert sum(model_only[:5]) == 0                                  # 30 and below ruled out
    i31 = LADDER.index("31°C")
    assert model_only[i31] + model_only[i31 + 1] > 0.9        # 3 h of heating left: 31 or 32
    assert p[LADDER.index("31°C")] > mk[LADDER.index("31°C")] / sum(mk)


def test_fee_is_paid_on_both_sides():
    snap = H.DaySnapshot(date(2026, 6, 1), BUCKETS[:1], [0.5], [1.0], {}, {}, _actual=30.0)
    mb = H.sim_market(snap, 0.02, fee_rate=0.05)[0]
    assert mb.yes_ask == pytest.approx(0.52 + 0.05 * 0.52 * 0.48)
    assert 1 - mb.yes_bid == pytest.approx(0.52 + 0.05 * 0.52 * 0.48)     # NO side symmetric at 0.5


def test_live_flw_feed_gives_target_day_and_max(monkeypatch):
    from hkbot import sources

    class R:
        def json(self):
            return {"forecastPeriod": "Weather forecast for tonight and tomorrow",
                    "forecastDesc": "Mainly fine. Temperatures will range between 26 and 32 degrees.",
                    "outlook": "Hotter later, up to 35 degrees.", "tcInfo": "Super Typhoon X ...",
                    "updateTime": "2026-10-21T16:45:00+08:00"}
    monkeypatch.setattr(sources, "_get", lambda *a, **k: R())
    f = sources.fetch_hko_local_forecast()
    assert (f["period"], f["max"], f["target"]) == ("tomorrow", 32.0, date(2026, 10, 22))
    assert f["tc_info"].startswith("Super Typhoon")
