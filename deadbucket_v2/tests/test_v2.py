import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v2 import buckets, metar, rules  # noqa: E402


# --- rounding exactly as the rules' own examples -----------------------------------------
@pytest.mark.parametrize("tenths,unit,whole", [
    (139, "C", 14), (174, "C", 17), (165, "C", 17), (-17, "C", -2), (-85, "C", -8),
    (183, "F", 65), (178, "F", 64), (190, "F", 66), (-225, "F", -8), (235, "C", 24), (234, "C", 23),
])
def test_whole_degrees(tenths, unit, whole):
    assert buckets.whole_degrees(tenths, unit) == whole


def test_labels():
    assert buckets.parse_label("22°C or below") == buckets.Bucket("22°C or below", None, 22, "C")
    assert buckets.parse_label("23°C") == buckets.Bucket("23°C", 23, 23, "C")
    assert buckets.parse_label("32°C or higher") == buckets.Bucket("32°C or higher", 32, None, "C")
    assert buckets.parse_label("52-53°F") == buckets.Bucket("52-53°F", 52, 53, "F")
    assert buckets.parse_label("51°F or below").hi == 51
    assert buckets.parse_label("70°F or higher").lo == 70
    with pytest.raises(ValueError):
        buckets.parse_label("Yes")


def test_dead_highest_c():
    b = buckets.parse_label("23°C")
    assert not buckets.is_dead(b, "highest", 234)      # 23.4 -> 23: alive
    assert buckets.is_dead(b, "highest", 235)          # 23.5 -> 24: dead
    assert buckets.margin_tenths(b, "highest", 237) == 2
    assert not buckets.is_dead(buckets.parse_label("32°C or higher"), "highest", 400)
    assert buckets.is_dead(buckets.parse_label("22°C or below"), "highest", 225)


def test_dead_lowest_and_f():
    b = buckets.parse_label("14°C")
    assert not buckets.is_dead(b, "lowest", 135)       # 13.5 -> 14 : alive
    assert buckets.is_dead(b, "lowest", 134)           # 13.4 -> 13 : dead
    assert not buckets.is_dead(buckets.parse_label("7°C or below"), "lowest", -100)
    f = buckets.parse_label("52-53°F")
    assert not buckets.is_dead(f, "highest", 119)      # 11.9C = 53.42F -> 53 alive
    assert buckets.is_dead(f, "highest", 120)          # 12.0C = 53.6F -> 54 dead


# --- METAR parsing --------------------------------------------------------------------
def test_metar_values():
    assert metar.qualifying_tenths("METAR KLGA 220651Z 35013KT 10SM FEW250 12/03 A3000 RMK AO2 SLP157 T01220028") == (True, 122)
    assert metar.qualifying_tenths("KLGA 220651Z 35013KT 10SM FEW250 12/03 A3000 RMK AO2 T10171022") == (True, -17)
    assert metar.qualifying_tenths("METAR RCSS 220700Z 08009KT 3000 RA SCT005 23/21 Q1013 NOSIG") == (True, 230)
    assert metar.qualifying_tenths("METAR UUEE 220700Z 00000KT M11/M17 Q1021") == (True, -110)
    assert metar.qualifying_tenths("SPECI RCSS 220735Z 08009KT 23/21 Q1013") == (False, None)
    assert metar.qualifying_tenths("METAR XXXX 220700Z 00000KT 9999 R04/P2000 Q1021") == (True, None)
    assert metar.qualifying_tenths("METAR XXXX 220700Z 1 1/2SM BR 18/ Q1021") == (True, 180)


# --- rules parsing ---------------------------------------------------------------------
def test_rules_metar_window_and_station():
    e = rules.parse_event({"slug": "highest-temperature-in-nyc-on-october-24-2026",
                           "resolutionSource": "https://aviationweather.gov/api/data/metar?ids=KLGA&format=raw&date=2026-10-25T07:00:00Z&hours=30",
                           "description": "... reported at LaGuardia Airport (KLGA) on 24 Oct '26 ... in whole degrees Fahrenheit ...",
                           "markets": [{"groupItemTitle": "52-53°F", "id": "1", "conditionId": "0xa", "clobTokenIds": '["11","22"]', "closed": False, "outcomePrices": '["0.3","0.7"]'},
                                       {"groupItemTitle": "51°F or below", "id": "2", "conditionId": "0xb", "clobTokenIds": '["33","44"]'}]})
    assert e.style == "metar" and e.icao == "KLGA" and e.unit == "F" and e.supported
    assert e.window_end == datetime(2026, 10, 25, 4, 0, tzinfo=timezone.utc)
    assert e.window_start == datetime(2026, 10, 24, 4, 0, tzinfo=timezone.utc)
    assert [m.label for m in e.markets] == ["51°F or below", "52-53°F"]
    assert e.markets[1].no_token == "22"


