#!/usr/bin/env bash
# Place-and-route + signoff reports for $TOP with OpenROAD-flow-scripts (Docker).
#
# Usage: eda/pnr.sh <out_dir> [rtl_file]
# Writes: <out_dir>/{results,logs,reports}/ and <out_dir>/pnr_summary.json
#
# The project is mounted at /work, so the space in the host path never reaches
# GNU Make. Runs as the calling user so outputs are not root-owned.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../scripts/env.sh"

out="${1:?usage: eda/pnr.sh <out_dir> [rtl_file]}"
rtl="${2:-$RTL_FILE_DEFAULT}"
mkdir -p "$out"
out="$(cd "$out" && pwd)"
rtl="$(cd "$(dirname "$rtl")" && pwd)/$(basename "$rtl")"

rel() { python3 -c 'import os,sys; print(os.path.relpath(sys.argv[1], sys.argv[2]))' "$1" "$PROJECT_ROOT"; }
c_out="/work/$(rel "$out")"

# Concrete constraints for this design (template: eda/pnr/constraint.sdc).
sed -e "s/@DESIGN_TOP@/$TOP/g" -e "s/@CLOCK_PORT@/$CLOCK_PORT/g" \
    -e "s/@CLOCK_PERIOD_NS@/$CLOCK_PERIOD_NS/g" \
    "$PROJECT_ROOT/eda/pnr/constraint.sdc" > "$out/constraint.sdc"
c_rtl="/work/$(rel "$rtl")"

if [ -n "${ORFS_NATIVE:-}" ]; then
  # Inside the container image (built on the ORFS image): run the flow directly.
  ( set +u; source /OpenROAD-flow-scripts/env.sh >/dev/null; set -e
    cd /OpenROAD-flow-scripts/flow
    DESIGN_TOP="$TOP" make DESIGN_CONFIG="$PROJECT_ROOT/eda/pnr/config.mk" RTL_FILE="$rtl" \
         RUN_SDC="$out/constraint.sdc" WORK_HOME="$out" FLOW_VARIANT=base finish
  ) > "$out/pnr.log" 2>&1 || { echo "pnr: FAIL — see $out/pnr.log"; tail -20 "$out/pnr.log"; exit 1; }
else
docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e DESIGN_TOP="$TOP" \
  -v "$PROJECT_ROOT:/work" "$ORFS_IMAGE" bash -c "
    set -e
    source /OpenROAD-flow-scripts/env.sh >/dev/null
    cd /OpenROAD-flow-scripts/flow
    make DESIGN_CONFIG=/work/eda/pnr/config.mk RTL_FILE='$c_rtl' RUN_SDC='$c_out/constraint.sdc' \
         WORK_HOME='$c_out' FLOW_VARIANT=base finish
  " > "$out/pnr.log" 2>&1 || { echo "pnr: FAIL — see $out/pnr.log"; tail -20 "$out/pnr.log"; exit 1; }
fi

python3 "$PROJECT_ROOT/eda/pnr_summary.py" "$out" > "$out/pnr_summary.json"
python3 -c 'import json,sys; s=json.load(open(sys.argv[1])); print("pnr: PASS (" + ", ".join(f"{k}={v}" for k,v in s.items() if k!="reports") + ")")' "$out/pnr_summary.json"
