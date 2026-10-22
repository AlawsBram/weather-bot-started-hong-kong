# HK live recorder (read-only, no keys, no orders)

Measures the two things the HK dead-bucket test could not:
1. **Speed:** how many seconds after each HKO reading each public HKO source shows it
   (`csv_1min`, `csv_maxmin`, `text_hko`, `text_wx`, `rhrread`).
2. **Match:** whether each source's highest reading (cut-off rule) equals HKO's official daily max (RYES).

- `recorder.py`: always on (systemd `hk-live-recorder`); polls every 4 s, 06:30–20:30 HKT (60 s at night) with
  conditional requests; logs every NEW reading to `data/versions-YYYY-MM-DD.csv`.
- `reconcile.py`: daily 10:00 HKT (`hk-live-reconcile.timer`); writes `data/reconcile.csv` and `data/degree_times.csv`
  and sends one Telegram summary (HK weather thread, via `~/hkbot/hkbot/alerts.py`, rate-limited).
- Alerts: no new reading from any source for 25 min by day, or a source failing for 15 min (deduped 6 h).

VM: twapvm `~/hk_live_recorder`, uses `~/hkbot/.venv`. Nothing here deletes data.
