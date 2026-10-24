# X POST CHECK 2026-10-23 - @0xSurferX: "top 1% market maker" article + "$670k YES+NO<$1" wallet

Posts read in Chrome (text + every image):
- https://x.com/0xSurferX/status/2106781374170509422  (X article, Oct 4 2026, 25.7K views) "How i became top 1% Polymarket market maker (or how liquidity rewards actually work)"
- https://x.com/0xSurferX/status/2112914483299364904  (Oct 21 2026, 7.5K views, tagged PAID PARTNERSHIP; attached image is an EcoHash GPU-rental advert) "This wallet is over $670k all time PnL and still just doing YES + NO < $1"
Both end in a funnel: Telegram "Surfer's Alpha" (screenshot shows 1 subscriber), "limited to 10 spots/day".

Data pulled (read-only, from twapvm): Polymarket docs (fees, liquidity rewards, taker rebates), data-api user-pnl + activity + trades for the wallet, CLOB rewards/markets/current (14,170 rewarded markets), gamma temperature events (329 active), live HK order books.

---

## 1. The article: what is right, what is wrong

| Claim in the article | Verdict | Evidence |
|---|---|---|
| Score per order S = ((v - s)/v)^2 x b, quadratic in distance, linear in size | CORRECT | docs.polymarket.com/programs/liquidity-rewards Eq.1 |
| Qmin = max(min(Q1,Q2), max(Q1/c, Q2/c)) with c = 3; below 0.10 / above 0.90 you must quote both sides | CORRECT | docs: "c = 3.0 on all markets", midpoint band [0.10, 0.90] |
| "3:1 rule": 100/100 -> 100, 200/100 -> 100, 400/100 -> 133 | CORRECT (follows from the formula) | arithmetic checks |
| Book sampled every minute; paid daily ~midnight UTC in pUSD; $1 minimum, no carry-over | CORRECT, one detail missing | docs: once per minute AT A RANDOM OFFSET (so you cannot flash quotes on the minute) |
| "Every cancel resets your accumulated score" | HALF RIGHT | there is nothing to reset; each sample is independent. You only lose the samples in which you had no order. Same practical conclusion. |
| Maker rebates: 20% crypto, 25% most, 15% sports, "up to 50% finance" | WRONG on finance | docs fees page: Finance 25%. Crypto 20%, Sports 15%, everything else 25% is right |
| "Reward orders are off-chain and private, nobody can mirror you" | HALF RIGHT | the wallet is not attributed, but every resting quote is visible in the public book |
| "eSports farmed best", "top 1% of wallets" | UNVERIFIABLE | no wallet given, no numbers, no rewards-leaderboard shown |
| Polymarket US formula = discount_factor^ticks x size | UNVERIFIED | not in the international docs we fetched |
| Max spread example 3c | fine as example | temperature markets today: 4.5c, min size 20 shares |

Bottom line: the mechanics section is a faithful rewrite of the official docs (the docs themselves say the method is copied from dYdX). The "I'm top 1%" part has no evidence and the post is a funnel.

## 2. The "$670k simple arb" wallet - what the data says

Wallet 0xce25e214d5cfe4f459cf67f08df581885aae7fdc (data-api v2/user-pnl, interval=all, 177 daily points since 2026-05-01):

| Line | All-time |
|---|---|
| trade_pnl (gross, before fees) | +$703,511 |
| fees_paid | -$709,886 |
| realized_market_pnl (trading, net of fees) | **-$6,486** |
| taker_rebate (tiered Taker Rebate Program, live since 2026-05-28) | +$210,166 |
| maker_rebate | +$30,470 |
| reward_income (liquidity rewards) | +$1,615 |
| economic_pnl (what he really made) | **+$235,876** |
| volume | $32.3M on 2.88M trades |

Cumulative realized_market_pnl by month end: May +40.8k, Jun +49.5k, Jul +56.5k, Aug +37.5k, Sep +3.0k, Oct -6.5k. So trading net of fees has LOST money every month since August (-19k, -34.5k, -9.5k). Rebates grew about +9k so far in October, so October is roughly flat overall.

What the bot actually does (5,000 most recent activity rows = 29 h, Oct 22 13:42Z to Oct 23 18:48Z): 4,452 BUYs, 0 SELLs, $26.6k; btc-updown-15m 2,208, btc-updown-5m 1,047, eth 15m 572, sol 15m 278, xrp 15m 127. Buys both sides in 256 of 319 markets. Median (avg Up price + avg Down price) per market = **0.998**. In an 8-hour window 251 of 441 trades were TAKER fills (57%), median taker fee 1.65c per share.

