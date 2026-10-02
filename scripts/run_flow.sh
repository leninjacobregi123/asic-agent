#!/usr/bin/env bash
# One command: spec -> RTL -> verify -> synth -> gate-level verify -> PnR/signoff
# for the active project's design, with diagnose-and-route on every
# verification failure; or a change request with --request-file.
#
#   scripts/run_flow.sh                         # interactive approval gates
#   scripts/run_flow.sh --auto-approve          # gates printed, not asked
#   scripts/run_flow.sh --auto-approve --inject int_rise_bug
#   scripts/run_flow.sh --request-file request.txt --auto-approve
#   scripts/run_flow.sh --no-llm                # rule-based diagnosis only
#
# The model comes from config/llm.toml; override per run with
# LLM_PROVIDER / LLM_MODEL. LLM_NO_FALLBACK=1 disables the configured fallback
# model (for runs that must not mix models).
set -euo pipefail
cd "$(dirname "$0")/.."
source scripts/env.sh
scripts/rag_up.sh
exec python3 -m asic_agent run "$@"
