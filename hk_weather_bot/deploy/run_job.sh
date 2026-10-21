#!/usr/bin/env bash
# One scheduled job of the HK weather bot. Called by hkbot@<job>.service.
#  - a lock stops two copies of the same job overlapping (a slow snapshot just skips the next slot),
#  - output is appended to logs/<job>.log with a timestamp,
#  - a failing job sends ONE Telegram alert (rate-limited by hkbot/alerts.py).
set -u
JOB="${1:?job name}"
BOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$BOT"
mkdir -p logs data/state
PY="$BOT/.venv/bin/python"
case "$JOB" in
  snapshot)      ARGS=(snapshot) ;;
  run-tomorrow)  ARGS=(run --date tomorrow --notify) ;;
  run-today)     ARGS=(run --date today --notify) ;;
  settle)        ARGS=(settle --notify) ;;
  calibrate)     ARGS=(calibrate --days 45) ;;
  backtest)      ARGS=(backtest --days 30) ;;
  backup)        ARGS=(backup) ;;
  health)        ARGS=(health) ;;
  selftest-fail) ARGS=(no-such-command) ;;   # used once to prove the failure alert works
  *) echo "unknown job $JOB"; exit 2 ;;
esac
LOG="logs/$JOB.log"
exec 9>"data/state/lock-$JOB"
if ! flock -n 9; then
  echo "[$(date -u +%FT%TZ)] $JOB still running from the previous slot - skipped" >> "$LOG"; exit 0
fi
# A subshell ( ), not a { } group: "exit" inside must leave only the subshell, so the
# failure alert below still runs.
(
  echo "===== [$(date -u +%FT%TZ)] $JOB start"
  "$PY" -m hkbot "${ARGS[@]}"
  RC=$?
  echo "===== [$(date -u +%FT%TZ)] $JOB exit $RC"
  exit $RC
) >> "$LOG" 2>&1
RC=$?
if [ "$RC" -ne 0 ]; then
  TAIL="$(tail -n 12 "$LOG" | cut -c1-200)"
  "$PY" - "$JOB" "$RC" "$TAIL" <<'PYEOF'
import sys
from hkbot import alerts
job, rc, tail = sys.argv[1], sys.argv[2], sys.argv[3]
alerts.alert(f"job_failed:{job}", f"Scheduled job '{job}' failed (exit {rc}). Last log lines:\n{tail}")
PYEOF
fi
if [ "$JOB" = "calibrate" ] && [ "$RC" -eq 0 ]; then
  "$PY" -m hkbot backtest --days 30 >> "logs/backtest.log" 2>&1
fi
exit "$RC"