So: "$670k PnL" is the gross trade_pnl shown on the profile page; "YES+NO < $1 with a fat gap that covers fees" is false (pairs cost 0.998 and 57% of fills pay taker fees); the money is the taker-rebate tier (volume game: Obsidian tier = 50% rebate above $10M 30-day weighted volume, crypto weight 2.3). This is the gabagool pattern we already closed, now kept alive only by a rebate program that needs millions of dollars of monthly volume. Not copyable at our size.

## 3. New facts that matter for OUR work (the real value of this check)

1. **Temperature markets carry Liquidity Rewards; BTC 5m/15m do not.** Today's btc-updown-5m and -15m condition ids are NOT in rewards/markets/current. 329 active temperature events. CORRECTION (19:19Z, after the daily logger's first run): the pool is **$100 per day per EVENT** (one city, one date, highest OR lowest), and only the **3-4 central buckets** carry it (top bucket about $50, the rest share $50); every other bucket is $0. The "per city" totals first computed (NYC $604, London $602, HK $400 ...) were sums over 3 dates x highest+lowest. The pool exists two days before, one day before and through the event's local day, and is $0 after local midnight (HK Oct 23 = $0 at 03:19 HKT Oct 24; NYC Oct 23 still $100 at 15:19 ET). All: max spread 4.5c, min size 20 shares. HK Oct 24 highest = 3 buckets, $100 (32C $50); my earlier "7 buckets, $200" was Oct 24 + Oct 25 combined.
2. **Weather taker fee is currently 0 on almost every city.** gamma feeSchedule rate = 0 for 50 of 53 cities (only jinan, zhengzhou and two odd cape-town/sao-paulo markets show 0.05). The docs still list Weather 0.05. Our memory and tape.py assume 0.05 p(1-p); for HK right now the real fee is 0 (verify on the first live fill; the CLOB fee-rate endpoint returns base_fee 1000 for BTC too, so it is not the applied rate).
3. **Quick live estimate of reward income in HK**: a 20-share two-sided quote 1c from mid in each of the 7 rewarded HK Oct-24 buckets, scored with the official formula against the books as they were at 19:00Z, would take about 5-54% of each pool = about $29/day on roughly $140 of resting capital. That is BEFORE adverse selection, which in dead-bucket markets is brutal: when the HKO reading moves, a live bucket's mid jumps 20-40c in one minute and the resting 20-share bid is picked off (about -$6 per pick-off vs about $7/day reward per bucket).

## 4. What we can test now, from data we already have (no waiting)

A. **Reward-farm backtest for temperature buckets (half a day).** Inputs we own: 1-minute CLOB price history for every HK bucket (169 days), the 567 kill timestamps, today's book depth, the official formula (one function). Simulate: rest 20 shares both sides 1c from mid, re-quote when mid moves > 1c, score every minute, subtract pick-off losses = (mid jump) x 20 shares whenever the 1-minute move crosses our quote. Output $/day net by hour-of-day and by bucket type (live bucket vs dead bucket vs far tail). Pass if net > 0 on at least 70% of days.
B. **Dead-bucket makers only (1 hour extension of A).** Quote both sides only in buckets our v3 logic has already declared dead (NO is worth $1, YES is worth 0): the "adverse selection" is then zero by construction, but the mid sits below 0.10 so both sides are required and the YES bid must be 1 tick... compute what share of the pool those buckets carry (today: the 0.06-0.10 buckets had $1-7/day each).
C. **Fee check (10 minutes).** Re-run tape.py with fee = 0 and with fee = 0.05 p(1-p); report both, label which one gamma says applies per day.
D. **Pool-size scan every day (5 minutes, cron).** Log rewards/markets/current for the 53 cities: $/day, max spread, min size. Pools change with the date; the biggest pools are the famous cities (crowded), the article's point about "asymmetric markets" is testable by share-of-pool per city from live books.
E. **Taker rebate tier**: not testable for us, our volume is 4 orders of magnitude too small. BTC 5m: nothing in these posts applies (no rewards, no edge in the copied wallet).

## 5. Verdict

- Article: a good plain-English copy of the official Liquidity Rewards docs, one factual error (finance rebate), no evidence for the author's own results, ends in a paid funnel.
- Wallet post: paid partnership, the wallet loses money on its trades after fees and lives on rebates; the "simple arb" story is wrong.
- Useful to us: temperature markets are subsidised ($200-600/day per city) and currently fee-free, which makes a weather maker test the first new, data-backed idea in this area. Run test A before anything live.
