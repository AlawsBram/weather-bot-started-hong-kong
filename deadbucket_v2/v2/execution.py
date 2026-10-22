"""Order execution. DRY records what would have been bought. PROBE and LIVE need a key in deploy/.env
(POLY_PRIVATE_KEY, POLY_FUNDER, POLY_SIGNATURE_TYPE - same names as the S1/paired-maker bots) and
LIVE additionally needs LIVE_TRADING=1. Nothing here runs unless --mode probe/live is given.

PROBE = prove the whole order path without buying: post a NO bid far below the market (default 50c,
5 shares = Polymarket's minimum) on the dead bucket, read it back from the exchange, cancel it.
If the exchange accepts and shows it, the path works. (A $0.10 order cannot be used for this: the
minimum size is 5 shares, so it is rejected before it reaches the book, which only proves the
API answered - it does not prove the order would have been placed.)
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

    def buy_no(self, no_token: str, price: float, shares: float) -> dict:
        """LIVE: take NO up to `price` (fill-or-kill, so we never rest a stale order)."""
        from py_clob_client_v2 import OrderArgs, OrderType
        from py_clob_client_v2.order_builder.constants import BUY
        t0 = time.time()
        order = self.client.create_order(OrderArgs(token_id=no_token, price=round(price, 3), size=round(shares, 2), side=BUY))
        resp = self.client.post_order(order, order_type=OrderType.FOK)
        self.orders_today += 1
        return {"ms": round(1000 * (time.time() - t0)), "resp": resp}

    def probe(self, no_token: str, price: float, shares: float, hold_s: float = 3.0) -> dict:
        """Post-only resting bid far below market, verify it is on the exchange, cancel."""
        from py_clob_client_v2 import OrderArgs, OrderType
        from py_clob_client_v2.order_builder.constants import BUY
        t0 = time.time()
        order = self.client.create_order(OrderArgs(token_id=no_token, price=round(price, 3), size=round(shares, 2), side=BUY))
        resp = self.client.post_order(order, order_type=OrderType.GTC, post_only=True)
        posted_ms = round(1000 * (time.time() - t0))
        oid = (resp or {}).get("orderID") or (resp or {}).get("id")
        seen = None
        if oid:
            time.sleep(hold_s)
            try:
                seen = self.client.get_order(oid)
            except Exception as e:          # noqa: BLE001 - report, never crash the loop
                seen = f"get_order failed: {type(e).__name__}"
            try:
                cancel = self.client.cancel(oid)
            except Exception as e:          # noqa: BLE001
                cancel = f"cancel failed: {type(e).__name__}"
        else:
            cancel = "not posted"
        return {"posted_ms": posted_ms, "post_resp": resp, "order_id": oid, "seen_on_exchange": seen, "cancel": cancel}
