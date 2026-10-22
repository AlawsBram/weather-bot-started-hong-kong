# 🧪 HighTempTation's "dead bucket" trick, tested on Hong Kong (2026-10-22)

> [!IMPORTANT]
> **Result:** 🔴 **At the time HKO's 10-minute "max since midnight" file shows the new degree, there's no edge.** The dead bucket's YES is already about 0.1¢ within a minute (564 trades, −0.74¢/share; NO never lost but always cost more than 97¢).
> 🟡 **But in the ~8 minutes *before* that file comes out, the price is often still wrong:** YES ≥ 3¢ in about a third of kills, ≥ 10¢ in about a sixth. A faster HKO source (the **1-minute** temperature file) could capture that. It can only be measured live.

## 📏 What was tested
- 📅 All HK days 2026-04-15 → 10-20: **567 bucket kills.** A kill is the moment HKO's max-since-midnight first reaches a new whole degree, which makes the bucket below it impossible under the cut-off rule (31.9 → 31).
- ⏱️ The kill time is when the reading first appeared in the data.gov.hk archive (usually **8 min after** HKO's reading time), so it's conservative.
- 💲 The dead bucket's YES price comes from Polymarket's **1-minute** price history, always using the last price at or before each moment (no look-ahead).
- 💸 Trade: buy NO 1 minute after the kill is public, +1¢ spread, + taker fee, hold to settlement.
- ✅ Safety check: on **0 days** did the official max come out below the since-midnight reading, so "dead" really was dead.

## 📊 Results (all 567 kills)
| Moment | YES price of the dead bucket (median / mean) | Kills with YES ≥ 3¢ |
|---|---|---|
| 10 min before the kill was public | 0.8¢ / 7.6¢ | 195 of 566 |
| **when it became public (archive time)** | 0.2¢ / 1.4¢ | 46 |
| +1 min | 0.1¢ / 0.3¢ | 5 |
| +5 min | 0.1¢ / 0.2¢ | 3 |
| +30 min | 0.1¢ / 0.1¢ | 1 |

**Measured from HKO's *reading* time instead** (April–July, 313 kills):

| Minutes after HKO's reading | YES (mean) | ≥ 3¢ | ≥ 10¢ |
|---|---|---|---|
| +0 | 6.7¢ | 118 of 312 | 57 |
| +2 | 6.2¢ | 106 | 53 |
| +5 | 5.3¢ | 101 | 46 |
| **+8 (the 10-minute file comes out)** | **2.3¢** | 48 | 18 |

## 💡 What it means (in plain words)
- 🏁 HK has its own bots that read HKO's 10-minute file and mark the dead bucket as dead within about **one minute** of that file appearing. Anyone reading the same file is too late.
- ⏳ But for about **8 minutes** between the actual reading and that file, the market still prices many dead buckets at 3–10¢ or more. Someone who knows the temperature **before** that file could buy NO at 90–97¢ on a bucket that can no longer win.
- 📡 HKO also publishes a **1-minute temperature file** (`latest_1min_temperature.csv`, Hong Kong Observatory row). If it shows the new degree a few minutes earlier, that's our window.
- ❓ It can't be backtested: the government archive only saves the 1-minute file about every 10 minutes. It needs a **live, read-only recorder** that measures:
  1. how fast HKO publishes each 1-minute reading;
  2. whether the 1-minute reading agrees with what the official daily max later counts;
  3. what the dead bucket's book (real asks, real depth) looks like during those minutes.

## ⭐ Verdict
🔴 **The 10-minute file: no edge.** 🟡 **The 1-minute file: promising, unproven.** Next step: a recorder on twapvm (no orders, no keys).

Files: `backtest from 2026-10-22 to 2026-04-15 - HK dead-bucket NO (HighTempTation idea).csv/.txt` (every kill), code `dead.py`.
