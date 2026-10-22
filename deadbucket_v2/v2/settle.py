"""Settle paper decisions once their market has resolved; keep data/ledger.csv; one Telegram line."""
from __future__ import annotations

import csv
import json
from collections import defaultdict

import requests

from . import alerts, config, rules


def main(quiet: bool = False) -> None:
    if not config.DECISIONS_FILE.exists():
        print("no decisions yet")
        return
    s = requests.Session()
    rows = [json.loads(x) for x in config.DECISIONS_FILE.read_text(encoding="utf-8").splitlines() if x.strip()]
    changed, settled, pnl_total, wins, losses = 0, [], 0.0, 0, 0
    for d in rows:
        if d.get("outcome") is not None:
            continue
        try:
            r = s.get(f"{rules.GAMMA}/markets/{d['market_id']}", timeout=20)
            if not r.ok:
                continue
            mk = r.json()
        except (requests.RequestException, ValueError):
            continue
        if not mk.get("closed"):
            continue
        op = mk.get("outcomePrices")
        op = json.loads(op) if isinstance(op, str) else (op or [])
        if len(op) != 2:
            continue
        no_won = float(op[1]) == 1.0
        shares, cost = d.get("paper_shares") or 0.0, d.get("paper_cost") or 0.0
        d["outcome"] = "NO_WON" if no_won else "NO_LOST"
        d["paper_pnl"] = round(shares * (1.0 if no_won else 0.0) - cost, 4)
        changed += 1
        settled.append(d)
    if changed:
        config.DECISIONS_FILE.write_text("".join(json.dumps(d) + "\n" for d in rows), encoding="utf-8")
        new = not config.LEDGER_FILE.exists()
        with config.LEDGER_FILE.open("a", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["decided_utc", "mode", "city", "kind", "slug", "label", "tenths_c", "margin_tenths",
                            "feed_lag_s", "no_best_ask", "paper_shares", "paper_cost", "outcome", "paper_pnl", "actionable"])
            for d in settled:
                w.writerow([d["ts"], d["mode"], d["city"], d["kind"], d["slug"], d["label"], d["tenths_c"],
                            d["margin_tenths"], d["feed_lag_s"], d.get("no_best_ask"), d.get("paper_shares"),
                            d.get("paper_cost"), d["outcome"], d["paper_pnl"], d["actionable"]])
    for d in rows:
        if d.get("outcome"):
            pnl_total += d.get("paper_pnl") or 0.0
            wins += d["outcome"] == "NO_WON"
            losses += d["outcome"] == "NO_LOST"
    by_city = defaultdict(float)
    for d in rows:
        if d.get("outcome"):
            by_city[d["city"]] += d.get("paper_pnl") or 0.0
    lost = [f"{d['city']} {d['kind']} {d['slug'][-15:]} {d['label']} reading {d['tenths_c']}" for d in rows if d.get("outcome") == "NO_LOST"]
    text = (f"settle: {changed} newly settled; all time {wins} NO won / {losses} NO lost, paper P&L ${pnl_total:+.2f} "
            f"(stake ${config.STAKE_USD}/kill)" + (f"\nLOST: " + "; ".join(lost[:5]) if lost else ""))
    print(text)
    if not quiet:
        alerts.alert("settle_" + str(len(rows)), text, dedup_hours=0.1)


if __name__ == "__main__":
    main()
