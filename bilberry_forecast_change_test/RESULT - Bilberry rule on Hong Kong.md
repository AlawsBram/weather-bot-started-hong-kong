# 🧪 Bilberry "forecast-change" rule — Hong Kong backtest (2026-10-22)

> [!IMPORTANT]
> **Result: 🔴 NO EDGE in Hong Kong.** 380 trades, −$510 on $9,500 staked (−5.4%). The bucket we bought won **24.2%** of the time, and we paid **23.9¢** on average, so **the hit rate equals the price paid.**

## 📏 The rule (copied from the tweet, nothing tuned)
Source: https://x.com/xmayeth/status/2108586474715185534 (@xmayeth), about trader **Bilberry** (`0xbf13934a1fec7d3211fc15c138d84ac2a691b91a`)
1. When a new **ECMWF** or **GFS** run is out, compare tomorrow's HK max with the **previous run** of the same model.
2. If it moved **≥ 0.5 °C**, buy the bucket **one degree away from the market's favourite, in the direction of the move**.
3. Only if that bucket costs **5¢–60¢**. **$25** per trade.

## 🔒 How peeking was prevented
- ⏰ A run only counts from the moment it's **public**: start time + **7.8 h (ECMWF)** / **6.4 h (GFS)**, measured from Open-Meteo's own metadata, **+ 5 min** for the bot to react.
- 💲 The buy uses the **first 5-minute price at or after** that moment, never an earlier one. This uses Polymarket's 5-minute history, which also fixes the 1-hour price-lag trap from v2.
- 🧾 Every past run comes from Open-Meteo's single-runs archive (8 of about 1,500 runs were missing). The answer (HKO max and the settled winner) is used only to settle trades.
- 💸 Costs: 1¢ half-spread on every buy and sell + the taker fee 0.05·p·(1−p).
- 🧊 Nothing was tuned, so all 187 settled days (2026-04-15 → 10-20) are test days.

## 📊 Results
| | Trades | Bucket won | Avg price | Hold to the end | Sell after 3 h | Price move in the 3 h after buying |
|---|---|---|---|---|---|---|
| **All** | 380 | 24.2% | 23.9¢ | 🔴 **−$510 (−5.4%)** | 🔴 −$1,037 | +1.0¢ average (199 up / 143 down) |
| ECMWF | 219 | 25.1% | 23.2¢ | 🔴 −$118 (−2.2%) | 🔴 −$541 | +1.2¢ |
| GFS | 161 | 23.0% | 24.7¢ | 🔴 −$392 (−9.7%) | 🔴 −$497 | +0.7¢ |

By month (hold to the end): Apr +$292 · May +$294 · Jun −$437 · Jul −$273 · Aug −$184 · Sep −$226 · Oct +$23. **Not stable.**

🟡 **Generous re-run:** if we pretend we get the runs **1.5 h earlier** than they're public, it's still **−$391 (−4.1%)**. Being faster doesn't rescue it.

## 💡 What it means (in plain words)
- 🧠 After a forecast change, the price **does** drift the right way, but only about **1¢**. Getting in and out costs about **2–3¢** (spread + fee), so the small push is eaten by costs.
- 🏠 In HK the market reacts to model updates almost straight away. That matches v2, where the market already "knows" the forecast.
- 🔬 **Bilberry's real bot is different from the tweet.** His wallet shows he trades **every hour at :10–:18 past**, all day, sells often, and makes only **+$1,007 in HK** (114 positions). Most of his profit comes from US/Europe/Korea cities in Sep–Oct 2026. So the tweet describes a simplified version, not what actually earns.

## ⭐ Verdict
🔴 **The tweet's rule doesn't work in HK.** Not worth building as written.
🟡 What *might* be worth it: his hourly timing hints that he reacts to **hourly data** (airport weather reports, or fast-updating models) in cities other than HK. That would be a new research project, outside HK.

Files: `backtest from 2026-10-22 to 2026-04-15 - Bilberry forecast-change rule HK.csv/.txt` (every trade) and the "(OPTIMISTIC - pretends runs public 1.5h earlier)" pair. Code: `bt.py`.
