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
