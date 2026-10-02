#!/usr/bin/env bash
# Make sure the knowledge service is running (python -m asic_agent kb-service).
# Idempotent; returns once it answers, or after 90 s with a warning (the flow
# then runs without retrieval and the run record says so).
cd "$(dirname "${BASH_SOURCE[0]}")/.."
source scripts/env.sh
url="${RAG_URL:-http://127.0.0.1:${RAG_PORT:-8090}}"
curl -sf "$url/health" >/dev/null 2>&1 && exit 0
mkdir -p runs/.console
nohup python3 -m asic_agent kb-service >> runs/.console/kb_service.log 2>&1 &
for _ in $(seq 1 180); do
  curl -sf "$url/health" >/dev/null 2>&1 && { echo "knowledge service up" >&2; exit 0; }
  sleep 0.5
done
echo "rag_up: knowledge service did not start (see runs/.console/kb_service.log)" >&2
exit 0
