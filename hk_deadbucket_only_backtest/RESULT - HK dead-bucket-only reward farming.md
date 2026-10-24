# RESULT - HK dead-bucket-only liquidity reward farming (2026-04-15 .. 2026-10-20)

**Verdict: FAIL.** Under the median scenario (others' score 40, ranked pool) the farm nets **-$0.10 per kill-day** and is positive on only **26% of days**. The gate was "net > 0 on 70% of days AND > $5/day". Even a perfect version (every dead minute quotable, no fills) tops out at **$3.76/day** under the median competition, so the gate cannot be reached by better execution.

Files (same folder):
- `backtest from 2026-10-23 to 2026-04-15 - HK dead-bucket-only liquidity reward farming.csv` - one row per kill (566 rows)
- `... - per day.csv` - one row per calendar day (168 rows)
- `... .txt` - the numbers summary printed by the script
- `deadbucket_rewards.py`, `run.log`

## The idea in one paragraph

Hong Kong has a daily "highest temperature" market with 11 buckets (26 °C or below, 27, 28 ... 36 °C or higher). Once today's running max has passed a bucket, that bucket is **dead**: its YES is worth 0 and its NO is worth $1, no matter what. Polymarket pays "liquidity rewards" to people who keep buy and sell orders near the price. In a dead bucket nobody can out-smart us (there is nothing left to know), so the only questions are: is there a reward pool on the dead bucket, can we even place a two-sided quote when the price is 0.001, and what do the fills cost us?

## What we simulated (every rule, loud)

1. **Kills.** 567 kills from the trade-tape backtest (`..\hk_tape_backtest\...first-mover prize from the trade tape.csv`): bucket X dies at HKO reading time T; the market "knows" at T + t_know (first trade at NO >= 0.985). 92 kills had no t_know on the tape; for those we used the median 418 s. 1 kill skipped (2026-05-19 25 °C: no price history). 566 simulated, all resolved YES = 0 (checked against gamma `outcomePrices`).
2. **Prices.** Polymarket 1-minute price history of the YES token (`prices-history`, fidelity 1) from day-1 00:00 HKT to day+1 06:00 HKT. The series is a **midpoint** (0.0005 means bid empty / ask 0.001). Cache shared with the full reward-farm backtest in `..\hk_reward_farm_backtest\cache\px_<token>.json` (567 files).
3. **Time window.** From the kill (T + t_know) to **23:59 HKT the same day**. Reason: measured live on 2026-10-23 the pool is $0 after the event's local midnight.
4. **Pool (the big assumption, measured live 2026-10-23 19:19Z).** $100/day per HK event, but only the 3 central buckets carry a reward line: the top one $50, the other two $25 each, all others $0. Historical pools are not recorded anywhere, so we used a no-lookahead proxy: **rank the killed bucket by its YES midpoint at 00:00 HKT of that day**; rank 1 = $50, rank 2-3 = $25, rank 4-11 = $0. The reward is also reported **per $10 of pool** so you can rescale.
5. **Scoring (docs.polymarket.com/programs/liquidity-rewards, re-read today).** S = ((4.5 - s)/4.5)^2 with s = distance from the midpoint in cents (every HK market has `rewardsMaxSpread` 4.5). When the midpoint is below 0.10 **both sides are required** and only min(bid side, ask side) counts. Book sampled once a minute; our share of a minute = ourQ / (ourQ + others). Others' Q: scenarios **10 / 40 / 100** (today's dead buckets showed 10-40). Order size 20 shares (min size confirmed live; see data-problem 3 below).
6. **Our quote.** 20 YES bid at mid - 1c (never below the 0.001 tick) and 20 YES ask at mid + 1c, re-quoted when the mid moves >= 1c. The "YES ask" is really a **NO bid** (we never own YES); if it fills we bought NO worth $1 for ~0.99 = small gain, capital tied until resolution. The YES bid, if filled, buys worthless YES = loss of 20 x price. At 1c from the mid each side scores S = 0.605, so ourQ = 12.1 units -> share 55% / 23% / 11% against others 10 / 40 / 100.
7. **When we cannot quote.** (a) If the midpoint is 0.0005 or 0.001 (book 0 / 0.001), a bid at the 0.001 tick would sit on or above the mid and hit the ask instead of resting - no two-sided quote exists, score 0. (b) If the midpoint is >= 0.10 the market does not price the bucket dead yet; we stand aside (this also removes a one-minute artefact where the price carried into the kill minute was still 0.80).
8. **Fills.** Fill proxy = the next 1-minute midpoint reaches our price. Optimistic for the loss side (a midpoint touching our bid does not always mean our 20 shares traded) and optimistic for the reward side (we assume our order is live every sampled minute).

## What the data says

