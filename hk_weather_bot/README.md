# Hong Kong temperature bot (Polymarket, paper trading)

Automates the exact manual method from our walkthrough for
"Highest temperature in Hong Kong on <date>" markets, then scores itself
against the market every day so you can see whether it actually has an edge.

**Paper trading only.** It never places real orders. Real money only makes
sense after a version beats the market's Brier score over 30+ settled days.

## Setup

```bash
cd hk_weather_bot
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q          # 20 tests, incl. the real Oct 2026 settlements
```

## Daily routine

| When (Nairobi time) | Command | What it does |
|---|---|---|
| Evening (after ~17:00) | `python -m hkbot run` | Predicts **tomorrow**, logs forecasts, places paper trades |
| Next morning ~06:30–09:00 | `python -m hkbot run --date today` | Re-runs using the newer HKO bulletin and live max-so-far (shows ideas; won't double-trade unless `--retrade`) |
| Next evening | `python -m hkbot settle` | Fetches the winner + HKO reading, scores each version, books P&L |
| Any time | `python -m hkbot report` | Compares versions: Brier vs market, P(winner), paper P&L |

Once a week:

```bash
python -m hkbot calibrate --days 45      # fit each source's warm/cool bias + uncertainty
python -m hkbot backtest --days 30 --verbose
```

## No-peek backtest on real past markets (run this first)

```bash
python -m hkbot history --start 2026-04-15 --train-end 2026-08-31 --decision evening
python -m hkbot history --start 2026-04-15 --train-end 2026-08-31 --decision morning
```

For each past market day D, it rebuilds **only what was knowable at the decision time**:

| Decision | When | Model forecasts used |
|---|---|---|
| `evening` | 18:00 HKT on D−1 | as issued **2 days** earlier (deliberately conservative) |
| `morning` | 09:00 HKT on D | as issued **1 day** earlier |

| Input | Source | Peeking guard |
|---|---|---|
| ECMWF / GFS / ICON | Open-Meteo **Previous Runs** API | forecast exactly as it was issued, never a later run |
| Polymarket prices | CLOB price history | last price **at or before** the decision time; later points are ignored, and prices older than 36 h are discarded |
| Source biases | HKO readings | only days ≤ D−2 (D−1's reading isn't published yet) |
| The answer (HKO daily max) | `CLMMAXT` | read **only after** the probabilities are locked |

Then it:
1. **Tunes** the uncertainty, the market blend and the minimum edge on the **training** days only.
2. **Freezes** those settings.
3. **Scores** the untouched **test** days against the market's price at the same moment, with paper trades that pay a 2¢ spread penalty (`--spread`).
4. Checks every settled day against the cut-off rule.
5. Writes per-day results to `data/history/backtest_*.csv`.

How to read the verdict:
- If the tuner picks `market blend 0.0`, the model added nothing on training data. That's an honest "no edge".
- Only trust a result where the **test** Brier beats the market over **30+ test days**, and both `evening` and `morning` look sane.
- Profit on cheap buckets assumes you could buy at that price. HK markets are thin, so treat large profit from many 2–5¢ tickets with suspicion.
- The HKO official forecast isn't archived, so the history backtest uses the models only. The live bot logs HKO forecasts daily, so they enter later backtests automatically.

Everything downloaded is cached in `data/history/cache/` (inspectable JSON). Use `--refresh` to re-download.

## How it maps to what we did by hand

| Manual step | In the bot |
|---|---|
| 1. Read the rules, right station | Hard-coded to HKO HQ; `settle` checks every result against the **cut-off rule** and flags a mismatch |
| 2. Official forecast (HKO 9-day) | `sources.fetch_hko_9day()` |
| 3. Other models (Windy: ECMWF/GFS/ICON) | Open-Meteo at the HKO coordinates, hourly → true daily peak (no 3-hour gaps) |
| 4. Bias check vs recent actuals | `calibrate` measures each source's average error; GFS's cool bias gets corrected and down-weighted |
| 5. Build the odds ladder | Weighted best guess + normal spread, **bucket k = readings k.0–k.9** |
| Lesson: 30 °C at 2 % was too low | `plausible_floor` = 8 % for any bucket a source points to |
| Lesson: respect the market | `market_blend` = 50 % model / 50 % market |
| 6. Compare with prices | Edge measured against the **ask** (YES) or **1 − bid** (NO), min 6 points, skips <3¢ and >97¢ |
| 7. Watch live readings | `--date today` uses HKO "max since midnight": buckets below it drop to 0 |
| Audit (Brier, P&L) | `settle` + `report`, per version |

## Strategy versions (kept side by side)

Defined in `hkbot/config.py` → `STRATEGIES`. Every run records **all**
versions, so they're forward-tested in parallel.

- `v1_naive`: our first manual attempt (HKO only, rounds instead of cutting off, ignores the market). Kept as the baseline to beat.
- `v2_audited`: everything learned in the audit.

To try an idea, copy `v2_audited` to `v3_…`, change one setting, and let it run alongside.

## Oct 19 replay (in the tests)

Same inputs we gathered by hand (HKO 32, ECMWF 30, meteoblue/ICON 30, GFS 28); 31 °C won.

| | P(31) | P(32) | Brier | Trades |
|---|---|---|---|---|
| Manual v1 | 40 % | 51 % | 0.62 | NO 31, YES 32 (lost) |
| `v1_naive` | 24 % | 42 % | 0.81 | reproduces the manual trades |
| `v2_audited` | **42 %** | 25 % | **0.44** | NO 31 only |
| Market | 53 % | 30 % | 0.33 | — |

v2 ranks 31 first and avoids the YES-32 trap, but the market was still better
calibrated that day. v2 also still bets NO 31 because its uncertainty
(±1.16 °C) is wider than the market's. That's what `calibrate` is for: it
tunes the uncertainty on 30–45 real days rather than one.

## Files

- `hkbot/buckets.py`: bucket labels and the cut-off rule
- `hkbot/sources.py`: Polymarket Gamma API, HKO open data, Open-Meteo
- `hkbot/model.py`: forecasts → bucket probabilities
- `hkbot/strategy.py`: edge rules
- `hkbot/scoring.py`: Brier, log-loss, P&L
- `hkbot/backtest.py`: walk-forward backtest and calibration
- `hkbot/ledger.py`: SQLite at `data/ledger.sqlite`
- `hkbot/cli.py`: commands

## First-run checks

The endpoints are written from the providers' public APIs but couldn't be
called from where this was built. On your first run, use `python -m hkbot -v run`
to see request details. If a source prints nothing, check its URL in
`hkbot/config.py`:

- HKO daily max (`CLMMAXT`) should match the Daily Extract page (e.g. Oct 15 = 29.7).
- HKO max-since-midnight CSV is only needed for `--date today`.
- The HKO official forecast only accumulates history from the day you start
  running the bot, so its bias estimate improves over the first couple of weeks.
