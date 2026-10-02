#!/usr/bin/env bash
# Synthesise $TOP to sky130_fd_sc_hd (typical corner) with Yosys + ABC.
#
# Usage: eda/synth.sh <out_dir> [rtl_file]     (default: $RTL_DIR/$TOP.sv)
# Writes: <out_dir>/$TOP.netlist.v, synth.log, stat.txt
#
# Fails (non-zero) on: any latch, any `check` problem, or any cell left
# unmapped to the liberty library.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/../scripts/env.sh"

out="${1:?usage: eda/synth.sh <out_dir> [rtl_file]}"
rtl="${2:-$RTL_FILE_DEFAULT}"
mkdir -p "$out"
out="$(cd "$out" && pwd)"

# Yosys script paths are passed through a file so a space anywhere in the
# project path cannot split an argument.
ys="$out/synth.ys"
cat > "$ys" <<EOF
read_liberty -lib "$LIB_TT"
read_verilog -sv "$rtl"
hierarchy -check -top $TOP
synth -top $TOP -flatten
select -assert-none t:\$_DLATCH_*
dfflibmap -liberty "$LIB_TT"
abc -liberty "$LIB_TT"
opt_clean -purge
check -assert
select -assert-none t:\$_*
tee -o "$out/stat.txt" stat -liberty "$LIB_TT"
write_verilog -noattr "$out/$TOP.netlist.v"
EOF

yosys -q -l "$out/synth.log" -s "$ys"

cells=$(grep -m1 -E "^ +[0-9]+ +[0-9.]+ +cells$|cells$" "$out/stat.txt" | awk '{print $1}')
area=$(grep -m1 -iE "chip area" "$out/stat.txt" | awk '{print $NF}')
echo "synth: PASS ($TOP -> $STD_CELL_LIB tt_025C_1v80, cells=${cells:-?}, area=${area:-?} um^2)"
