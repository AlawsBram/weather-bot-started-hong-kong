# RESULT - HK dead-bucket: what the first movers really take (from Polymarket's public trade tape)

Made 2026-10-23 in ~15 minutes from data that already existed (no waiting). Script `tape.py`, cache in `cache/`,
full table in `backtest from 2026-10-23 to 2026-04-15 - HK dead-bucket first-mover prize from the trade tape.csv`.

## Question
When an HKO reading kills a bucket, how much money is really on the table for whoever buys NO first, how fast is it gone, and who takes it?

## Method
For each of the 567 kills (Apr 15 - Oct 20) pull every trade on that bucket's market. "Prize" = every NO bought at 97c or
less between 4 min after the reading stamp and 90 s after the market first trades at 98.5c, valued at 99.9c.

## Answer
| | |
|---|---|
| kills | 567 over 169 days |
| total prize taken | $23,209 (one odd kill on May 7 is $4,012 of it; $19.2k without it) |
| per kill | median $0, mean $41; 453 of 567 kills had under $10 on the table |
| kills with >= $50 | 73 (about one every 2.3 days), 28 with >= $200 |
| per calendar day | median $14, mean $137 |
| market "knows" (first trade at >= 98.5c) | median 7.0 min after the stamp (p10 4.4, p90 8.0) |
| HKO text page visible to us | 7.4 min after the stamp |
| split between | 10+ wallets; the best took $1,994 in 6 months |

By month ($ prize, $ per day): Apr 3,116 (208) · May 6,762 (260) · Jun 1,661 (57) · Jul 1,694 (63) · Aug 2,990 (107) · Sep 6,262 (216) · Oct 724 (48).
The big prizes are afternoon crossings of 31-34C where the market was split (NO at 10-75c before the stamp) - a few per month.

The fastest wallet seen live today (Fine-Passive-Cufflink, 0x1a9cfddd...) has +$34k realized since July, but $29k of it was August and only $464 in October,
and only $525 of it falls inside this post-publication sweep window - so its profit mostly came from positions taken BEFORE publication (prediction), not from being first after it.

## Verdict
Real, small, shrinking, and crowded. Being first after publication is worth roughly $50-200 on a good day and nothing on most days,
shared with a dozen bots that already beat our current polling by 6-9 s. Worth attacking only if the cache-bypass poll (added to v3 today)
puts us ahead of the pack; measured automatically from tomorrow's readings. It is not a strategy that scales to meaningful money in Hong Kong.
