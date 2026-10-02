#!/usr/bin/env bash
# Step 0 of the verification loop: verilator --lint-only -Wall.
# A non-zero exit counts as an attempt with root_cause=implementation_bug.
#
# Usage: eda/lint.sh [rtl_file ...]      (default: $RTL_DIR/$TOP.sv)
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../scripts/env.sh"

files=("$@")
[ ${#files[@]} -gt 0 ] || files=("$RTL_FILE_DEFAULT")

# Per-design waivers are optional: eda/lint/<top>.vlt, each one justified.
waivers=()
[ -f "$PROJECT_ROOT/eda/lint/$TOP.vlt" ] && waivers=("$PROJECT_ROOT/eda/lint/$TOP.vlt")

verilator --lint-only -Wall \
  --top-module "$TOP" \
  "${waivers[@]}" \
  "${files[@]}"
echo "lint: PASS ($TOP, $(verilator --version | cut -d' ' -f1-2))"
