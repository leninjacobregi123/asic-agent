# OpenROAD-flow-scripts design config on sky130hd. The design name and clock
# come from project.json via eda/pnr.sh (DESIGN_TOP, CLOCK_PORT, CLOCK_PERIOD_NS).
# Used by eda/pnr.sh inside the pinned openroad/orfs image (config/versions.lock).
# Paths are container paths: the project is mounted at /work.
export DESIGN_NAME     = $(DESIGN_TOP)
export DESIGN_NICKNAME = $(DESIGN_TOP)
export PLATFORM        = sky130hd

export VERILOG_FILES   = $(RTL_FILE)
export SDC_FILE        = $(RUN_SDC)

# ~1.5k cells / ~20k um^2 after synthesis (M1). 40% leaves routing room.
export CORE_UTILIZATION = 40
export PLACE_DENSITY    = 0.6
export SYNTH_HIERARCHICAL = 0
