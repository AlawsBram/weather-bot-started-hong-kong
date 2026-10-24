# What is new in the redownloaded arena workspace (2026-10-23)

> I'm explaining this in simple language a 15 year old can understand.

**Zip:** `C:\Users\Administrator\Downloads\Compressed\workspace-01a16957-3631-72f2-8833-1b80ad02fc54.zip` (122.7 MB, downloaded 2026-10-23 21:35)
**Extracted to:** `C:\Users\Administrator\Downloads\workspace-01a16957-3631-72f2-8833-1b80ad02fc54\` (new folder, next to the old one)
**Old copy (kept, untouched):** `C:\Users\Administrator\Downloads\workspace-01a1601e-25f4-7320-aec8-d79693f82f83\` (extracted 2026-10-22)
**Method:** every file in both trees hashed (SHA-1) and compared. Full lists in `workspace diff old vs new (file lists).json`.

| | files |
|---|---|
| old workspace | 6,354 |
| new workspace | 8,635 |
| added | 2,315 |
| removed | 34 |
| changed | 15 |
| identical | 6,305 |

No `.env`, key or wallet file appeared anywhere in the added or changed files. The `polymarket secrets` folder is byte-identical to before.

## 1. Added (the work from the arena session you pasted)

| What | Where | Size |
|---|---|---|
| **Moon Dev stream rules test** (188 HK days, 2,068 bucket-days) | `WEATHER-RULES-FROM-MOONDEV-STREAM.md` + `weather-rules-test/` (fetch_data.py, test_rules.py, test_rules2.py, `data/tidy.csv`, `data/events/` 190 days, `data/prices/` 2,068 hourly histories, `results/refined.json`, `results/rules_test.json`) | 2,276 files, 14 MB |
| **Speed audit of hk_weather_bot v1** + WebSocket spec | `SPEED-AUDIT-WEATHER-BOT.md`, `weather-rules-test/fastpath.py` | |
| **Two bugs, one backtest, RBI correction** (the pasted session) | `BUGS-AND-BACKTEST-RBI.md`, `weather-rules-test/lead_lag_backtest.py`, `results/lead_lag.json` (79 days), `no_lookahead.py`, `data/nla_cache.json` (21 days of HKO archive captures), `logs/no_lookahead.log`, `reaction_time.py` + `results/reaction_time.json`, `source_race.py` + `.log`, `cadence_monitor.py` + `cadence.log` | |
| **Day-window guard** (73 stations) | `weather-bot-started-hong-kong/hk_weather_bot/hkbot/daywindow.py` | 12 KB |
| **Live recorder** (HKO text page + CSV polls with the live Polymarket book, websocket) | `.../hkbot/recorder.py`, `recorder_report.py`, `RECORDER.md`, `data/records/records-2026-10-23.ndjson` (184 polls), `ticks-2026-10-23.ndjson` (3,718 book ticks) | 2.8 MB |
| requirements.txt of hk_weather_bot | adds `websockets>=13.0` and `coincurve>=20.0` | |
| New transcript | `YouTube Transcripts/NoteGPT_Transcript_Why 99% of Traders Fail (And How a Trading Bot Can Save You).txt` (also copied to `uploads/`) | 120 KB |
| `README.md` + a top-level `.git` | the workspace is now a clone of our public repo `entire-workspace-lm-trading` (commits d04f12a, a32afd4) | 27 MB pack |

## 2. Removed

- `polymarket-decompiled-2.8.5-README.md`, `-RESOURCES.zip`, `-SOURCES.zip`, `-SPLITS.zip` (the decompiled app is gone from the workspace, matching the repo README rule).
- The nested `weather-bot-started-hong-kong/.git` (28 files) is gone because the whole workspace is now one git clone.
- `.sudo_as_admin_successful`, and `uploads/NOTEGP~1.TXT` (a short-name duplicate).

## 3. Changed (15 files)

- 12 CSVs under `maker_lab/history`, `s1_harness`, `truth_lab`, `ui_lab` changed **only in line endings** (CRLF to LF, git autocrlf). Same rows, no data change.
- `uploads/Transcript of leaked WeChat video call.txt`: line endings only.
- `uploads/NoteGPT_Transcript_how do prop firm bots actually work.txt`: **real content change**, 100 lines became 187. The old file started with the Oct 1 prop-firm story; the new one starts with the Dec 12 HyperLiquid whale story, so it is a different (longer) transcript under the same name.
- `weather-bot-started-hong-kong/hk_weather_bot/requirements.txt`: the two new libraries above.

## 4. What the new documents say, and where they agree or disagree with our own work

### 4a. WEATHER-RULES-FROM-MOONDEV-STREAM.md (new, 188 HK days)
All NO EDGE, same direction as every HK test we ran:
- The 11-bucket ladder costs $1.034 the evening before (overpriced 3.4%), and by noon every bucket is outside 3-97c.
- Tail NO sniper: bottom tail pays 0.1c, top tail won 14.4% of days (26.6% in spring). Selling NO on the top two ranks: -11.6%, 7 losing days in a row.
- 20%-discount "stink bids": 17.8% fill, 48.9% win rate when filled, -7.85%. The fill is the bad news.
- His exact 4-leg recipe: 76.5% win rate, -3.58% over 378 legs.
- Market calibration on live buckets: 23.9% implied vs 23.2% real. Nothing to harvest.

### 4b. SPEED-AUDIT-WEATHER-BOT.md
Audits **v1** (`hk_weather_bot`), not v3. Its findings: v1 only looks 3 times a day, 91% of its 2.14 s is waiting on other servers, there is no order code at all in v1, and a Rust rewrite would gain 0.06%. The daemon it proposes ("one asyncio process, websocket books, HKO poller, pre-signed orders, recorder") is essentially what `hk_deadbucket_v3` already is on twapvm. Two things worth taking: `pip install coincurve` (it measured order signing 7.08 ms to 0.66 ms) and the websocket gotchas (silent freeze, re-subscribe on reconnect, tick-size change events).

### 4c. BUGS-AND-BACKTEST-RBI.md (the pasted session)
- **Lead/lag backtest, 79 days:** the winning bucket crossed 50c before HKO's observation existed on 34% of days, inside the 10-min publish window 9%, after it 57% (median 112 min later). Conclusion: no speed edge for HK. Agrees with our trade-tape backtest. One caution on reading it: "market led" there means the winner was already above 50c before the max reached it, which is mostly the forecast favourite being priced in, not proof that someone had the observation first.
- **Helsinki:** the effect is right (the 12.0 C reading at 2026-10-22 18:20Z, 21:20 Helsinki, was counted for the Oct 23 market and killed 11 C, which then won). The **cause it names is wrong**: our v2 does not run on Hong Kong time, it runs in UTC, and the real bug was `window = rules-URL date - 3 h` (the URL convention changed on Oct 24+ markets). Both the real buggy window (start 18:00Z) and their guessed one (start 16:00Z) contain 18:20Z, so the symptom cannot tell them apart; the code can. v2 was already fixed on 2026-10-23 with `CITY_TZ` + `local_day_window` (station-local calendar day, URL only cross-checked, 21 tests). Their `daywindow.py` lands on the same rule. Its 73-station table is keyed by guessed names ("new-york", "sf"), not Polymarket slugs ("nyc", "san-francisco"), so do not merge it blindly; our 51 slugs are the ones the markets actually use.
- **The 31 C jump:** they say it cannot be decided from one event; agrees with us (we showed the Sep 19 move came 3 min before the number was publishable).
- **"Buy every bucket the max walks into" (no_lookahead.py):** 17 days, +2.30c per entry, flagged by them as probably an illusion because half the fills were 0.00-0.02 midpoints. We finished that check, see section 5.

### 4d. RECORDER.md / recorder.py
A read-only recorder of every HKO poll with the live book, run from a US-West sandbox on 2026-10-23 (184 polls). It confirms what v3 measured: the text page is first, the CSV 34-86 s later, and the page briefly served an older stamp for 4 s (stale CDN copy). v3 on twapvm already logs the same thing every poll, so there is nothing to deploy from it.

## 5. The open question from the pasted session, answered: the +2.30c dies

Their rule: each time HKO's max-since-midnight enters a new integer bucket, buy YES on that bucket and hold to settlement. Their fill time was `archive capture - 7.5 min`, which is about 1 min after the stamp, i.e. **about 6 minutes before the text page shows the reading** (we measured first sight at 6.5-7.4 min after the stamp). That is look-ahead. Also their first entry each day used the 00:00 stamp, which belongs to the day just ended.

Rerun on the same 21 cached days (`nla_realfill.py`, output `backtest from 2026-10-23 to 2026-04-15 - HK walk-every-bucket YES buys, arena no_lookahead rerun with real fills.csv`), 85 entries, 00:00 stamps dropped, taker fee 0.05*p*(1-p) included:

| Variant | entries | win rate | median price | total | per entry |
|---|---|---|---|---|---|
| A. their fill time (capture - 7.5 min), midpoint | 85 | 24.7% | 0.08 | +187c | **+2.21c** (reproduces their number) |
| B. fill when the text page actually showed it (stamp + 7.5 min), midpoint | 85 | 24.7% | 0.08 | -146c | **-1.72c** |
| C. same as B, only entries priced >= 0.20 | 32 | 56.2% | 0.65 | -228c | -7.13c |
| D. real fill = first real YES buy on the tape within 15 min of sight, all | 71 | 28.2% | 0.17 | -162c | -2.28c |
| E. real fill, only entries >= 0.20 | 31 | 58.1% | 0.64 | -147c | **-4.73c** |

- 12 of 21 days lose. The winning days are the ones where the max stopped early and the bucket was bought for pennies; the losing days are the ones where the max kept climbing through buckets bought at 40-80c.
- In the fillable range (E) the hit rate (58%) is below the price paid (64c). Hit rate = price minus costs: the market already knows.
- 14 of the 85 entries had no real YES trade at all in the 15 minutes after sight; all of them were the 0.00-0.03 "midpoints" that nobody can trade.
- 66 of 85 entries already had YES buys on that bucket between the stamp and our first sight: the bucket was being traded before the page showed the number.

**Verdict:** the +2.30c was the fill-time look-ahead plus untradeable midpoints. With honest timing and real fills the rule is -2 to -5c per entry. NO EDGE, consistent with the tape backtest (first-mover prize median $14/day, split across 10+ wallets).

## 6. Nothing in the new zip changes what is running
- v3 stays LIVE on twapvm as set on 2026-10-23 15:53Z (caps $10/order, 3/day).
- v2 stays dry with the day-window fix already deployed.
- Possible cheap follow-up (not done): `pip install coincurve` into the hkbot venv and re-measure v3's signing time; it needs a restart of a live bot, so it is the user's call.
