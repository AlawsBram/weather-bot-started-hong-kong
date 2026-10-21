#!/usr/bin/env bash
# Run on the PC (Git Bash): copy the ledger backups, calibration and newest snapshot folder from the VPS.
# Copies only; never deletes anything on either side.
set -u
DEST="$(cd "$(dirname "$0")/../.." && pwd)/VPS data copies"
mkdir -p "$DEST/backups" "$DEST/snapshots"
scp -q "twapvm:hkbot/data/backups/*.sqlite" "$DEST/backups/" && echo "ledger backups -> $DEST/backups"
scp -q "twapvm:hkbot/data/calibration.json" "$DEST/" 2>/dev/null && echo "calibration.json copied"
LAST=$(ssh twapvm 'ls -1 ~/hkbot/data/snapshots | tail -n 1')
[ -n "$LAST" ] && scp -q -r "twapvm:hkbot/data/snapshots/$LAST" "$DEST/snapshots/" && echo "newest snapshot $LAST copied"
