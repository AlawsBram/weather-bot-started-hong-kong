# Source race 2026-10-26: which way of fetching the HKO text page shows a new reading first?

> Plain language. Data: `RACE 2026-10-26 - 10 ways of fetching the HKO page, every poll (22.00-23.00 HKT).csv` (5,723 polls, 5,670 OK, 53 errors), script `race.py` (session scratchpad, copy below in this folder as `race_hko.py` on twapvm `~/race_hko.py`). Read-only, about 2 requests per second, only inside the 5 to 10 minute publish window after each 10-minute stamp, 22:00 to 23:00 HKT.

**Why:** v3 saw the HKO text page a median 36 s after the page's own `Last-Modified` (276 readings, Oct 25-26), and the fastest wallets swept the dead bucket 17 to 18 s before our first sight. The page is served from HKO's own data centre on two IPs that alternate in DNS (203.129.88.115 and 103.30.70.62). A keep-alive session sticks to one of them.

**Ten variants polled every 3 s in parallel:** keep-alive HTTP/2 (what v3 does), fresh connection HTTP/2, fresh connection HTTP/1.1, the weather.gov.hk host, the Chinese page (`_c.htm`, never showed a reading, dropped), a double-slash URL, and each IP hit directly for both hostnames (SNI kept correct with `--resolve`).

## Result: seconds behind the first variant to show each reading

| reading (HKT) | Last-Modified | winner | lag after Last-Modified | keep-alive (v3) | IP 203 (hko) | IP 103 (hko) | fresh h2 | fresh h1.1 | wx host |
|---|---|---|---|---|---|---|---|---|---|
| 22:00 | 14:07:06Z | all tied | 80 s (HKO itself late) | 0 | 0 | 0 | 0 | 0 | 0 |
| 22:10 | 14:16:48Z | **IP 203** | 15 s | +3.2 | 0 | +12.8 | +3.0 | +4.6 | +7.6 |
| 22:20 | 14:26:30Z | **IP 203** | 21 s | +8.2 | 0 | +6.8 | +10.7 | +10.8 | +5.4 |
| 22:30 | 14:36:33Z | **IP 203** | 13 s | +7.5 | 0 | +13.9 | +7.6 | +10.3 | +7.8 |
| 22:40 | 14:46:31Z | **IP 203** | 3 s | +8.9 | 0 | +9.7 | +6.9 | +14.1 | +17.0 |
| 22:50 | 14:56:31Z | fresh h1.1 | 12 s | +1.1 | +7.0 | +2.7 | +2.0 | 0 | +13.2 |
| 23:00 | 15:07:09Z | **IP 203** | 14 s | +17.6 | 0 | +6.1 | +11.0 | +10.8 | +23.9 |

- Direct IP 203.129.88.115 was first on 5 of 7 readings, tied on 1, and lost 1 by 7 s.
- When it won, v3's own method was 3 to 18 s behind it (median 8 s).
- Its lag after Last-Modified was 3 to 21 s, versus v3's 36 s median. The Oct 23 log check found the sweeping bots trading 14 to 20 s after Last-Modified, so this puts us in their range, not clearly ahead of them.
- The other server (103.30.70.62) was usually the slow one (7 to 14 s behind), so it is the file-copy lag between HKO's two servers that we were paying for, not a CDN and not our polling.
- Nothing else helped: fresh connections, HTTP/1.1, the weather.gov.hk host and the double-slash URL were all within noise of the keep-alive poll.

## What v3.1 does with it

`hk_deadbucket_v3_1_direct_ip` polls the page from both IPs directly (hostname kept for the TLS check) plus the normal named fetch, and the first source to show a new stamp fires the kill logic, once per stamp. Every reading and kill records which source fired (`trigger_src`), so tomorrow's `readings-*.csv` and `versions-*.csv` show the gain in daylight hours, when it matters.

Caveat: 7 night readings from one evening. The two servers may swap roles, or HKO may change its setup. v3.1 keeps all three trigger sources for exactly that reason; the winner is whichever is first each time.
