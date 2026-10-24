# reward_pool_logger

A tiny **read-only** logger. Once a day it writes down how much money Polymarket
is paying market makers ("Liquidity Rewards") on every daily-temperature market,
city by city. It places no orders, needs no API keys, reads no `.env`, and sends
no Telegram messages. The deliverable is a CSV file you can open in Excel.

## Where it lives
- VM `twapvm` (34.7.18.36): `~/reward_pool_logger/`
- Local copy (identical): `C:\Users\Administrator\polymarket-bot\Speed+VPS,weather bot+btc-5-minute bot\Weather bot starting from scratch\reward_pool_logger\`

## What it does
1. Pulls the full list of rewarded markets from
   `clob.polymarket.com/rewards/markets/current` (paged with `next_cursor`).
2. Pulls every active temperature event from
   `gamma-api.polymarket.com/events?tag_id=103040` (paged with `offset`).
3. Joins them on `condition_id` and writes:

| File | What is in it |
|---|---|
| `data/reward_pools.csv` | one row per (run date, city, event date, highest/lowest). Columns: `n_markets`, `n_rewarded`, `pool_usd_per_day_total` (sum of the buckets' daily reward), `max_spread` (cents), `min_size` (shares), `fee_rate_from_gamma`, `top_bucket_question`, `top_bucket_pool`. Appends every day, header once. |
| `data/reward_markets_<date>.csv.gz` | raw per-bucket rows for that day (condition_id, slug, question, pool, max_spread, min_size, fee_rate, rewarded yes/no). gzip, open with 7-zip or `zcat`. |
| `data/btc_rewarded.csv` | one row per day per slug: is the current `btc-updown-5m-*` / `btc-updown-15m-*` round in the rewards list? yes/no. |
| `logs/run_<date>.log` | what happened on that day's run, ends with `SUMMARY` lines (top 10 cities, Hong Kong pool, fee summary, BTC yes/no). `logs/run.log` points at the latest one. Logs older than 60 days are deleted. |

## How to read it
- One row = one **event** = one city, one event date, highest OR lowest. Its
  `pool_usd_per_day_total` is the daily reward on that event (NYC ~$100-104).
- The "NYC $604 per day" figure = the **sum over all of NYC's active events**
  (3 dates x highest+lowest x ~$100). To get it: filter one `date_utc`, group by
  `city`, sum `pool_usd_per_day_total`. The log's `SUMMARY top10` line already does this.
- Typically only 3 of the 11 buckets per event carry a reward line (`n_rewarded`).
- Re-running on the same UTC day replaces that day's rows (no duplicates).
- `n_rewarded` < `n_markets` means some buckets have a $0 reward line.
- `max_spread 4.5` means your quote has to be within 4.5 cents of the midpoint to earn;
  `min_size 20` means at least 20 shares resting.
- `fee_rate_from_gamma 0.0` means no taker fee on that city (most of them).
- A city missing from a day means Polymarket had no active event for it that day.

Quick look on the VM:
```
tail -20 ~/reward_pool_logger/logs/run.log
grep SUMMARY ~/reward_pool_logger/logs/run.log
column -s, -t < ~/reward_pool_logger/data/reward_pools.csv | less -S
```

## Schedule
systemd timer `reward-pool-logger.timer` fires daily at **00:20 UTC** (pools are set
per UTC day; `Persistent=true` so a missed run catches up at boot). The service is
`reward-pool-logger.service` (Type=oneshot, runs as the login user).

```
systemctl list-timers | grep reward          # next/last run
systemctl status reward-pool-logger.service  # last result
sudo systemctl start reward-pool-logger.service   # run it now by hand
```

## How to stop it
```
sudo systemctl disable --now reward-pool-logger.timer
```
That is all; the data files stay where they are.

## Re-deploying after a code change
From the Windows PC (Git Bash):
```
cd "C:/Users/Administrator/polymarket-bot/Speed+VPS,weather bot+btc-5-minute bot/Weather bot starting from scratch/reward_pool_logger"
scp reward_pool_logger.py README.md twapvm:~/reward_pool_logger/
```
Unit files only change if you edit them: `scp reward-pool-logger.* twapvm:/tmp/ && ssh twapvm 'sudo cp /tmp/reward-pool-logger.* /etc/systemd/system/ && sudo systemctl daemon-reload'`.
