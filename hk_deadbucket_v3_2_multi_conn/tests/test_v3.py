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


# --- live order path against a fake exchange client: call shapes must match the v2 library -------------
def test_executor_live_path_with_fake_client(monkeypatch):
    import sys, types
    calls = []

    class FakeClient:
        def get_tick_size(self, tok): calls.append(("tick", tok)); return "0.001"
        def get_neg_risk(self, tok): calls.append(("neg", tok)); return True
        def create_order(self, args, options=None):
            calls.append(("create", args.token_id, args.price, args.size, args.side, options.tick_size, options.neg_risk))
            return {"signed": True, "price": args.price, "size": args.size}
        def post_order(self, order, order_type="GTC", post_only=False):
            calls.append(("post", order["price"], order["size"], str(order_type), post_only)); return {"orderID": "abc", "status": "matched"}
        def get_order(self, oid): calls.append(("get", oid)); return {"id": oid}
        def cancel_order(self, oid): calls.append(("cancel", oid)); return {"canceled": [oid]}

    class OrderArgs:
        def __init__(self, token_id, price, size, side): self.token_id, self.price, self.size, self.side = token_id, price, size, side
    fake = types.ModuleType("py_clob_client_v2"); fake.OrderArgs = OrderArgs
    fake.OrderType = types.SimpleNamespace(FOK="FOK", GTC="GTC")
    ct = types.ModuleType("py_clob_client_v2.clob_types")
    class PartialCreateOrderOptions:
        def __init__(self, tick_size=None, neg_risk=None): self.tick_size, self.neg_risk = tick_size, neg_risk
    ct.PartialCreateOrderOptions = PartialCreateOrderOptions
    ob = types.ModuleType("py_clob_client_v2.order_builder"); consts = types.ModuleType("py_clob_client_v2.order_builder.constants"); consts.BUY = "BUY"
    for name, mod in {"py_clob_client_v2": fake, "py_clob_client_v2.clob_types": ct, "py_clob_client_v2.order_builder": ob, "py_clob_client_v2.order_builder.constants": consts}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    from v3.execution import Executor
    ex = Executor("dry"); ex.mode = "live"; ex.client = FakeClient()
    a = ex.prearm("TOK", "31C", 0.97, 10.3)
    assert a["signed"] and calls[0][0] == "tick" and calls[2][0] == "create" and calls[2][5] == "0.001"
    r = ex.fire("TOK", 0.90, 11.1)                    # armed 0.97 x 10.3 qualifies (price >=, shares <=) -> presigned, one POST
    assert r["presigned"] and calls[-1] == ("post", 0.97, 10.3, "FOK", False) and ex.orders_today == 1
    r = ex.fire("TOK", 0.90, 5.0)                     # armed shares 10.3 > 5 -> fresh order, no new tick lookup (cached)
    assert not r["presigned"] and calls[-2][0] == "create" and calls[-1][1:3] == (0.9, 5.0) and sum(c[0] == "tick" for c in calls) == 1
    ex.mode = "probe"
    pr = ex.probe("TOK", 0.5, 5, hold_s=0)
    assert pr["order_id"] == "abc" and pr["cancel"] == {"canceled": ["abc"]} and calls[-1] == ("cancel", "abc")


# --- the 00:00 stamp belongs to the day that just ended ------------------------------------------------
def test_reading_day_midnight_stamp_is_yesterday():
    assert sources.reading_day(datetime(2026, 10, 24, 0, 0, tzinfo=HKT)) == datetime(2026, 10, 23).date()
    assert sources.reading_day(datetime(2026, 10, 24, 0, 10, tzinfo=HKT)) == datetime(2026, 10, 24).date()
    assert sources.reading_day(datetime(2026, 10, 24, 23, 50, tzinfo=HKT)) == datetime(2026, 10, 24).date()