def test_rules_other_styles_not_traded():
    wu = rules.parse_event({"slug": "lowest-temperature-in-taipei-on-october-22-2026", "resolutionSource": "https://www.wunderground.com/history/daily/tw/taipei/RCSS",
                            "description": "Wunderground ...", "markets": [{"groupItemTitle": "9°C", "id": "1", "conditionId": "", "clobTokenIds": '["1","2"]'}]})
    assert wu.style == "wu" and wu.icao == "RCSS" and not wu.supported
    hk = rules.parse_event({"slug": "highest-temperature-in-hong-kong-on-october-24-2026", "resolutionSource": None,
                            "description": "recorded by the Hong Kong Observatory ...", "markets": [{"groupItemTitle": "27°C", "id": "1", "conditionId": "", "clobTokenIds": '["1","2"]'}]})
    assert hk.style == "hko" and not hk.supported
    bad = rules.parse_event({"slug": "highest-temperature-in-x-on-october-24-2026",
                             "resolutionSource": "https://aviationweather.gov/api/data/metar?ids=KXXX&format=raw&date=2026-10-25T07:00:00Z&hours=30",
                             "description": "reported at Some Airport (KYYY)", "markets": [{"groupItemTitle": "9°C", "id": "1", "conditionId": "", "clobTokenIds": '["1","2"]'}]})
    assert not bad.supported and any("differs" in x for x in bad.problems)


def test_station_table_change(tmp_path):
    p = tmp_path / "stations.json"
    e1 = rules.parse_event({"slug": "highest-temperature-in-x-on-october-23-2026", "resolutionSource": "https://www.wunderground.com/history/daily/a/b/KAAA",
                            "description": "", "markets": [{"groupItemTitle": "9°C", "id": "1", "conditionId": "", "clobTokenIds": '["1","2"]'}]})
    assert rules.update_station_table([e1], p) == []
    e2 = rules.parse_event({"slug": "highest-temperature-in-x-on-october-24-2026",
                            "resolutionSource": "https://aviationweather.gov/api/data/metar?ids=KBBB&format=raw&date=2026-10-25T07:00:00Z&hours=30",
                            "description": "(KBBB)", "markets": [{"groupItemTitle": "9°C", "id": "1", "conditionId": "", "clobTokenIds": '["1","2"]'}]})
    ch = rules.update_station_table([e2], p)
    assert ch and "wu/KAAA -> metar/KBBB" in ch[0]


# --- day window: local midnight in the station's zone; the rules URL is only a cross-check ----------
def _metar_event(slug, url):
    icao = url.split("ids=")[1][:4]
    return rules.parse_event({"slug": slug, "resolutionSource": url, "description": f"... reported at the airport ({icao}) ... whole degrees Celsius ...",
                              "markets": [{"groupItemTitle": "12°C", "id": "1", "conditionId": "0xa", "clobTokenIds": '["1","2"]'}]})


def test_window_oct23_convention_hours27():
    # Oct 23 batch: date = local midnight, hours=27. The old "date - 3 h" code made this 3 h early (the 2026-10-23 bug).
    e = _metar_event("highest-temperature-in-helsinki-on-october-23-2026",
                     "https://aviationweather.gov/api/data/metar?ids=EFHK&format=raw&date=2026-10-23T21:00:00Z&hours=27")
    assert e.window_start == datetime(2026, 10, 22, 21, 0, tzinfo=timezone.utc)
    assert e.window_end == datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc)
    assert e.problems == [] and e.supported
    # the Helsinki 12.0 C report at 18:20Z on Oct 22 (21:20 local, the day BEFORE) must be outside
    assert not (e.window_start <= datetime(2026, 10, 22, 18, 20, tzinfo=timezone.utc) < e.window_end)


def test_window_oct24_convention_hours30_and_dst_end():
    e = _metar_event("highest-temperature-in-helsinki-on-october-24-2026",
                     "https://aviationweather.gov/api/data/metar?ids=EFHK&format=raw&date=2026-10-25T00:00:00Z&hours=30")
    assert (e.window_start, e.window_end) == (datetime(2026, 10, 23, 21, 0, tzinfo=timezone.utc), datetime(2026, 10, 24, 21, 0, tzinfo=timezone.utc))
    assert e.problems == []
    # Oct 25 2026: EU clocks go back, the local day is 25 h long; the rule counts by local date, so the window is 25 h
    e = _metar_event("highest-temperature-in-helsinki-on-october-25-2026",
                     "https://aviationweather.gov/api/data/metar?ids=EFHK&format=raw&date=2026-10-26T01:00:00Z&hours=30")
    assert (e.window_start, e.window_end) == (datetime(2026, 10, 24, 21, 0, tzinfo=timezone.utc), datetime(2026, 10, 25, 22, 0, tzinfo=timezone.utc))
    assert e.problems == []


def test_window_cross_check_flags_mismatch_and_unknown_city():
    bad = _metar_event("highest-temperature-in-nyc-on-october-24-2026",
                       "https://aviationweather.gov/api/data/metar?ids=KLGA&format=raw&date=2026-10-24T04:00:00Z&hours=30")
    assert any("local midnight" in p for p in bad.problems) and not bad.supported
    unk = _metar_event("highest-temperature-in-atlantis-on-october-24-2026",
                       "https://aviationweather.gov/api/data/metar?ids=KXYZ&format=raw&date=2026-10-25T07:00:00Z&hours=30")
    assert any("time zone" in p for p in unk.problems) and not unk.supported
    assert unk.window_start == datetime(2026, 10, 24, 4, 0, tzinfo=timezone.utc)   # URL-derived fallback, still logged
