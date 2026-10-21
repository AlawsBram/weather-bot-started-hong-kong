# Weather bot started — Hong Kong

Paper-trading research bot for Polymarket's "Highest temperature in Hong Kong" markets
(resolved by the Hong Kong Observatory's Daily Extract, whole °C, cut-off rule: 31.9 counts as 31).

| Folder | What it is |
|---|---|
| `hk_weather_bot/` | v1 bot: forecasts, live HKO readings, order books, 30-minute snapshots, Telegram alerts, systemd timers (`VPS-SETUP.md`), and the no-peek history backtest (`python -m hkbot history`). |
| `hk_weather_bot/data/history/` | Backtest results, files named "backtest from <today> to <earliest date>". |
| `hk_weather_bot_v2/` | v2 research strategy: tuned on training days only, frozen with a hash, then tested once on sealed days. |
| `Updated zip+backtest/` | The zip as received. |
| `VPS data copies/` | One sample snapshot, calibration and a ledger backup copied from the server. |

Paper only. No keys are in this repository; the bot reads them from a local `.env` that is never committed.
Data: Polymarket Gamma/CLOB, Open-Meteo, HKO open data and the data.gov.hk historical archive.