| Item | Number |
|---|---|
| Kills simulated / calendar days with a kill | 566 / 168 (the window has 189 days; 21 days had no kill = $0) |
| Killed buckets that were top-3 at 00:00 HKT (= had a pool) | **184 of 566 (33%)**; rank 1: 53, rank 2: 69, rank 3: 62 |
| Pool attached to dead buckets | $5,925 total = **$35 per kill-day** out of $100/day per event |
| Dead minutes per day (kill -> 23:59 HKT) | 2,578 mean (all kills); **730 on pooled kills** (central buckets die around 12:57 HKT, so ~11 h remain) |
| Quotable minutes (mid < 0.10 and bid can rest) | **10.4%** of all dead minutes; **17.2%** on pooled kills; 540 of 566 kills sit at a 0.0005 median mid after the kill |
| Quotability by month | Apr 30% -> May 13% -> Jun 11% -> Jul 12% -> Aug 4% -> Sep 6% -> Oct 5% (books tightened to 0/0.001) |
| YES-bid fills (worthless YES bought) | 1,334 fills, **$140.76 total = $0.84/day**; 471 of 566 kills had at least one; median fill price $0.001 (20 shares = 2 cents) |
| NO-bid fills (free money) | 23 fills, $17.56 total; 7 of them on 2026-04-22 alone (someone bought YES of a dead bucket at up to 0.166) - noise, not farm income |
| Reward income, ranked pool | others 10: **$1.54/day**; others 40: **$0.64/day**; others 100: **$0.29/day** (medians $0.28 / $0.11 / $0.05) |
| Reward per $10 of pool on every dead bucket | $0.98 / $0.41 / $0.19 per day |
| Net $/day (reward - YES losses + NO gains) | others 10: **+$0.81** (positive 43% of days); others 40: **-$0.10** (26%); others 100: **-$0.44** (14%) |
| Net, quoting only the 184 pooled kills | +$1.05 / +$0.14 / -$0.20 per day |
| Ceiling: every pooled dead minute quotable, zero fills | $8.86 / **$3.76** / $1.75 per day |
| Gate (70% positive days AND > $5/day, others 40) | **FAIL** (26%, -$0.10/day) |

Worst day: 2026-07-17, -$3.88 (16 YES-bid fills while two buckets drifted down from ~0.05). Best day: 2026-04-19, +$8.34 ($6.36 reward on 1,686 quotable minutes when books were still 1-2c wide, plus $3.30 from NO fills).

## Why it fails, in plain words

1. **Most dead buckets have no pool.** Two thirds of the buckets that die were never among the 3 rewarded central buckets, so they pay nothing. Only $35 of the $100 daily pool ever sits on a dead bucket, and that bucket dies around 1 pm, so less than half the day is left.
2. **You usually cannot quote at all.** Once a bucket is dead the book collapses to bid nothing / ask 0.001. A two-sided quote needs a bid below the mid; with a 0.001 tick there is no room. Only 10% of dead minutes (17% on pooled kills) allow a quote, and that share has been shrinking all year (30% in April, 5% in October).
3. **Fills are cheap but reward is cheaper.** The feared "we buy worthless YES" cost is real but tiny: $0.84/day, mostly 2-cent fills at 0.001. The problem is that the reward is tiny too: $0.64/day at median competition. Even if execution were perfect the median-competition ceiling is $3.76/day, below the $5 gate.
4. **Competition decides the sign.** With only 10 units of competing score the farm is slightly positive (+$0.81/day, 43% of days); at 40 it is break-even-to-negative; at 100 it loses.

## Data problems (not hidden)

1. **Pools are reconstructed, not observed.** The 00:00 HKT top-3 ranking is a proxy for "central/rewarded bucket". 18 of 1,859 bucket price lookups had no price point near 00:00 (ranked last, which is where such buckets belong). If Polymarket's historical pools were bigger than today's $100/event, rescale with the per-$10 line.
2. **Dead-bucket pools may not even exist.** Today's live check saw 3 rewarded buckets per event; whether a bucket keeps its line after it dies, or the line moves to another bucket, was not observable historically. We assumed it keeps it (generous to the farm).
3. **Min size.** Live today the min size is 20 shares, but gamma's cached `rewardsMinSize` for the historical markets was 50 on 290 kills and 100 on 121 kills. If those had been binding, 20-share orders would have scored nothing on 73% of kills and income falls to $0.06/day (others 40).
4. **Fill proxy from midpoints.** We see 1-minute midpoints, not the book. A touch counts as a full 20-share fill (pessimistic on losses) and we assume our order is live on every sampled minute (optimistic on rewards). Both errors are small relative to the conclusion.
5. **t_know fallback** on 92 kills (median 418 s) and **1 kill with no price history** (2026-05-19 25 °C).
6. **The large positive days are April**, when HK books were still 1-2c wide. That regime is gone (October quotability 5%).

## Next step

Do not build the dead-bucket-only farm. The only version worth a second look is "others = 10" on pooled kills only (+$1.05/day, 43% positive), and that needs a live check that (a) a dead central bucket actually keeps its reward line after it dies and (b) competing score really is ~10 units - both are one-evening observations on the live `rewards/markets/current` endpoint, no bot required. If the full reward-farm backtest (sibling agent) shows the live central buckets are where the money is, the dead-bucket leg should be at most an add-on, not a strategy.
