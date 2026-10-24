# Log check 2026-10-24: why you saw nothing happen

> Plain language, a 15 year old can follow it. Every number comes from the files named.

**Short answer:** v3 was alive all day but could not see a single dead bucket, because of a bug: HKO's 00:00 reading still shows *yesterday's* maximum, and v3 took that as *today's* starting point. Yesterday (Oct 24 HKT) was cooler than the day before (31.9 vs 32.3), so no bucket could ever "die" and the bot stayed silent. Fixed and restarted 2026-10-24 19:12Z, still in live mode with the same caps. Even without the bug, no order would have gone out on Oct 24: every dead bucket was already priced above the 0.97 ceiling before we could see it.

## 1. What the bot did (VM `twapvm`, `~/hk_deadbucket_v3`)

| check | result |
|---|---|
| service | active since 2026-10-23 16:07:51Z, 0 crashes, heartbeat fresh |
| KILL file | absent (orders allowed) |
| polls on Oct 24 HKT | 34,820 (`polls-2026-10-24.csv`) |
| readings seen | 144 of 144 ten-minute stamps (`readings-2026-10-24.csv`) |
| kills recorded | **0** (no `kills-2026-10-24.csv`, `decisions.jsonl` unchanged since Oct 23) |
| live orders | 0 |
| Telegram | only the daily summary at 00:00 HKT ("0 dead buckets yesterday"), which was wrong, not quiet |

## 2. The bug

HKO's text page shows the "maximum since midnight". At the 00:00 stamp that number is still the whole previous day's maximum; the first reading of the new day is 00:10.

What `readings-2026-10-24.csv` shows:

| seen (HKT) | stamp | max since midnight | whole degree |
|---|---|---|---|
| 00:07:55 | 00:00 | 32.3 (= Oct 23's official max) | 32 |
| 00:17:08 | 00:10 | 28.3 | 28 |
| 11:36:35 | 11:30 | 29.1 | 29 |
| 12:27:11 | 12:20 | 30.1 | 30 |
| 12:57:48 | 12:50 | 31.0 | 31 |
| 23:57:19 | 23:50 | 31.9 | 31 (official max 31.9, settles as 31) |

v3 keyed the day by the stamp's date, so the 00:00 row became Oct 24's baseline: "today's max is already 32". A bucket dies only when the max climbs *above* the baseline. 28, 29, 30, 31 never beat 32, so the three real kills (28°C dead at 11:36, 29°C at 12:27, 30°C at 12:57) were never noticed. The same thing had already started for Oct 25: the 00:00 row (31.9) set the baseline to 31 while the real reading was 26.2.

Why Oct 23 looked fine: Oct 22 ended at 30.7, so the wrong baseline was 30 and the 31 and 32 readings still produced the two kills we saw. It only worked because the day was warmer than the day before.

The daily reconcile had the same blind spot: its Oct 23 report said "24C to 30C first seen at 00:04" (those were Oct 22's values).

**Fix** (deployed 19:12Z, tests 8/8):
- `v3/sources.py`: new `reading_day()`: a 00:00 stamp belongs to the day that just ended.
- `v3/bot.py`: `on_trigger` uses it, so the first reading of a day is the 00:10 one.
- `v3/reconcile.py`: same attribution, and it also reads the next day's versions file where the 00:00 row is filed.
- `tests/test_v3.py`: a replay of Oct 24 (32.3 at 00:00, 28.3 at 00:10, 29.1 at 11:30, 31.0 at 12:50) must kill 28°C, 29°C and 30°C.

After the restart the bot's baseline came from the 03:10 HKT reading (26.2), so today's kills will fire as the temperature rises.

Checked and clean: the 567-kill backtest set (`hk_dead_bucket_test`) has no 00:00 stamps (median 3 kills per day), so the trade-tape backtest and its "$14 a day" verdict are not affected.

## 3. What the market did at the three missed kills (public trade tape + 1-minute price history)

Scratch script `tape_oct24.py`, output `tape_oct24.json`, both in the session scratchpad (`oct24` folder).

| dead bucket | HKO stamp | our first sight | NO price before the stamp | NO buys between stamp and our sight | NO after our sight |
|---|---|---|---|---|---|
| 28°C | 11:30 | 11:36:35 | 0.9995 all morning, no trades at all | none | none |
| 29°C | 12:20 | 12:27:07 | 0.99 from 12:01, 0.999 by 12:18 | none | 0.999, no trades |
| 30°C | 12:50 | 12:57:14 | 0.97 to 0.985 from 12:22 | 12 buys at 0.991 to 0.999, the big ones at 12:57:02 (472 + 94 + 105 shares) | 0.997 to 0.999 |

Read it like this: the market had already written off 28 and 29 before the readings existed (the forecast said 31). For 30, people were paying 0.97 to 0.985 for NO half an hour *before* the 12:50 reading made it certain, and the bots that finished it off at 12:57:02 were 12 seconds ahead of our first sight. With the 0.97 ceiling, v3 would have reported "nothing to buy" three times. So the bug cost no money on Oct 24; it cost us the three log entries that would have proven the live order path.

## 4. The cache-bypass source did not help

`text_nocache` (same page, no conditional headers, unique query string) was added on Oct 23 to beat the stale CDN copy. Over 163 stamps on Oct 24 and 25 it saw the new reading a median **0.5 s later** than the normal `text_hko` poll (earlier by more than 2 s on 39 stamps, later by more than 2 s on 64). At the three kills it was 0 to 26 s behind. The CDN gap we measured on Oct 23 is not fixed by bypassing the cache; the page simply appears when HKO publishes it, 6.6 to 7.2 min after the stamp. The CSV came 52 to 84 s after the page, as before.

## 5. Where things stand

- v3 LIVE on twapvm since 2026-10-24 19:12Z with the fix, same caps ($10 per order, 3 per HKT day, NO 0.50 to 0.97, FOK, `KILL` file stops it). Account 2 balance untouched ($32.41).
- v2 (50 cities, dry) unchanged. The v2 code keys days by the station's local calendar day from the METAR time, not by HKO's since-midnight stamp, so it does not have this bug.
- Next check: after ~09:00 HKT on Oct 25 (01:00Z), `kills-2026-10-25.csv`, `decisions.jsonl` and the Telegram kill messages should show today's kills. If one of them has a NO ask at or below 0.97, the first real $10 order goes out and the response text lands in `decisions.jsonl` under `live`.
