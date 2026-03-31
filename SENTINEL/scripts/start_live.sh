#!/usr/bin/env bash
# Quick helper to start SENTINEL in LIVE mode (safe wrapper)
# Usage: set ENV vars (see DEPLOYMENT_READY.txt) then run: bash scripts/start_live.sh

set -euo pipefail

if [ "${ENABLE_LIVE:-0}" != "1" ]; then
  echo "ERROR: ENABLE_LIVE is not set to 1. Aborting to avoid accidental live trading."
  exit 2
fi

if [ -z "${DHAN_ACCESS_TOKEN:-}" ] || [ -z "${DHAN_API_KEY:-}" ]; then
  echo "ERROR: Missing Dhan credentials (DHAN_ACCESS_TOKEN or DHAN_API_KEY). Aborting."
  exit 3
fi

# Optional: show configured approval phrase (masked)
if [ -n "${LIVE_APPROVAL_PHRASE:-}" ]; then
  echo "LIVE_APPROVAL_PHRASE: set (value hidden)"
else
  echo "LIVE_APPROVAL_PHRASE: not set"
fi

echo "Starting API (background)..."
nohup python3 api.py >/dev/null 2>&1 &
API_PID=$!
sleep 1

echo "Starting bot in LIVE mode..."
python3 main.py --execution LIVE --mode SCALP

# Note: kill API if bot exits
kill ${API_PID} || true
