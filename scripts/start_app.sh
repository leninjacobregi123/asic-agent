#!/usr/bin/env bash
# Start the Flow Console web app:  scripts/start_app.sh   then open http://localhost:8080
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh
mkdir -p runs/.console
if [ -n "${ASIC_AGENT_SUPERVISE_KB:-}" ]; then
  # Container: keep the knowledge service running for the life of the app.
  ( while true; do
      python3 -m asic_agent kb-service >> runs/.console/kb_service.log 2>&1
      echo "knowledge service exited; restarting in 3 s" >> runs/.console/kb_service.log
      sleep 3
    done ) &
else
  scripts/rag_up.sh        # knowledge service (127.0.0.1:8090)
fi
exec python3 -m asic_agent serve
