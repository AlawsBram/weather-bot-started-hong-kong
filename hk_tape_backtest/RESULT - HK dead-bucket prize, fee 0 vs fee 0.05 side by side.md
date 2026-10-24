# RESULT - HK dead-bucket prize, fee 0 vs fee 0.05 side by side

Made 2026-10-23 from the cached trade tape (no new downloads, no VPS, no orders). Script `tape_fee.py` (= `tape.py` + a `--fee` switch,
nothing else changed). Outputs:
`backtest from 2026-10-23 to 2026-04-15 - HK dead-bucket first-mover prize, fee 0.csv/.txt` and
`backtest from 2026-10-23 to 2026-04-15 - HK dead-bucket first-mover prize, fee 0.05.csv/.txt`.

## What was asked
The first-mover prize in `RESULT - HK dead-bucket, what the first movers really take (trade tape).md` was counted with NO trading fee.
Polymarket's docs say weather markets charge takers `0.05 x price x (1 - price)` per share. Gamma's market data says the Hong Kong
fee rate is 0 right now. So: how much does the fee change the answer? Both versions are shown.

## Which fee is real?
**Unknown today.** Gamma (`feeSchedule.rate`) says **0** for Hong Kong markets; the docs say **0.05**. Nobody knows if that is a
fee holiday, a category change, or a stale field. Both numbers are below. **The first real fill must confirm which one applies**
(look at the fee line on the fill receipt). Until then, plan with the 0.05 column.

## Side by side (567 kills, 169 days, 2026-04-15 to 2026-10-20)

| | fee 0 (gamma today) | fee 0.05 (docs) | difference $ | difference % |
|---|---|---|---|---|
| total prize | $23,209 | $22,493 | -$717 | -3.1% |
| median per kill | $0.00 | $0.00 | $0 | 0% |
| mean per kill | $40.93 | $39.67 | -$1.26 | -3.1% |
| median per day | $13.93 | $13.25 | -$0.68 | -4.9% |
| mean per day | $137.33 | $133.09 | -$4.24 | -3.1% |
| kills >= $10 | 114 | 113 | -1 | -0.9% |
| kills >= $50 | 73 | 72 | -1 | -1.4% |
| kills >= $200 | 28 | 28 | 0 | 0% |
| kills < $10 | 453 | 454 | +1 | |
| total fees paid | $0 | $717 | | |

Only two kills changed category: 2026-06-19 30C went from $52.45 to $49.93 (drops out of the ">= $50" bin) and
2026-10-10 30C went from $10.31 to $9.81 (drops out of ">= $10"). The top-wallet list is the same names in the same order,
each about 3-5% smaller (Tan-Humor $1,994 -> $1,903).

## Why the fee barely matters here
The fee is `0.05 x price x (1 - price)` per share, and the prize per share is `0.999 - price`. At 95c the fee is
0.05 x 0.95 x 0.05 = **0.24c per share** while the prize is 4.9c per share, so the fee eats about 5% of it; the sweeps in this
tape were bought at an average of 86c (fee 0.42c per share, prize 13.9c per share, fee = 3%). The fee would matter for a trade
whose *edge* is thin - for example buying a 50c coin flip for a 1-2c edge pays 0.05 x 0.5 x 0.5 = **1.25c per share** in fee,
which is the whole edge - but a dead-bucket NO bought at 50-97c is worth 99.9c minutes later, so the profit per share is always
many times the fee (at most about 5% of the profit at any price in the sweep window).

In short: the fee changes the dead-bucket answer by about 3%, which does not change the verdict of the earlier RESULT file
(real, small, crowded, median $0 per kill). It is not the thing to worry about; speed and getting filled at all are.

## Check: does fee 0 reproduce the original run?
Yes, exactly. The fee-0 CSV matches the original CSV cell for cell in every shared column (567 rows, 0 differences), and the
summary numbers are identical: total $23,209, median per kill $0, mean $40.93, 453 of 567 kills under $10, 114 kills >= $10,
73 kills >= $50, 28 kills >= $200, per-day median $14 and mean $137 over 169 days. The gross `prize_usd` column is also identical
between the fee-0 and fee-0.05 runs (only `fee_usd` and `prize_net_usd` differ), as intended.

## Method notes
* Per fill: `fee_usd = round(size x fee x price x (1 - price), 5)`, `prize_net = size x (0.999 - price) - fee_usd`.
* `prize_usd` in the CSV stays the gross number; `fee_usd` and `prize_net_usd` are new; every summary statistic above uses the net.
* All other rules (sweep window T+4 min to t_know+90 s, NO buys at <= 97c, valued at 99.9c) are unchanged from `tape.py`.
* Data: `cache/` from the original run (169 event files + 567 trade-tape files), nothing re-downloaded.
