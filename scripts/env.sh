# Source this before any flow script:   source scripts/env.sh
#
# Puts the pinned toolchain on PATH, exports the PDK paths, and exports the
# active design (from config/project.json) for the EDA scripts. Every version
# here is pinned; config/versions.lock records the same values. Change a pin
# only deliberately, and re-run the regression after doing so.
#
# Machine-specific locations can be overridden before sourcing:
#   OSS_CAD_SUITE, PDK_ROOT, BUILD_DIR, ORFS_IMAGE, ASIC_AGENT_PROJECT

_here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PROJECT_ROOT="$_here"
export PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

export OSS_CAD_SUITE="${OSS_CAD_SUITE:-$HOME/eda/oss-cad-suite-20260925}"
export PDK_ROOT="${PDK_ROOT:-$HOME/eda/pdk}"
export PDK="sky130A"
export PDK_VERSION="1689ac3f2dc763876eaf967227c7dfe831b031ae"
export STD_CELL_LIB="sky130_fd_sc_hd"
export LIB_TT="$PDK_ROOT/ciel/sky130/versions/$PDK_VERSION/$PDK/libs.ref/$STD_CELL_LIB/lib/${STD_CELL_LIB}__tt_025C_1v80.lib"

# GNU Make (used by Verilator) refuses to build in a directory whose path
# contains a space, so build products live outside the repository.
export BUILD_DIR="${BUILD_DIR:-$HOME/.cache/asic-agent/build}"
case "$BUILD_DIR" in
  *" "*) echo "env.sh: BUILD_DIR must not contain spaces: '$BUILD_DIR'" >&2 ;;
esac

# Place-and-route runs in Docker (eda/pnr.sh). Pinned by tag; digest in config/versions.lock.
export ORFS_IMAGE="${ORFS_IMAGE:-openroad/orfs:26Q3-705-gecb3cfdeb}"

# The design comes from the active project file (the one design-specific file).
_project="${ASIC_AGENT_PROJECT:-$PROJECT_ROOT/config/project.json}"
eval "$(python3 - "$_project" "$PROJECT_ROOT" <<'PY'
import json, os, shlex, sys
p = json.load(open(sys.argv[1]))
root = sys.argv[2]
d = p["design"]
rtl = os.path.join(root, d["rtl"])
clk = d.get("clock", {})
for k, v in (("TOP", d["top"]), ("RTL_DIR", os.path.dirname(rtl)),
             ("RTL_FILE_DEFAULT", rtl), ("CLOCK_PORT", clk.get("port", "HCLK")),
             ("CLOCK_PERIOD_NS", str(clk.get("period_ns", 10.0))),
             ("TB_FILE", os.path.join(root, p["testbench"]["file"]))):
    print(f"export {k}={shlex.quote(str(v))}")
PY
)"

case ":$PATH:" in
  *":$OSS_CAD_SUITE/bin:"*) ;;
  *) export PATH="$OSS_CAD_SUITE/bin:$PATH" ;;
esac

for _p in "$OSS_CAD_SUITE/bin/verilator" "$OSS_CAD_SUITE/bin/yosys" "$LIB_TT"; do
  [ -e "$_p" ] || echo "env.sh: missing $_p — see docs/SETUP.md" >&2
done
unset _here _p _project
