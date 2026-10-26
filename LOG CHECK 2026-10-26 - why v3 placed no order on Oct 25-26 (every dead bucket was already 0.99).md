# Log check 2026-10-26: why v3 placed no order on Oct 25 and 26

> Plain language. Every number is from `~/hk_deadbucket_v3/data` on twapvm, the public trade tape (`data-api.polymarket.com/trades`) and the 1-minute CLOB price history.

**Short answer:** the bot is working now (the midnight bug fix of Oct 24 did its job: 8 dead buckets found in two days, 8 Telegram alerts). It placed no order because its rule is "buy NO only between 0.50 and 0.97", and at all 8 kills the NO side was already at 0.99 or had no seller left when we saw the reading. Six of the eight buckets were dead in the market hours before the reading (the forecast had already buried them). The two that were alive (29°C on both days, NO at 0.80 to 0.94) were swept to 0.99 by one big wallet each time, 17 to 18 seconds before our first sight of the HKO page.

## 1. The bot

| | Oct 25 | Oct 26 |
|---|---|---|
| service | up, 0 restarts, mode live | same |
| dead buckets found | 4 (26-or-below, 27, 28, 29) | 4 (26, 27, 28, 29) |
| NO best ask at our sight | none (0 shares ≤ 0.97) at all 4 | same |
| YES best ask at our sight | 0.001 to 0.002 | 0.001 to 0.01 |
| actionable | 0 of 4 | 0 of 4 |
| live orders | 0 | 0 |
| official max | 30.5 → favourite 30°C won | 30.0 so far (22:00 HKT) → 30°C |

"NO ask none, YES ask 0.001" means: nobody was offering NO any more; the only thing left in the book was someone selling YES for a tenth of a cent, which is the same as buying NO at 0.999. The bucket was worth $1 to NO holders and the market knew.

The `prearm failed: invalid price (0.00x)` warnings in `bot.log` are harmless: the bot tries to pre-sign a NO order on the favourite at the current NO ask, and once the favourite is at 99.9% that ask is below Polymarket's 0.01 minimum. The kill path builds its own order, so nothing is lost. Cosmetic only.

## 2. What the market did at each kill (trade tape)

| day | dead bucket | HKO stamp | our sight | NO already ≥ 0.97 since | what happened between the stamp and our sight |
|---|---|---|---|---|---|
| Oct 25 | 26°C or below | 09:50 | 09:57:11 | 03:54 (6 h before) | nothing, no trades |
| Oct 25 | 27°C | 10:20 | 10:27:34 | 04:24 | nothing |
| Oct 25 | 28°C | 11:40 | 11:47:25 | 05:41 | 14 trades at 0.998 to 0.999 |
| **Oct 25** | **29°C** | **14:00** | **14:07:49** | **14:07:33** | NO was 0.70 to 0.87 the whole time; at 14:07:32 to 14:07:33 wallet 0xdf6633c0 took 338 NO at 0.913 and wallet 0x1a9cfddd took 1,041 NO at 0.942, then 0.99 within 2 s. We arrived 17 s later |
| Oct 26 | 26°C | 09:10 | 09:17:30 | 03:10 | nothing |
| Oct 26 | 27°C | 10:20 | 10:27:22 | 04:21 | 2 trades at 0.999 |
| Oct 26 | 28°C | 11:20 | 11:27:00 | 07:32 | nothing |
| **Oct 26** | **29°C** | **12:50** | **12:56:52** | **12:56:35** | NO was 0.90 to 0.94; at 12:56:35 wallet 0x2662228c took 1,027 NO at 0.965 and 120 at 0.99. We arrived 18 s later |

So the only money on the table these two days was the 29°C bucket each day (the bucket right below the favourite): about $90 to $100 of profit per day for whoever bought NO at 0.91 to 0.965 before it went to 0.99. One wallet took it each time, with about $1,000.

Those wallets did not have a secret feed. The 1-minute price history shows the bucket drifting from 0.65 (13:50) to 0.77 (14:00) on Oct 25 while the temperature sat at 29.8 to 29.9, which is the crowd guessing the max would cross 30. Then the sweep came 7.5 min after the stamp, which is when HKO's text page appears. They just see the page before we do.

## 3. The real problem: we see the HKO page 36 s after it is written

For every text-page reading on Oct 25 and 26 (276 readings), time from the page's own `Last-Modified` header to our first sight:

| median | p10 | p90 | min | max |
|---|---|---|---|---|
| 36.1 s | 21.1 s | 67.3 s | 8.2 s | 86.4 s |

We poll every 2 s inside the publish window, so the delay is not our polling. The page is served stale for tens of seconds after it is written. There is no CDN in front: `www.hko.gov.hk` and `www.weather.gov.hk` resolve straight to HKO's data centre (`mainhko-dc-gp.wx.hk`, two IPs that alternate: 103.30.70.62 and 203.129.88.115), `server: HKO`, and the weather.gov.hk copy sends `cache-control: max-age=30`. The two IPs showed different `Last-Modified` times for the same reading (3 s apart), so the file is copied to several servers with a lag, and a keep-alive session sticks to one of them.

The Oct 23 idea of a cache-busting query string (`text_nocache`) did not help: median 0.5 s slower than the plain poll over 163 stamps. The cache key ignores the query string, or the lag is server-side.

**Source race result (22:00 to 23:00 HKT, 5,723 polls, full write-up in `hk_deadbucket_v3_1_direct_ip\RACE 2026-10-26 - which way of fetching the HKO page shows a new reading first.md`):** fetching the page straight from server IP 203.129.88.115 showed the new reading first on 5 of 7 stamps (tied 1, lost 1 by 7 s), 3 to 21 s after Last-Modified instead of v3's 36 s median; v3's own method was 3 to 18 s behind it. The other server (103.30.70.62) was the slow one, so the lag is the file copy between HKO's two servers. Fresh connections, HTTP/1.1, the other hostname and URL tricks changed nothing.

**v3.1 built from it:** `hk_deadbucket_v3_1_direct_ip` (service `hk-deadbucket-v3.1-direct-ip`, Telegram label "HK dead-bucket v3.1 (direct HKO server IP trigger)") polls both IPs directly plus the named page, the first to show a new stamp fires once, and every reading/kill records which source fired. Installed DRY beside the live v3 on 2026-10-26 15:16Z. Deploy slip on the way: the first two starts ran the old v3 code from the v3 folder in dry mode for about a minute each (unit file WorkingDirectory had no trailing slash and the files had Windows line endings), which sent two "started in DRY mode" Telegram messages labelled v3 at 15:14Z and 15:15Z. The live v3 process itself was never touched. Fixed by converting to LF and correcting the path.

## 4. What it means for the live test

- Nothing is broken. The order path is still unproven because no kill has ever met the 0.50 to 0.97 condition at our speed (10 kills since Oct 23, 0 actionable).
- The prize exists (about $100 a day on the bucket under the favourite) but is taken 17 to 18 s before we see the page. Unless the race above finds a faster fetch, v3 cannot win it from this server, and a bot that is 36 s slow than the page will never place an order. That matches the tape backtest: the market "knows" at 7.0 min, we see at 7.4.
- Not worth changing: raising the 0.97 ceiling. Above 0.97 the fee (0.05·p·(1−p) ≈ 0.15c) plus the 1c spread eats the whole 1 to 3c you can still win.
