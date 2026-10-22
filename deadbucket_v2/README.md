# dead-bucket v2 — many cities (dry run first)

HighTempTation's trick, for every daily-temperature market on Polymarket except Hong Kong (HK keeps its
own bot next door in `hk_weather_bot/`). A bucket is **dead** when the day's reading has already gone past
it (a daily HIGH can only rise, a daily LOW can only fall). Dead = NO is worth $1.00 for certain.

## What it does, every 5 seconds
1. **One request** to the resolution feed itself (aviationweather.gov METAR, all ~50 stations at once).
2. Applies each market's own rules: routine reports only (no SPECI), tenths from the T-group, °F by
   formula, round half up, station local day from the rules URL.
3. A bucket that just became impossible = a **kill**: read its NO order book, record a paper fill
   (dry), or a probe / live order, in `data/decisions.jsonl`. Feed delay per report → `data/feed_lag.csv`.
4. Every 6 h it re-reads **every market's Rules text** (`python -m v2 discover`), keeps
   `data/stations.json`, and sends a Telegram alert if a city's station or source changes.
   `python -m v2 inspect --today` opens the real web page in headless Chrome and checks the station the
   page shows against the API.
5. `python -m v2 settle` (timer, every 6 h) marks each decision NO_WON / NO_LOST once the market closes.

## Modes
- `dry` (default, deployed): nothing is sent to the exchange; paper $10 per kill.
- `probe`: proves the order path **without buying**: a post-only NO bid at 50¢ (5 shares, the minimum),
  read back from the exchange, then cancelled. ($0.10 orders cannot be used: below the 5-share minimum
  they are rejected before reaching the book, which only proves the API answered.)
- `live`: FOK taker buy of NO at the best ask, capped per order and per day, needs `LIVE_TRADING=1`.
  Both need `deploy/.env` (POLY_PRIVATE_KEY, POLY_FUNDER, POLY_SIGNATURE_TYPE) — never committed.

Kill switch: create a file named `KILL` in this folder.

## Files
`v2/rules.py` discovery + rules extraction · `v2/buckets.py` exact dead test · `v2/metar.py` feed ·
`v2/book.py` NO book + fees · `v2/bot.py` loop · `v2/execution.py` orders · `v2/settle.py` ·
`tests/` (`python -m pytest -q tests`) · `deploy/` systemd units.

VM: twapvm `~/deadbucket_v2`, runs on `~/hkbot/.venv` (requests + playwright already there).
