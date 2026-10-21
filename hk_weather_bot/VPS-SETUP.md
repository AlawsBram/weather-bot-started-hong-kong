# HK weather bot on the VPS — what runs, when, and what to look at

Paper trading only. No wallet, no keys, no orders. Set up 2026-10-21 (HKT) on the GCP VM
`twapvm` (Netherlands), in `~/#13 HK weather bot (Hong Kong max temperature)/hk_weather_bot`
(shortcut `~/hkbot`). The original `hk_weather_bot.zip` is kept untouched beside this folder.

## The schedule (all times Hong Kong; Nairobi is 5 hours behind)

| Job | When (HKT) | Nairobi | What it does |
|---|---|---|---|
| `snapshot` | every :00 and :30, plus 11:40 and 16:40 | — | Logs every source, the full order books, page numbers, screenshots, cross-checks |
| `run-today` | 11:50 and 14:20 | 06:50, 09:20 | Same-day prediction with the live "max so far" (the best window) |
| `run-tomorrow` | 22:15 | 17:15 | Predicts tomorrow and places the paper trades |
| `settle` | 10:20, 15:20, 20:20 | 05:20, 10:20, 15:20 | Scores finished days once Polymarket has resolved them |
| `calibrate` (+ backtest) | Sunday 09:30 | Sunday 04:30 | Re-measures each source's bias and error size over 45 days |
| `backup` | 03:10 daily | 22:10 | Copies `data/ledger.sqlite` to `data/backups/` |
| `health` | every 15 min | — | Alerts if snapshots stop or the disk gets low |

HKO's 9-day bulletin updates around 11:30 and 16:30 HKT, hence the extra snapshots at 11:40 / 16:40.

## Where the numbers come from (and how they are checked)

| What | Number from (data feed) | Checked against (what the page shows) |
|---|---|---|
| HKO 9-day forecast | HKO open-data `fnd` | the 9-day page's own data file every round + the visible page every 3 h |
| ECMWF / GFS / ICON | Open-Meteo at the HKO station, hourly → daily peak | — (Windy shows the same models; its data isn't public) |
| HKO max since midnight | HKO regional CSV (row "HK Observatory") | — |
| Official result (resolution) | HKO **Daily Extract** data file | the visible Daily Extract table every 3 h; CLMMAXT too once HKO fills it |
| Polymarket prices | full order book from the CLOB API | the "Buy Yes / Buy No" cents shown on the event page, every round |

If a feed and its page disagree, you get a Telegram alert. HKO numbers must match exactly.
Prices must disagree by more than 3 cents on two buckets, two rounds in a row (prices move).
If the HKO forecast feed breaks, the bot uses the page's data file, then the page itself, and tells you.

**Not automated, on purpose:** Google Weather (CAPTCHAs for automated visits, and its terms
forbid scraping) and Windy (non-public data, same models as Open-Meteo). The browser here is
an ordinary headless Chrome at a low rate. It does not disguise itself or get past CAPTCHAs.
To score Google anyway, log it by hand (see below) for a couple of weeks.

## Folders

```
data/snapshots/2026-10-21T14-30HKT/   one folder per round
    snapshot.json                     every number, feed status, page reads, cross-checks
    market_<date>.json.gz             raw Polymarket market data + full order books
    polymarket_<date>.jpg             screenshot (every round)
    hko_9day.jpg, hko_daily_extract_<yyyy-mm>.jpg   screenshots (every 3 h + after bulletins)
data/ledger.sqlite                    forecasts, quotes, cross-checks, predictions, trades, results, scores
data/calibration.json                 measured bias / error size per source (weekly)
data/backups/                         daily ledger copies
data/state/alerts.log                 every alert, sent or suppressed
logs/<job>.log                        output of each scheduled job
```

Nothing is ever deleted automatically. A round is about 0.4 MB (1.2 MB when the HKO pages are
included), roughly 25 MB a day.

## Telegram

Alerts go to the weather topic (thread 1914) of the usual group, from the same bot.
- The same problem is sent at most once every 6 hours, and at most 6 alerts an hour overall.
- When a problem clears you get one "✅ ... working again" line.
- Feed problems alert after 2 failed rounds in a row (one hour), not on a single blip.
- Daily reports: each `run` (the paper trades) and each `settle` (scores and P&L).

## Commands (on the VPS: `cd ~/hkbot && .venv/bin/python -m hkbot <command>`)

| Command | Use |
|---|---|
| `report` | Versions side by side: Brier vs market, P(winner), paper P&L |
| `sources` | How accurate each logged source has been, and its weight in v2 vs v3 |
| `manual google 2026-10-22 31` | Log Google Weather's max for a day by hand. It is then scored like any source. |
| `snapshot --force-pages` | One round now, including the HKO pages |
| `notify-test` | Check Telegram works |

`sudo systemctl list-timers 'hkbot-*'` shows the schedule. Each job's output is in `logs/<job>.log`.

## Changes to the original bot (each one is a separate commit)

1. **Fixed: the "max since midnight" was never read.** HKO labels the row "HK Observatory".
2. **Fixed: settle and calibrate had no October readings.** CLMMAXT was empty for the current
   month, so they now read the Daily Extract data file. That is the exact resolution source the
   market names. CLMMAXT is kept as a second check.
3. **Real costs:** weather markets charge a taker fee of 0.05 × p × (1 − p) per share. Paper fills
   now walk the order book for the $10 stake and add the fee. `pnl` is that honest number, and
   `pnl_quoted` keeps the old top-price-no-fee number beside it. Trade *decisions* are unchanged.
4. **New version `v3_auto_weights`**: v2 with one change. The hand-written trust numbers fade out
   as measured days arrive, so each source is trusted only by its measured error size. v1 and v2
   are untouched.

## Going live: not yet

Rule (from the plan): 30–60 settled days, and a version must beat the market's Brier score AND
show positive paper P&L after real costs. Then tiny stakes ($1–5) with a daily cap. Polymarket
blocks trading from some countries, including the US. Reading data works from this VPS, but
check that trading is allowed from where you live and from the VPS before going live.
