"""Orders. DRY never touches the exchange. PROBE/LIVE need deploy/.env (POLY_PRIVATE_KEY, POLY_FUNDER,
POLY_SIGNATURE_TYPE, LIVE_TRADING=1 for live) and the py_clob_client_v2 library in the venv.

Pre-arming: as soon as the reading tells us which bucket dies NEXT, the order for its NO token is
built (and, with a client, signed) ahead of time. Measured elsewhere: signing is ~1 ms, the POST ~22 ms;
the real saving is that no lookup is needed at the trigger moment.
"""
from __future__ import annotations

import os
import time

CLOB_HOST = "https://clob.polymarket.com"


class Executor:
    def __init__(self, mode: str):
        assert mode in ("dry", "probe", "live")
        self.mode = mode
        self.client = None
        self.orders_today = 0
        self.armed: dict | None = None
        self.opts: dict[str, object] = {}       # token -> PartialCreateOrderOptions (tick size, neg risk), looked up once
        if mode != "dry":
            self._connect()

    def _connect(self):
        from py_clob_client_v2 import ClobClient
        pk, funder = os.environ.get("POLY_PRIVATE_KEY"), os.environ.get("POLY_FUNDER")
        sig = os.environ.get("POLY_SIGNATURE_TYPE", "2")
        if not pk or not funder:
            raise SystemExit("POLY_PRIVATE_KEY / POLY_FUNDER missing from deploy/.env")
        if self.mode == "live" and os.environ.get("LIVE_TRADING") != "1":
            raise SystemExit("LIVE_TRADING=1 is not set: refusing to start in live mode")
        sig = int(sig) if sig.isdigit() else sig
        creds = ClobClient(host=CLOB_HOST, key=pk, chain_id=137).create_or_derive_api_key()
        self.client = ClobClient(host=CLOB_HOST, key=pk, chain_id=137, creds=creds, signature_type=sig, funder=funder)

    def _options(self, token: str):
        """Tick size + neg-risk for a token, looked up once (create_order otherwise asks the API every time)."""
        if token not in self.opts:
            from py_clob_client_v2.clob_types import PartialCreateOrderOptions
            self.opts[token] = PartialCreateOrderOptions(tick_size=str(self.client.get_tick_size(token)), neg_risk=bool(self.client.get_neg_risk(token)))
        return self.opts[token]

    def _build(self, no_token: str, price: float, shares: float):
        from py_clob_client_v2 import OrderArgs
        from py_clob_client_v2.order_builder.constants import BUY
        return self.client.create_order(OrderArgs(token_id=no_token, price=round(price, 3), size=round(shares, 2), side=BUY), self._options(no_token))

    def prearm(self, no_token: str, label: str, price: float, shares: float) -> dict:
        """Build (and in live mode sign) the next kill's order now, so the trigger only has to POST."""
        armed = {"label": label, "no_token": no_token, "price": round(price, 3), "shares": round(shares, 2),
                 "armed_at": time.time(), "signed": False}
        if self.mode == "live" and self.client is not None:
            t0 = time.time()
            armed["order"] = self._build(no_token, armed["price"], armed["shares"])
            armed["signed"], armed["sign_ms"] = True, round(1000 * (time.time() - t0), 2)
        self.armed = armed
        return armed

    def fire(self, no_token: str, price: float, shares: float) -> dict:
        """LIVE: fill-or-kill taker buy of NO; uses the pre-signed order if it matches."""
        from py_clob_client_v2 import OrderType
        t0 = time.time()
        a = self.armed
        if a and a.get("signed") and a["no_token"] == no_token and a["price"] >= price and a["shares"] <= shares:
            order, pre = a["order"], True
        else:
            order, pre = self._build(no_token, price, shares), False
        resp = self.client.post_order(order, order_type=OrderType.FOK)
        self.orders_today += 1
        return {"ms": round(1000 * (time.time() - t0)), "presigned": pre, "resp": resp}

    def probe(self, no_token: str, price: float, shares: float, hold_s: float = 3.0) -> dict:
        """Prove the path without buying: post-only bid far below market, read it back, cancel."""
        from py_clob_client_v2 import OrderType
        t0 = time.time()
        order = self._build(no_token, price, shares)
        resp = self.client.post_order(order, order_type=OrderType.GTC, post_only=True)
        posted_ms = round(1000 * (time.time() - t0))
        oid = (resp or {}).get("orderID") or (resp or {}).get("id")
        seen = cancel = None
        if oid:
            time.sleep(hold_s)
            try:
                seen = self.client.get_order(oid)
            except Exception as e:      # noqa: BLE001
                seen = f"get_order failed: {type(e).__name__}"
            try:
                cancel = self.client.cancel_order(oid)      # v2 library: cancel_order (there is no .cancel)
            except Exception as e:      # noqa: BLE001
                cancel = f"cancel failed: {type(e).__name__}"
        return {"posted_ms": posted_ms, "post_resp": resp, "order_id": oid, "seen_on_exchange": seen, "cancel": cancel}
