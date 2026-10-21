# HK weather bot — version 2 (research)

**Result: no edge found.** On 49 sealed test days (2026-09-01 .. 10-19) v2 matched the market at
evening, morning and noon (it chose to copy it) and at 14:00 made **+$2.01 on 12 paper trades** —
a coin-flip amount, not a strategy. Full numbers: `results/backtest from 2026-10-21 to 2026-04-15 - v2 TEST RESULT.txt`.

## The fine-tuning idea (each piece is one knob)

1. **Uncertainty from its own misses** — how far the combined forecast actually missed over the last
   20 or 40 known days, instead of guessing from how much the models disagree.
2. **Recent-bias fix** — if the forecast ran hot lately, take that off.
3. **Market sharpening (gamma)** — tests the classic betting-market habit of over-pricing long shots.
4. **Geometric pooling** — market^(1-w) × model^w, so a cheap bucket only gets more likely when both agree.
5. **Trade filter** — edge must clear the 2c spread + 5% taker fee; options: price cap, NO-only.

## No peeking — how

- **Same-hour data:** the CLOB hourly price is 60 min before the decision, so HKO's live reading and
  HKO's bulletin are also taken at that earlier moment (found 2026-10-21: otherwise a fake +$364).
- **Sealed test:** `v2/data.py` refuses to load test days until `results/FROZEN_v2.json` exists.
- **Freeze with a fingerprint:** the settings' SHA-256 (`d04e7633a3920e21`) is checked before testing; an edit is detected (`tests/test_seal.py`).
- **Walk-forward inside training:** June, July and August each scored with settings picked on earlier months only.
- **Learning lag:** day D only learns from readings of days <= D-2.

## What happened

| Time | Training walk-forward (Jun–Aug) | Tuner chose | Sealed test (49 days) |
|---|---|---|---|
| Evening 18:00 | −$16.32 | copy market | tie, 0 trades |
| Morning 09:00 | −$145.13 | copy market | tie, 0 trades |
| Noon 12:00 | −$53.68 | copy market | tie, 0 trades |
| Afternoon 14:00 | −$84.80 | 15% model | Brier 0.467 vs 0.472, +$2.01 on 12 trades |

Training exploration (`results/explore_train (training days only).txt`): the market's prices match real
win rates within noise, its average guess is unbiased (+0.00 °C), and it misses by 0.46–0.84 °C vs the
models' 0.81–1.03 °C. When models disagree with the market, the market is the one that is right.

## Run

```
python -m v2 tune    # training only; refuses to run once frozen
python -m v2 test    # opens the sealed test with the frozen settings
python -m pytest -q tests
```
Uses the v1 downloaders and cache in `../hk_weather_bot/` (read-only).
