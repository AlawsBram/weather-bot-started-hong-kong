# Log check 2026-10-23 15:10 UTC - what the v2 and v3 logs say

Both bots are running (DRY) on twapvm. The 06:09 UTC restart of both was our redeploy of the labelled alerts; the VM has not rebooted (up 25 days).

## 1. dead-bucket v2 (50 cities, METAR) - a day-window BUG, dry results so far are INVALID

Numbers from `~/deadbucket_v2/data/decisions.jsonl` (489 kills logged, Oct 22 07:47 -> Oct 23 15:08 UTC):

| | count |
|---|---|
| kills whose METAR really fell inside the market's local day | 270 |
| kills attributed to the WRONG day (reading from the previous local evening) | **219 (45%)** |
| "actionable" paper trades (NO <= 97c with depth) | 8 - **all 8 are wrong-day kills** |
| settled so far | 11 Wellington NO_WON - all wrong-day kills that won by luck |

Cause: the rules' METAR URL changed convention between batches. Oct 23 markets: `date=<local midnight>&hours=27`.
Oct 24+ markets: `date=<local midnight + 3 h>&hours=30`. `v2/rules.py` always subtracts 3 h, so every Oct 23 window
starts and ends 3 h early (Helsinki 18:00Z instead of 21:00Z, NYC 01:00Z instead of 04:00Z, Seoul 12:00Z instead of 15:00Z).
Robust rule that fits both: `window_start = date - hours + 3 h`, `window_end = start + 24 h`; cross-check with the station's time zone.

Examples of false kills the bot thought were cheap (each one would have been a real-money loss or a coin flip in live mode):
- Helsinki Oct 23 "11C" called dead by a 12.0C METAR at 21:20 local on Oct 22 -> NO ask 42c. Market now prices 11C at 97% YES.
- NYC Oct 23 "60-61F" called dead by 62F at 22:51 local on Oct 22 -> NO ask 38c. Market now 66% YES.
- Toronto, Paris, London, Istanbul, Seoul, Los Angeles: same pattern (evening reading of the day before).

Honest v2 result on the 270 correct kills: NO ask was >= 98c or there was no NO seller at all in every single one.
Feed delay (first sight minus observation stamp): median 5.6 min, p90 9.7 min, n=1,869 routine reports. 4 "problem" events are stale May markets with no tokens (harmless). METAR feed timeouts: 1 per ~8 h.

## 2. HK dead-bucket v3 - works as designed, and shows exactly who is faster

Reconcile Oct 22: official max 30.7 -> 30; text page, both CSVs all matched; text page median delay 7.4 min; rhrread is HOURLY whole degrees (useless as a trigger).
Polls: 24,197 today, text page every 2 s inside the window; fetch time 0.5-0.85 s by day (0.24 s on Oct 22 - VM load 2.5).

Two kills today, both already swept when we looked:

| bucket | reading | HKO page Last-Modified | our first sight | bots' sweep (public tape) | NO at sweep | NO when we looked |
|---|---|---|---|---|---|---|
| 30C | 13:20 (31.0) | 13:26:33 | 13:26:53.9 | 13:26:47-48 (3 trades) | 98.7-99.6c | 99.9c, depth 0 |
| 31C | 14:30 (32.1) | 14:36:34 | 14:37:02.8 | 14:36:54 (1,244 sh @ 83c + 196 sh @ 68c, resold @ 99c 2 s later) | 40c -> 99c | none left |

So: HKO wrote the page at :33/:34, the fast bots traded 14-20 s later, and our polls kept getting "304 not modified" for 20-28 s
after Last-Modified (304 at 14:36:59.9, new page at 14:37:02.8). We are reading a stale CDN copy. The bots beat us by 6-9 s;
they are NOT faster than the CDN by magic - they are ~20 s behind Last-Modified too, but ahead of our cached edge.
The 31C kill was worth ~$0.17-0.60/share to whoever was first (one wallet made ~$210 in one trade).

## 3. v1 HK weather bot (forecast models) - normal
hko_local_forecast feed failed 14 rounds (07:31-08:30 UTC) then recovered; 14:15 UTC run posted paper trades for Oct 24 as usual.

## What to do next (not done - waiting for your go)
1. Fix the v2 window (one function in `v2/rules.py` + time-zone cross-check + test), archive the wrong-day decisions/ledger files (rename, not delete), restart v2 DRY.
2. v3 speed experiment: add a cache-busting poll (query string / no-cache header) as a 6th "source" and measure first-sight minus Last-Modified against the current one. If it closes the 20-28 s gap we are ahead of today's sweep.