def test_midnight_reading_does_not_poison_the_new_day(monkeypatch, tmp_path):
    """Replay of 2026-10-24: 00:00 stamp shows yesterday's 32.3, then 28.3 at 00:10, 29.1 at 11:30 -> 28°C must die."""
    from v3 import bot as botmod, market
    from hkbot.buckets import parse_label
    labels = ("26°C or below", "27°C", "28°C", "29°C", "30°C", "31°C", "32°C", "33°C", "36°C or higher")
    loaded, killed = [], []
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(market, "load_day", lambda sess, day: loaded.append(day) or [market.Market(parse_label(l), l, "", "", "y" + l, "n" + l) for l in labels])
    monkeypatch.setattr(market, "book", lambda sess, tok: ([(0.5, 100.0)], [(0.49, 100.0)]))
    b = botmod.Bot.__new__(botmod.Bot)
    b.clob = None; b.lock = __import__("threading").Lock(); b.day = None; b.markets = []; b.last_whole = None; b.done = set(); b.stats = {"kills": 0, "polls": 0}; b.trigger_src = "text_ip203"; b.triggered = set()
    b.exe = type("E", (), {"prearm": lambda self, *a, **k: {"label": a[1]}})()
    monkeypatch.setattr(b, "decide", lambda day, m, deg, *a: killed.append((str(day), m.label, deg)))
    t = lambda h, mi: datetime(2026, 10, 24, h, mi, tzinfo=HKT)
    b.day, b.last_whole = datetime(2026, 10, 23).date(), 32           # yesterday ended at 32
    b.markets = market.load_day(None, b.day); loaded.clear()
    b.on_trigger(t(0, 0), 32.3, t(0, 7).timestamp())                  # 00:00 stamp = yesterday's max: no new day, no kill
    assert loaded == [] and killed == [] and b.day == datetime(2026, 10, 23).date()
    b.on_trigger(t(0, 10), 28.3, t(0, 17).timestamp())                # first reading of the new day: baseline 28
    assert loaded == [datetime(2026, 10, 24).date()] and b.last_whole == 28 and killed == []
    b.on_trigger(t(11, 30), 29.1, t(11, 36).timestamp())              # max enters 29 -> 28°C is dead
    assert killed == [("2026-10-24", "28°C", 29)]
    b.on_trigger(t(12, 50), 31.0, t(12, 57).timestamp())              # jumps 29 -> 31: 29°C and 30°C both die
    assert [k[1] for k in killed] == ["28°C", "29°C", "30°C"]


# --- v3.2: several connections per HKO server IP, first-of-set trigger with one fire per stamp -------------
def test_v32_connections_and_trigger_dedup(monkeypatch):
    from v3 import bot as botmod
    expect = tuple(f"{b}_c{i}" for b, n in (("text_ip203", config.CONNS_PER_IP), ("text_ip103", config.CONNS_PER_IP),
                                              ("text_hko", config.CONNS_NAMED)) for i in range(1, n + 1))
    assert sources.TRIGGERS == expect and len(expect) == 2 * config.CONNS_PER_IP + config.CONNS_NAMED
    assert sources.SOURCES["text_ip203_c1"].startswith("https://203.129.88.115/")
    assert sources.SOURCES["text_ip103_c2"].startswith("https://103.30.70.62/")
    assert sources.SOURCES["text_hko_c1"].startswith("https://www.hko.gov.hk/")
    assert sources.HOST_FOR["text_ip203_c3"] == "www.hko.gov.hk" and "text_hko_c1" not in sources.HOST_FOR
    assert sources.base_name("text_ip203_c12") == "text_ip203" and sources.base_name("text_nocache") == "text_nocache"
    assert sources.parse("text_ip203_c2", PAGE)[2] == 30.3                    # same parser as the named page
    in_window = datetime(2026, 10, 27, 12, 7, tzinfo=HKT)                      # daytime, 7 min after the 12:00 stamp
    assert all(sources.interval(s, in_window) == 2 for s in sources.TRIGGERS)
    assert sources.interval("text_nocache", in_window) == 10
    b = botmod.Bot.__new__(botmod.Bot); b.triggered = set(); b.trigger_src = ""
    fired = []
    monkeypatch.setattr(b, "on_trigger", lambda dt, mx, seen: fired.append((b.trigger_src, mx)))
    d = datetime(2026, 10, 27, 12, 0, tzinfo=HKT)
    assert b._maybe_trigger("text_ip103_c2", d, 29.1, "2026-10-27 12:00", 1.0)      # first connection to show 12:00 fires
    assert not b._maybe_trigger("text_ip203_c1", d, 29.1, "2026-10-27 12:00", 2.0)  # same stamp on another connection: no second fire
    assert not b._maybe_trigger("text_hko_c1", d, 29.1, "2026-10-27 12:00", 2.5)
    assert not b._maybe_trigger("csv_maxmin", d, 29.1, "2026-10-27 12:10", 3.0)     # not a trigger source
    assert b._maybe_trigger("text_hko_c2", d, 29.2, "2026-10-27 12:10", 4.0)        # next stamp, a named connection first this time
    assert fired == [("text_ip103_c2", 29.1), ("text_hko_c2", 29.2)]
