# HK dead-bucket v3.1 (direct HKO server IP trigger)

Copy of v3 with ONE change: the HKO text page is also fetched straight from each of HKO's two web servers (203.129.88.115 and 103.30.70.62, hostname kept for TLS/SNI and Host), and the first source to show a new 10-minute stamp fires the kill logic (sources.TRIGGERS, Bot._maybe_trigger, one fire per stamp). Reason: v3 saw the page a median 36 s after its Last-Modified (2026-10-25/26, n=276) while the fastest wallets swept the dead bucket 17-18 s before us; in the 2026-10-26 source race the direct IP showed new readings first. Every reading and kill records which source fired (trigger_src). Folder `~/hk_deadbucket_v3_1_direct_ip`, units `hk-deadbucket-v3.1-direct-ip*`, Telegram label 'HK dead-bucket v3.1 (direct HKO server IP trigger)'. Everything else (window polling, book read, paper/probe/live modes, caps, KILL file) is v3.

---- v3 README follows ----

# HK dead-bucket v3 (paper, Hong Kong only)

The three bots side by side:

| | v1 `hk_weather_bot/` | v2 `deadbucket_v2/` | **v3 `hk_deadbucket_v3/`** |
|---|---|---|---|
| Idea | forecast models vs market price (paper) | dead buckets in ~50 cities | **dead buckets in Hong Kong** |
| Trigger | scheduled runs (11:50, 14:20, 22:15 HKT) | aviationweather.gov METAR feed, every 5 s | **HKO text page**, the fastest public source, 2 s polling only in the 5.5–9.75 min publish window |
| Telegram label | 🌡️ HK weather bot v1 | 🌍 dead-bucket v2 | 🏙️ HK dead-bucket v3 |

v3 = everything the SPEED-GAP-TESTS report recommended: text-page trigger, windowed polling (~90% fewer
requests to HKO), every poll recorded, every source raced and reconciled against the official max,
the dead bucket's order book read the second it dies, orders pre-armed, paper fills settled.

## Files it writes (`data/`, HKT dates)
`polls-*.csv` every poll · `versions-*.csv` first sight of each new reading per source · `readings-*.csv` favourite bucket
prices + what is pre-armed at each reading · `kills-*.csv` the dead bucket's book the second it died · `decisions.jsonl` /
`ledger.csv` paper trades and their outcome.

## Commands
`python -m v3 run [--mode dry|probe|live]` · `python -m v3 settle` · `python -m v3 reconcile [--date YYYY-MM-DD]` · `python -m v3 status`
Kill switch: a file named `KILL` in this folder. Probe/live need `deploy/.env` (never committed) and `py_clob_client_v2`.

VM: twapvm `~/hk_deadbucket_v3`, runs on `~/hkbot/.venv`; services `hk-deadbucket-v3`, `hk-deadbucket-v3-settle.timer` (6 h),
`hk-deadbucket-v3-reconcile.timer` (10:00 HKT). It replaced the standalone `hk_live_recorder` (same measurements, no duplicate polling).

## Going live ($10 test) - the user does this, not the assistant
1. `cp deploy/.env.example deploy/.env && chmod 600 deploy/.env`, then append the three POLY_* lines from an existing bot env on the VM
   (e.g. `grep -E "^POLY_(PRIVATE_KEY|FUNDER|SIGNATURE_TYPE)=" ~/s1_harness/.env >> deploy/.env`). Nothing is printed.
2. Probe first (no money): set `V3_MODE=probe`, `sudo systemctl restart hk-deadbucket-v3`; the next kill posts a 50c x 5 NO bid, reads it back and cancels; the Telegram kill message shows the exchange's answer.
3. Live: set `V3_MODE=live` and `LIVE_TRADING=1`, restart. Each kill with NO <= 97c on the book sends ONE fill-or-kill buy of up to $10 (caps in .env); a miss costs nothing, a fill costs ~5c in fees.
4. Stop: `touch KILL` (no more orders, polling continues) or set `V3_MODE=dry` and restart.
Order library: `py_clob_client_v2` must import in `~/hkbot/.venv` (installed 2026-10-23).
