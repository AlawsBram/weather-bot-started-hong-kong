# Log check 2026-10-27: did v3.1 (direct HKO server IP trigger) improve on v3?

> Plain language. Data: `~/hk_deadbucket_v3/data` and `~/hk_deadbucket_v3_1_direct_ip/data` on twapvm (versions, readings, kills, polls for 2026-10-27, 00:00 to 13:10 HKT), the public trade tape (`data-api.polymarket.com/trades`), and the gamma market list. Raw per-stamp table: `hk_deadbucket_v3_1_direct_ip\COMPARE 2026-10-27 - v3 vs v3.1 first sight of every HKO stamp (raw table, 00.00-13.10 HKT).txt`, script `tools_compare_v3_vs_v31.py` (copy on the VM as `~/compare_v31.py`). Checked at 13:20 HKT (05:20 UTC, 08:20 Kenya), both bots healthy, v3 live, v3.1 dry.

**Short answer:** yes, a real but modest improvement. v3.1 saw the new HKO reading before v3 on 58 of 80 stamps today (8 ties, 14 losses). Median gain 6.5 s, mean 12 s, and the time from the page's own `Last-Modified` to our first sight fell from 43 s (v3) to 31 s (v3.1) at the median. That is still not fast enough: the sweeper wallets traded 7 s and 14 s after `Last-Modified` on today's two kills. On one kill v3.1 arrived in the same second as the sweeper, on the other 22 s after. Neither kill had any money in it anyway (both buckets were at NO 0.99 since 00:10 because of the forecast), so no order was placed by v3 and v3.1 would not have placed one either.

## 1. Speed: v3 vs v3.1, every stamp today (80 stamps)

| | v3 (named page, keep-alive) | v3.1 (first of IP 203 / IP 103 / named) |
|---|---|---|
| seconds after `Last-Modified`, median | 43.4 | 30.5 |
| p10 / p90 | 25.7 / 78.7 | 18.9 / 60.8 |
| who saw the stamp first | 14 of 80 | **58 of 80** (8 ties) |
| gain v3 minus v3.1, median / mean | | **+6.5 s / +12.1 s** (p10 −2.1, p90 +38.9, worst −27.7, best +61.6) |
| daytime only, 08:00 to 13:10 HKT (32 stamps) | | median +6.6 s, mean +8.7 s, worst −13.0, best +34.3 |
| which source fired v3.1 | | IP 203: 30, named page: 31, IP 103: 19 |

Reading the table: on a good stamp (01:30, 02:50, 04:40, 05:20) v3.1 was 55 to 62 s ahead because v3's connection was stuck on a copy of the page that updated late. On the bad stamps (06:10, 09:10, 11:00) v3 happened to be on the fresh copy and v3.1's three sources were all on stale ones, 13 to 28 s behind.

Two things the table teaches that the Oct 26 race did not show:

- **It is not simply "IP 203 fast, IP 103 slow".** Each of the three sources won a share of the stamps (30 / 31 / 19). The same IP gave a fresh copy one minute and a stale one the next, and v3's named-page connection and v3.1's named-page connection (same hostname, same code) saw the same stamp up to 58 s apart. So there are more than two copies of the file behind those IPs, and which copy a connection lands on looks random. "First of several connections" is the right design; two IPs plus one name is just a small sample of it.
- **Polling faster would not help.** Both bots poll every 2 s inside the window (about 5,000 polls per text source today, almost all answered `304 Not Modified`, 0 errors on the IP sources). The 14 to 98 s spread is how long the server takes to serve the new file, not how often we ask.

## 2. The two kills today and what the market was doing

| dead bucket | HKO stamp | page `Last-Modified` | sweeper trade | v3.1 first sight | v3 first sight | NO ask at our sight |
|---|---|---|---|---|---|---|
| 27°C (max 28.0) | 11:10 | 11:16:46 | 11:16:53 (+7 s): 0xf18de4f3 took 337 + 900 NO at 0.998 | 11:17:15 (+29 s, via IP 103) | 11:17:30 (+44 s) | 0.999, nothing under 0.97 |
| 28°C (max 29.1) | 12:00 | 12:07:07 | 12:07:21 (+14 s): 0x2662228c took 459 NO at 0.991 | 12:07:20 (+13 s, via named page) | 12:07:21 (+14 s) | 0.998, nothing under 0.97 |

- Both buckets had been at NO 0.97 or higher since 00:10 HKT (the overnight forecast already said 29 to 30). The last trade under 0.97 was 24 shares at 0.960 (27°C, 10:47) and 30 shares at 0.960 (28°C, 11:56). There was no $100 prize in either; being first would have won nothing.
- Still useful as a timing test: on the 28°C kill v3.1 was in the same second as the sweeper (13 s vs 14 s after `Last-Modified`). On the 27°C kill the sweeper was 7 s after `Last-Modified` and v3.1 22 s behind them. So on a lucky stamp we are now level with them, on a normal stamp we are 10 to 20 s behind, and on a bad stamp 30 to 60 s behind.

## 3. The bucket that matters today: 29°C

At 13:10 HKT the max was 29.7 and the market had 29°C as the favourite (YES 0.80, NO 0.12 to 0.20). If the next readings cross 30.0, the 29°C bucket dies and NO on it goes from about 0.18 to 0.99, which is the roughly $0.80 per share move the whole strategy is built for. Both bots are armed on it (`armed_next_kill = 29°C`, `armed_no_ask 0.18` to `0.55` through the early afternoon). v3 is the live one and would buy up to $10 of NO at 0.97 or less if it sees the crossing first. The afternoon readings (13:20 onward) will show whether we get there before the sweepers. Nothing to do now but wait for them.

## 4. Verdict and what could still be squeezed

- **Improvement: yes, measured, about 13 s at the median.** v3.1 did exactly what it was built to do and nothing broke (0 errors on the direct-IP sources, correct kill labels, Telegram messages say "via text_ipXXX").
- **Enough to catch the sweepers: no, not yet.** They sit at 7 to 20 s after `Last-Modified`; v3.1 is at 19 s on a good stamp and 31 s at the median.
- **One more step that follows from today's data (not built, your call):** since each connection lands on a random copy of the file, open several keep-alive connections per IP (say 3 per IP plus the name, 7 in total) and fire on the first. The cost is 7 small conditional requests every 2 s inside the window only, still well under one request per second on average. Today's spread between connections (up to 58 s on the same stamp) says this is where the remaining seconds are. Whether it gets under the sweepers' 7 to 14 s is unknown until measured.
- The switch to make v3.1 the live one (and v3 dry) is still the paste-ready command from the Oct 26 check; it has not been run.
