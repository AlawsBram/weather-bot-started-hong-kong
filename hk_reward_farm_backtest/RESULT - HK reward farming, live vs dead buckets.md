# RESULT - HK reward farming, live vs dead buckets

Backtest window: 2026-04-15 to 2026-10-20 (169 Hong Kong "highest temperature" events, 1,859 bucket markets, 151 complete calendar days scored).
Script: `reward_farm.py` in this folder. Numbers: `backtest from 2026-10-23 to 2026-04-15 - HK liquidity reward farming net of pick-offs.txt` / `.csv`.

## One-line answer

**FAIL.** Resting 20-share bids and asks 1 cent off the mid in every HK temperature bucket would have collected about **$30 a day** in liquidity rewards (median-competition scenario) but given back **$190 a day** in pick-offs. Net was positive on **1 of 151 days (1%)**; the gate needs 70%. No bucket type passes on its own: LIVE buckets are the worst, FAR buckets are second worst, DEAD buckets are almost never quotable, and the day-before ("EVE") is roughly break-even at best.

## What was tested (plain words)

Polymarket pays a daily "liquidity reward" to people who keep buy and sell orders close to the price in a market. Our idea: put a 20-share buy order 1 cent below the price and a 20-share sell order 1 cent above it, in every Hong Kong temperature bucket, all day, and collect that reward. The danger: when the temperature reading comes out and a bucket is suddenly known to be dead (or alive), the price jumps and someone trades against our stale order before we can move it. That is a "pick-off". The test asks: rewards minus pick-offs, is it positive?

