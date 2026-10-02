#!/usr/bin/env bash
# Start the Flow Console web app:  scripts/start_app.sh   then open http://localhost:8080
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh
scripts/rag_up.sh          # knowledge service (127.0.0.1:8090)
exec python3 -m asic_agent serve
