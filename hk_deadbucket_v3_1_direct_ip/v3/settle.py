"""Settle v3 paper decisions once the HK market resolves; ledger + one Telegram line."""
from __future__ import annotations

import csv
import json

import requests

from . import alerts, config

GAMMA_MARKET = "https://gamma-api.polymarket.com/markets/{id}"


def main(quiet: bool = False) -> None:
    if not config.DECISIONS_FILE.exists():
        print("no decisions yet")
        return
    s = requests.Session()
    rows = [json.loads(x) for x in config.DECISIONS_FILE.read_text(encoding="utf-8").splitlines() if x.strip()]
    changed, settled = 0, []
    for d in rows:
        if d.get("outcome") is not None:
            continue
        try:
            r = s.get(GAMMA_MARKET.format(id=d["market_id"]), timeout=20)
            mk = r.json() if r.ok else None
        except (requests.RequestException, ValueError):
            mk = None
        if not mk or not mk.get("closed"):
            continue
        op = mk.get("outcomePrices")
        op = json.loads(op) if isinstance(op, str) else (op or [])
        if len(op) != 2:
            continue
        no_won = float(op[1]) == 1.0
        shares, cost = d.get("paper_shares") or 0.0, d.get("paper_cost") or 0.0
        d["outcome"] = "NO_WON" if no_won else "NO_LOST"
        d["paper_pnl"] = round(shares * (1.0 if no_won else 0.0) - cost, 4)
        settled.append(d)
        changed += 1
    if changed:
        config.DECISIONS_FILE.write_text("".join(json.dumps(d) + "\n" for d in rows), encoding="utf-8")
        new = not config.LEDGER_FILE.exists()
        with config.LEDGER_FILE.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["decided_utc", "mode", "day", "label", "max_c", "publish_lag_s", "no_best_ask", "paper_shares", "paper_cost", "outcome", "paper_pnl", "actionable"])
            for d in settled:
                w.writerow([d["ts"], d["mode"], d["day"], d["label"], d["max_c"], d["publish_lag_s"], d.get("no_best_ask"),
                            d.get("paper_shares"), d.get("paper_cost"), d["outcome"], d["paper_pnl"], d["actionable"]])
    done = [d for d in rows if d.get("outcome")]
    pnl = sum(d.get("paper_pnl") or 0 for d in done)
    wins = sum(d["outcome"] == "NO_WON" for d in done)
    text = (f"settle: {changed} newly settled; all time {wins} NO won / {len(done) - wins} NO lost, paper P&L ${pnl:+.2f} "
            f"on {len(done)} dead buckets (stake ${config.STAKE_USD}/kill, only when NO <= {config.MAX_NO_PRICE})")
    print(text)
    if not quiet and changed:
        alerts.alert("settle_" + str(len(done)), text, dedup_hours=0.1)


if __name__ == "__main__":
    main()
