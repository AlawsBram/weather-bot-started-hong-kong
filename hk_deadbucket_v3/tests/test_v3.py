import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from v3 import config, sources  # noqa: E402

HKT = timezone(timedelta(hours=8))
PAGE = """<html><body><pre>Latest readings recorded at 15:00 Hong Kong Time 22 October 2026
                               Air     Relative   Maximum/Minimum   Past 24-hour
HK Observatory                 30.0       57        30.3 / 26.0       -2.1
</pre></body></html>"""


def test_cutoff():
    assert sources.cutoff_value(31.9) == 31
    assert sources.cutoff_value(32.0) == 32
    assert sources.cutoff_value(30.3) == 30


def test_parse_text_page():
    dt, temp, mx, mn = sources.parse("text_hko", PAGE)
    assert dt == datetime(2026, 10, 22, 15, 0, tzinfo=HKT) and temp == 30.0 and mx == 30.3 and mn == 26.0


def test_parse_csvs():
    assert sources.parse("csv_maxmin", "Date time,Station,Max,Min\n202610221450,HK Observatory,30.3,26.0\n")[2] == 30.3
    assert sources.parse("csv_1min", "Date time,Station,T\n202610221450,Chek Lap Kok,29.7\n202610221450,HK Observatory,29.9\n")[1] == 29.9
    assert sources.parse("csv_1min", "Date time,Station,T\n202610221450,Chek Lap Kok,29.7\n") is None


def test_publish_window_and_intervals():
    def at(h, m, s=0):
        return datetime(2026, 10, 22, h, m, s, tzinfo=HKT)
    assert not sources.in_publish_window(at(14, 25))          # 5.0 min after the :20 stamp: not yet
    assert sources.in_publish_window(at(14, 25, 30))          # 5.5 min
    assert sources.in_publish_window(at(14, 29, 40))          # 9.67 min
    assert not sources.in_publish_window(at(14, 29, 50))      # 9.83 min
    assert sources.interval("text_hko", at(14, 27)) == 2
    assert sources.interval("text_hko", at(14, 22)) == config.OUTSIDE_INTERVAL
    assert sources.interval("text_hko", at(2, 27)) == config.NIGHT_INTERVAL
    assert sources.interval("rhrread", at(14, 27)) == 30


def test_killed_by_uses_exclusive_upper_edge(monkeypatch):
    from v3 import market
    from hkbot.buckets import parse_label
    ms = [market.Market(parse_label(l), l, "", "", "y", "n") for l in ("26°C or below", "27°C", "30°C", "31°C", "36°C or higher")]
    assert [m.label for m in market.killed_by(ms, 31)] == ["30°C"]      # max reaches 31.0 -> "30°C" dead
    assert [m.label for m in market.killed_by(ms, 27)] == ["26°C or below"]
    assert market.killed_by(ms, 37) == []                               # the top bucket never dies
    assert market.containing(ms, 30).label == "30°C"