For every minute of every bucket market (from 00:00 HKT the day before, until 23:59 HKT on the event day), the script:
1. Looks at the 1-minute price (Polymarket's midpoint history, fetched from the public CLOB API and cached in `cache\px_<token>.json`).
2. If the price is between 2c and 98c, it rests our two orders 1c away and credits the reward for that minute (official formula, see below).
3. If the next minute's price crossed one of our orders, it counts a pick-off: we are filled at our price and marked at the new price (base case), or held to the market's final result (alternative).
4. Labels the bucket that minute as EVE (day before the event day), LIVE (the bucket the running max is in right now), DEAD (the running max has already passed it, known to the market) or FAR (above the live one).

## Assumptions (stated loudly)

1. **Pool size and shape (the biggest unknown).** Measured live 2026-10-23 19:19Z: $100/day per HK event, paid only on 3 buckets (around the most-likely max), $50 to the top one and $25 each to the other two; the pool exists on the day before and on the event day and is $0 after 23:59 HKT. Historical pools are **not published anywhere we can reach**. Proxy used: the 3 buckets with the highest YES price at 00:00 HKT of the event day get $50/$25/$25 (first price of the day when 00:00 was missing: 22 of 1,859 markets). That proxy put the eventual winner among the 3 rewarded buckets on 150 of 169 days, so it is probably generous. Rewards scale 1:1 with the pool, so the `.txt` also prints income per $100 of pool. The old assumption ($200/day spread over 7 buckets by rank from the live bucket) was run as well; it changes the reward from $4,592 to $7,112 over 151 days and does not change the verdict.
2. **Competition.** Everyone else's score ("Q_min") is unknown historically. Three constant scenarios from today's live reading: 20 (easy), 45 (median, the gate scenario), 200 (hard). With our two 20-share orders 1c off a 4.5c max spread our Q_min is 12.1, so our share of a bucket's pool is 38% / 21% / 6%.
3. **Reward formula** (docs.polymarket.com/programs/liquidity-rewards): per order S = ((4.5 - s)/4.5)^2 x size, s = cents from the mid; Q_min = max(min(Q_bid,Q_ask), max/3) when the mid is in 10c-90c, else min(Q_bid,Q_ask); reward per minute = pool/1440 x ourQ/(ourQ + others). Max spread 4.5c and min size 20 confirmed live; gamma shows 50 or 100 as `rewardsMinSize` on 1,320 of the 1,859 historical markets, so a "size = market min size" sensitivity is included.
4. **Mid proxy** = Polymarket's 1-minute price history (values are half-cent midpoints). Last point in each minute, forward-filled. Minutes with price < 2c or > 98c: no quote, no reward, no pick-off.
5. **Pick-off rule (base)** = a 1c move of the mid across our price counts as a fill. This is an **upper bound**: a mid can move because someone else cancels an order, with no trade. That is why a second estimate from the real trade tape is given below.
6. **Bucket type.** Kill times come from the kill list (HKO reading time + the seconds until the market "knew", median 418 s used for the 93 kills where the tape never showed NO >= 98.5c). Buckets below the lowest listed kill were already dead at midnight (the kill list only records crossings seen during the day). LIVE = the lowest not-yet-dead bucket; FAR = any not-dead bucket above it; EVE = anything before 00:00 HKT of the event day. A pick-off is charged to the type the bucket had when the order was resting. Check: the final LIVE bucket equalled the resolved winner on all 169 days.
7. Fees: makers pay 0; HK taker fee rate is 0 too. The "fee if taker" column ($22,837) exists only to show fees are not the issue.
8. Re-quoting is free and instant (new quote in place from the next minute). Real latency would only make pick-offs worse.

## The numbers (151 complete days, 2026-04-15 to 2026-10-19)

Base case: 20 shares each side, 1c off mid, pick-off marked at the new mid.

| Competition | Reward | Pick-off loss | Net | Net / day | Days net > 0 | Worst day |
|---|---|---|---|---|---|---|
| 20 (easy) | $8,302 | $30,853 | **-$22,551** | -$149 | 1 / 151 (1%) | -$674 (2026-05-20) |
| **45 (median, gate)** | $4,592 | $30,853 | **-$26,261** | -$174 | **1 / 151 (1%)** | -$692 (2026-05-20) |
| 200 (hard) | $1,219 | $30,853 | **-$29,634** | -$196 | 0 / 151 | -$709 |

135,336 pick-offs = 892 a day (median), $0.23 lost each. The reward side is tiny and flat: $20-$34 a day on every single day, i.e. about 19% of the pool minutes we could reach ($23,752). We quoted 1.67 million bucket-minutes; 1.02 million of them were in a rewarded bucket.

Held to resolution instead of marking at the new mid: loss $23,543, net -$126/day at C=45, 33/151 days positive (22%). Still a clear fail.

### By bucket type (C=45, mark-to-mid)

| Type | Quoted minutes | Pick-offs (one every) | Reward | Pick-off loss | Net | Days net > 0 |
|---|---|---|---|---|---|---|
| EVE (day before) | 1,108,302 | 42,393 (26 min) | $2,851 | $4,437 | -$1,586 | 73 / 150 (49%) |
| LIVE | 63,352 | 21,673 (2.9 min) | $169 | $9,402 | -$9,233 | 1 / 150 (1%) |
| DEAD | 331 | 80 (4.1 min) | $0.39 | $97 | -$96 | 1 / 45 (2%) |
| FAR | 497,337 | 71,190 (7.0 min) | $1,571 | $16,917 | -$15,346 | 1 / 150 (1%) |

What this says in plain words:
- **DEAD buckets cannot be farmed.** Once the market knows a bucket is dead its price drops under 2c within minutes, and Polymarket's rules (both sides required, inside the spread) make it worthless for rewards. In 151 days there were only 331 quotable dead-bucket minutes, and the 80 pick-offs in them cost 250x the reward.
- **LIVE buckets are a wood-chipper.** One pick-off every 3 minutes, $0.43 each; losses are 55x the reward. This is the bucket whose price moves on every 10-minute HKO reading.
- **FAR buckets lose most in dollars** simply because there are many of them and they are quoted all day; their price moves whenever the forecast or the live reading moves.
- **EVE (the day before) is the only part near zero.** With easy competition (C=20) it nets +$721 over 150 days ($4.80/day, 104/150 days positive); with median competition it is -$1,586. It fails the gate at C=45 (49%) and the dollar amounts are not worth a bot.

### By hour of day (HKT, C=45)

Every hour of the day is net negative in total. The damage is concentrated in 09:00-16:00 HKT (the hours when the temperature actually climbs): hours 11-14 alone lose $16,300 of the $26,261. Night hours (18:00-06:00) lose $50-$300 each over the whole 151 days. The EVE-only line is slightly positive in the small hours (02:00-10:00 HKT, +$5 to +$40 per hour over 151 days) and negative every evening hour, which is the forecast-update time.

### Sensitivities (C=45 unless stated)

| Variant | Reward | Pick-off loss | Net / day | Days net > 0 |
|---|---|---|---|---|
| 2c off mid instead of 1c | $2,491 | $18,263 | -$104 | 1 / 151 |
| 10 shares instead of 20 | $2,546 | $15,427 | -$85 | 1 / 151 |
| Size = each market's `rewardsMinSize` (20/50/100) | $9,053 | $81,662 | -$481 | 1 / 151 |
| Old pool shape ($200 rank 53/50/46/39/7/4/1) | $7,112 | $30,853 | -$157 | 1 / 151 |
| Tick-level pick-offs (every price point, not 1-min) | - | $30,858 | same | same |

Nothing flips. Quoting wider or smaller cuts both sides roughly in half; the ratio loss/reward stays near 6-7x.

### Reality check from the trade tape (fills only when a real taker traded through our price)

The mid-move rule can over-count fills. For the 567 bucket markets that already have their full trade tape cached (the buckets that were killed during the day: mostly LIVE-then-DEAD and the FAR ones just above), the script re-ran the same quotes and filled us only when a taker actually sold YES (or bought NO) at or below our bid, or bought YES at or above our ask, shares capped at 20 per minute.

| Mark of the filled shares | Reward (C=45) | Loss | Net | Days net > 0 (of 150) |
|---|---|---|---|---|
| next minute's mid (optimistic: nobody can exit at mid) | $1,623 | $1,579 | **+$44** | 92 (61%) |
| next mid, paying 1c/share to get out | $1,623 | $9,064 | **-$7,442** | 4 (3%) |
| held to resolution | $1,623 | $1,796 | **-$174** | 63 (42%) |

Real fills were about as frequent as mid-moves (51,129 vs 47,991 on the same markets, 748,538 shares) but on average less damaging per fill: takers often cross a wide spread and the mid does not follow. Even so, the best case is zero, the moment you add a realistic 1c exit cost it is -$50/day, and held to resolution it is still negative. Note the LIVE row: marked at mid it loses $1,526 on $80 of reward; FAR and EVE are the only rows that are positive at mid-mark, and both turn negative with a 1c exit cost.

## What is NOT known

- Historical pool sizes per market (the $100 / 3-bucket / $50-25-25 shape is today's, applied to every day).
- Historical competition (constant 20/45/200 score units is a guess calibrated on one live snapshot).
- Which 3 buckets actually carried the pool each day (00:00 HKT price proxy).
- Queue position: the tape check assumes our order is first in line at its price.
- 20 of 189 calendar days have no gamma event in the cache (2026-04-24, 05-05, 05-09, 05-16..18, 06-14, 07-14, 07-20, 07-26, 07-31, 08-14, 08-20, 08-26, 09-14, 10-05, 10-07, 10-08, 10-12, 10-16); they and their eve are excluded, which is why 151 (not 168) days are scored.
- 11 markets had fewer than 100 price points (effectively never traded); they contribute nothing.

None of these unknowns is big enough to turn a 6-7x loss/reward ratio into a profit: rewards would have to be ~6x larger (a $600/day pool per event) or pick-offs ~6x smaller at the same reward.

## Gate

**Gate: net > 0 on at least 70% of days under the median competition scenario (C=45).**
Result: 1 / 151 days = 1%. **FAIL.** Per type: EVE 48% FAIL, LIVE 1% FAIL, DEAD 1% FAIL, FAR 1% FAIL. The trade-tape version at its most optimistic mark reaches 61% (still FAIL) and only on a subset of markets.

## Next step

Do not build an HK reward-farming bot. If reward farming is ever revisited, the only two places with a sliver of hope in this data are (a) the day-before market in the HKT small hours and (b) only when competition is light (C around 20), and even then the prize is a few dollars a day before latency and queue position. A cheaper next move than any bot: read the actual `rewards/markets/current` pool numbers for a week to replace the pool assumption, because every result above scales with it.

## Files

- `reward_farm.py` - the script (fetch + cache + simulate + report). Run `python reward_farm.py` (uses the cache; `--days N` for a smoke test).
- `backtest from 2026-10-23 to 2026-04-15 - HK liquidity reward farming net of pick-offs.csv` - per day x bucket type (556 rows, `complete_day` flag).
- `... - by hour of day HKT.csv` - per HKT hour x bucket type.
- `... - rewarded buckets per day (00h HKT price proxy).csv` - which 3 buckets got the assumed pool each day.
- `backtest from 2026-10-23 to 2026-04-15 - HK liquidity reward farming net of pick-offs.txt` - printed summary.
- `run.log` - fetch/simulation log. `cache\px_<token>.json` - 1,859 cached price series (shared with `..\hk_deadbucket_only_backtest\`).
