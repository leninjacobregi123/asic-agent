# Timing constraint TEMPLATE. eda/pnr.sh fills the @...@ fields from
# project.json (design.top, design.clock) and writes the result next to the
# run's outputs: ORFS reads the clock period from the file's text, so the
# values must be literal. Defaults: HCLK, 10 ns (100 MHz). For apb_gpio the
# PULP APB peripheral clock runs well below this, so positive slack is expected.
current_design @DESIGN_TOP@

set clk_period @CLOCK_PERIOD_NS@
set clk_io_pct 0.2

create_clock -name core_clock -period $clk_period [get_ports @CLOCK_PORT@]
set_input_delay  [expr $clk_period * $clk_io_pct] -clock core_clock [delete_from_list [all_inputs] [get_ports @CLOCK_PORT@]]
set_output_delay [expr $clk_period * $clk_io_pct] -clock core_clock [all_outputs]
