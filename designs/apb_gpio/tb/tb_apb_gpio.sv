// Testbench harness for apb_gpio.
//
// One compiled binary, many tests: the test is selected at run time with
// +TEST=<name>, so the runner (asic_agent.eda.runner) can fan tests out across cores
// as separate processes. Each test prints exactly one result line:
//
//     RESULT <name> PASS
//     RESULT <name> FAIL <first failing check>
//
// Every check states the spec clause it tests (see designs/apb_gpio/docs/apb_gpio_regs.md),
// so a failure log carries the evidence the verification agent needs to decide
// between implementation_bug and spec_ambiguity.

`timescale 1ns/1ps

module tb_apb_gpio;

  localparam int PAD_NUM = 32;

  // Register offsets, from the spec register map (not from the RTL defines,
  // so a wrong define in the RTL is caught rather than mirrored).
  localparam logic [11:0] PADDIR    = 12'h000;
  localparam logic [11:0] GPIOEN    = 12'h004;
  localparam logic [11:0] PADIN     = 12'h008;
  localparam logic [11:0] PADOUT    = 12'h00C;
  localparam logic [11:0] PADOUTSET = 12'h010;
  localparam logic [11:0] PADOUTCLR = 12'h014;
  localparam logic [11:0] INTEN     = 12'h018;
  localparam logic [11:0] INTTYPE0  = 12'h01C;
  localparam logic [11:0] INTTYPE1  = 12'h020;
  localparam logic [11:0] INTSTATUS = 12'h024;
  localparam logic [11:0] PADCFG0   = 12'h028;
  localparam logic [11:0] PADCFG3   = 12'h034;
  localparam logic [11:0] PADDIR_HI = 12'h038;

  logic        clk = 1'b0;
  logic        rst_n = 1'b0;
  logic [11:0] paddr = '0;
  logic [31:0] pwdata = '0;
  logic        pwrite = 1'b0, psel = 1'b0, penable = 1'b0;
  logic [31:0] prdata;
  logic        pready, pslverr;
  logic [PAD_NUM-1:0] gpio_in = '0;
  logic [PAD_NUM-1:0] gpio_in_sync, gpio_out, gpio_dir;
  logic [PAD_NUM-1:0][3:0] gpio_padcfg;
  logic        irq;

  // Gate level: the synthesised netlist is flat and parameter-free (its
  // parameters were fixed at these values during synthesis).
`ifdef GATE_LEVEL
  apb_gpio dut (
`else
  apb_gpio #(.APB_ADDR_WIDTH(12), .PAD_NUM(PAD_NUM), .NBIT_PADCFG(4)) dut (
`endif
    .HCLK(clk), .HRESETn(rst_n), .dft_cg_enable_i(1'b0),
    .PADDR(paddr), .PWDATA(pwdata), .PWRITE(pwrite), .PSEL(psel),
    .PENABLE(penable), .PRDATA(prdata), .PREADY(pready), .PSLVERR(pslverr),
    .gpio_in(gpio_in), .gpio_in_sync(gpio_in_sync), .gpio_out(gpio_out),
    .gpio_dir(gpio_dir), .gpio_padcfg(gpio_padcfg), .interrupt(irq)
  );

  always #5 clk = ~clk;

  // ---- result bookkeeping -------------------------------------------------
  string test_name;
  int    n_checks = 0;
  string first_fail = "";

  task automatic check(input bit ok, input string what);
    n_checks++;
    if (!ok && first_fail == "") first_fail = what;
    if (!ok) $display("  CHECK FAIL: %s", what);
  endtask

  task automatic check_eq(input logic [31:0] got, input logic [31:0] exp, input string what);
    check(got === exp, $sformatf("%s: got 0x%08h expected 0x%08h", what, got, exp));
  endtask

  // ---- APB master ---------------------------------------------------------
  // Signals change 1ns after the rising edge so the DUT samples them cleanly.
  task automatic apb_write(input logic [11:0] addr, input logic [31:0] data);
    @(posedge clk); #1;
    paddr = addr; pwdata = data; pwrite = 1'b1; psel = 1'b1; penable = 1'b0;
    @(posedge clk); #1;
    penable = 1'b1;
    @(posedge clk); #1;
    psel = 1'b0; penable = 1'b0; pwrite = 1'b0;
  endtask

  task automatic apb_read(input logic [11:0] addr, output logic [31:0] data);
    @(posedge clk); #1;
    paddr = addr; pwrite = 1'b0; psel = 1'b1; penable = 1'b0;
    @(posedge clk); #1;
    penable = 1'b1;
    #1 data = prdata;          // sampled in the access phase, before the edge
    check(pready === 1'b1, $sformatf("PREADY high during read of 0x%03h", addr));
    check(pslverr === 1'b0, $sformatf("PSLVERR low during read of 0x%03h", addr));
    @(posedge clk); #1;
    psel = 1'b0; penable = 1'b0;
  endtask

  task automatic idle(input int n);
    repeat (n) @(posedge clk);
    #1;
  endtask

  // Watch the interrupt line for n cycles; return how many cycles it was high.
  task automatic count_irq(input int n, output int high);
    high = 0;
    repeat (n) begin
      @(posedge clk); #1;
      if (irq) high++;
    end
  endtask

  // ---- tests ----------------------------------------------------------------
  logic [31:0] rd;
  int          hi;

  // Spec: every register has default value 0x0.
  task automatic t_reset_values();
    apb_read(PADDIR, rd);    check_eq(rd, 0, "PADDIR reset value");
    apb_read(GPIOEN, rd);    check_eq(rd, 0, "GPIOEN reset value");
    apb_read(PADIN, rd);     check_eq(rd, 0, "PADIN reset value");
    apb_read(PADOUT, rd);    check_eq(rd, 0, "PADOUT reset value");
    apb_read(INTEN, rd);     check_eq(rd, 0, "INTEN reset value");
    apb_read(INTTYPE0, rd);  check_eq(rd, 0, "INTTYPE_00_15 reset value");
    apb_read(INTTYPE1, rd);  check_eq(rd, 0, "INTTYPE_16_31 reset value");
    apb_read(INTSTATUS, rd); check_eq(rd, 0, "INTSTATUS reset value");
    apb_read(PADCFG0, rd);   check_eq(rd, 0, "PADCFG_00_07 reset value");
    check(gpio_out === '0 && gpio_dir === '0 && irq === 1'b0,
          "outputs (gpio_out, gpio_dir, interrupt) low after reset");
  endtask

  // Spec: PADDIR R/W; bit[i]=1 output mode for GPIO[i].
  task automatic t_dir_rw();
    apb_write(PADDIR, 32'hA5A5_0F0F);
    apb_read(PADDIR, rd); check_eq(rd, 32'hA5A5_0F0F, "PADDIR readback");
    check_eq(gpio_dir, 32'hA5A5_0F0F, "gpio_dir follows PADDIR");
  endtask

  // Spec: PADOUT R/W; DATA_OUT[i] is the output data set on GPIO[i].
  task automatic t_out_rw();
    apb_write(PADOUT, 32'h1234_5678);
    apb_read(PADOUT, rd); check_eq(rd, 32'h1234_5678, "PADOUT readback");
    check_eq(gpio_out, 32'h1234_5678, "gpio_out follows PADOUT");
  endtask

  // Spec: PADOUTSET bit=1 sets GPIO[i], bit=0 no change; PADOUTCLR likewise clears.
  task automatic t_out_set_clr();
    apb_write(PADOUT, 32'h0000_00F0);
    apb_write(PADOUTSET, 32'h0000_0F00);
    check_eq(gpio_out, 32'h0000_0FF0, "PADOUTSET sets only the written-1 bits");
    apb_write(PADOUTCLR, 32'h0000_0030);
    check_eq(gpio_out, 32'h0000_0FC0, "PADOUTCLR clears only the written-1 bits");
    apb_read(PADOUT, rd); check_eq(rd, 32'h0000_0FC0, "PADOUT reflects set/clear");
  endtask

  // Spec: INTTYPE and PADCFG are R/W with per-pad field packing.
  task automatic t_cfg_rw();
    apb_write(INTTYPE0, 32'h9C63_1E27);
    apb_read(INTTYPE0, rd); check_eq(rd, 32'h9C63_1E27, "INTTYPE_00_15 readback");
    apb_write(INTTYPE1, 32'h1357_9BDF);
    apb_read(INTTYPE1, rd); check_eq(rd, 32'h1357_9BDF, "INTTYPE_16_31 readback");
    apb_write(PADCFG0, 32'hFEDC_BA98);
    apb_read(PADCFG0, rd); check_eq(rd, 32'hFEDC_BA98, "PADCFG_00_07 readback");
    check(gpio_padcfg[0] === 4'h8 && gpio_padcfg[7] === 4'hF,
          "gpio_padcfg[i] = PADCFG_00_07[4i+3:4i]");
    apb_write(PADCFG3, 32'h0000_0005);
    check(gpio_padcfg[24] === 4'h5, "gpio_padcfg[24] = PADCFG_24_31[3:0]");
    apb_write(INTEN, 32'h8000_0001);
    apb_read(INTEN, rd); check_eq(rd, 32'h8000_0001, "INTEN readback");
  endtask

  // Spec: DATA_IN[i] is the input data of GPIO[i]; clock must be enabled
  // (GPIOEN) for a GPIO in input mode. The spec states no latency, so this
  // measures it and only requires the value to appear within 4 cycles.
  task automatic t_padin_sync();
    int lat;
    apb_write(GPIOEN, 32'hFFFF_FFFF);
    gpio_in = 32'hC0FF_EE01;
    lat = -1;
    for (int c = 1; c <= 4 && lat < 0; c++) begin
      @(posedge clk); #1;
      if (dut.r_gpio_in === 32'hC0FF_EE01) lat = c;
    end
    $display("  measured PADIN latency: %0d cycles", lat);
    check(lat > 0, "gpio_in reaches PADIN within 4 cycles when GPIOEN=1");
    apb_read(PADIN, rd); check_eq(rd, 32'hC0FF_EE01, "PADIN readback");
  endtask

  // Spec: GPIOs are clock-gated in groups of 4; a group is gated only if all 4
  // are disabled. Group 0 enabled via pad 1 only -> pads 0..3 all sample;
  // group 1 fully disabled -> pads 4..7 do not.
  task automatic t_padin_gating();
    apb_write(GPIOEN, 32'h0000_0002);
    gpio_in = 32'h0000_00FF;
    idle(6);
    apb_read(PADIN, rd);
    check_eq(rd & 32'hFF, 32'h0F, "group of 4 samples if any member enabled; disabled group holds");
  endtask

  // Spec: INTTYPE 00 falling, 01 rising, 10 both. INTSTATUS[i]=1 on interrupt.
  task automatic int_edge(input logic [1:0] ty, input bit rise, input bit expect_int,
                          input string what);
    apb_write(GPIOEN, 32'h0000_0001);
    apb_write(INTTYPE0, {30'b0, ty});
    apb_write(INTEN, 32'h0000_0001);
    gpio_in[0] = rise ? 1'b0 : 1'b1;
    idle(6);
    apb_read(INTSTATUS, rd);                 // discard any edge from setup
    gpio_in[0] = rise ? 1'b1 : 1'b0;
    count_irq(6, hi);
    apb_read(INTSTATUS, rd);
    check_eq(rd & 1, expect_int ? 1 : 0, {what, ": INTSTATUS[0]"});
    check((hi > 0) == expect_int, {what, ": interrupt line activity"});
  endtask

  task automatic t_int_rise();
    int_edge(2'b01, 1'b1, 1'b1, "rise type, rising edge");
    int_edge(2'b01, 1'b0, 1'b0, "rise type, falling edge");
  endtask

  task automatic t_int_fall();
    int_edge(2'b00, 1'b0, 1'b1, "fall type, falling edge");
    int_edge(2'b00, 1'b1, 1'b0, "fall type, rising edge");
  endtask

  task automatic t_int_both();
    int_edge(2'b10, 1'b1, 1'b1, "both type, rising edge");
    int_edge(2'b10, 1'b0, 1'b1, "both type, falling edge");
  endtask

  // OPTIONAL (driver --inject spec_gap). Spec: INTTYPE 2'b11 is "RFU" and
  // nothing more. This test assumes it means a level-high interrupt, as many
  // GPIO controllers do. The spec never says so: a deliberate spec gap for
  // exercising the spec_ambiguity route. Not in the default suite.
  task automatic t_inttype_rfu_level();
    apb_write(GPIOEN, 32'h0000_0001);
    gpio_in[0] = 1'b1;
    idle(6);                                 // pad already high, no edge to come
    apb_write(INTTYPE0, 32'h3);
    apb_write(INTEN, 32'h1);
    idle(4);
    apb_read(INTSTATUS, rd);
    check_eq(rd & 1, 1, "INTTYPE=11 level interrupt: INTSTATUS[0] while GPIO0 is high");
  endtask

  // Spec: interrupt enable bit=0 disables interrupt for GPIO[i].
  task automatic t_int_disabled();
    apb_write(GPIOEN, 32'h0000_0001);
    apb_write(INTTYPE0, 32'h1);
    apb_write(INTEN, 32'h0);
    gpio_in[0] = 1'b1;
    count_irq(6, hi);
    apb_read(INTSTATUS, rd);
    check_eq(rd, 0, "INTEN=0: INTSTATUS stays 0");
    check(hi == 0, "INTEN=0: interrupt line stays low");
  endtask

  // Spec: "INTSTATUS is cleared when it is red."
  task automatic t_intstatus_clear_on_read();
    apb_write(GPIOEN, 32'h0000_0001);
    apb_write(INTTYPE0, 32'h1);
    apb_write(INTEN, 32'h1);
    gpio_in[0] = 1'b1;
    idle(6);
    apb_read(INTSTATUS, rd); check_eq(rd, 1, "INTSTATUS set by rising edge");
    apb_read(INTSTATUS, rd); check_eq(rd, 0, "INTSTATUS cleared by the previous read");
  endtask

  // Spec: "GPIO interrupt line is also cleared when INTSTATUS register is red."
  // Read literally, the line stays asserted from the edge until software reads
  // INTSTATUS. Checked here as a level held for the whole wait window.
  task automatic t_irq_held_until_read();
    apb_write(GPIOEN, 32'h0000_0001);
    apb_write(INTTYPE0, 32'h1);
    apb_write(INTEN, 32'h1);
    gpio_in[0] = 1'b1;
    idle(4);                                 // edge detected by now
    count_irq(8, hi);
    check(hi == 8, $sformatf("interrupt line held high until INTSTATUS read (high %0d of 8 cycles)", hi));
    apb_read(INTSTATUS, rd);
    idle(1);
    check(irq === 1'b0, "interrupt line low after INTSTATUS read");
    // Same on a pad other than 0. Added 1 Oct after the RTL agent's fix
    // `interrupt = r_status[0]` passed every test while being wrong for
    // pads 1..31: no interrupt test used any pad but 0.
    apb_write(GPIOEN, 32'h0000_0021);
    apb_write(INTTYPE0, 32'h0000_0401);       // pad 5: rising edge
    apb_write(INTEN, 32'h0000_0020);
    gpio_in[5] = 1'b1;
    idle(4);
    count_irq(8, hi);
    check(hi == 8, $sformatf("pad 5: interrupt line held high until INTSTATUS read (high %0d of 8 cycles)", hi));
    apb_read(INTSTATUS, rd);
    check_eq(rd, 32'h20, "pad 5: INTSTATUS[5] set");
    idle(1);
    check(irq === 1'b0, "pad 5: interrupt line low after INTSTATUS read");
  endtask

  // Spec: offsets beyond the map are not described. APB has no error for this
  // block (PSLVERR tied low); checked that such reads return 0 and complete.
  task automatic t_unmapped_and_hi_bank();
    apb_read(12'h07C, rd); check_eq(rd, 0, "unmapped offset 0x7C reads 0");
    apb_write(PADDIR_HI, 32'hFFFF_FFFF);
    apb_read(PADDIR_HI, rd); check_eq(rd, 0, "PADDIR_32_63 reads 0 with PAD_NUM=32");
    check_eq(gpio_dir, 0, "write to PADDIR_32_63 does not touch pads 0..31");
  endtask

  // @agent-tests — the request pipeline inserts generated test tasks above this line.

  // ---- dispatcher -----------------------------------------------------------
  initial begin
    if (!$value$plusargs("TEST=%s", test_name)) test_name = "reset_values";
    if ($test$plusargs("WAVES")) begin
      $dumpfile({test_name, ".vcd"});
      $dumpvars(0, tb_apb_gpio);
    end

    repeat (3) @(posedge clk);
    #1 rst_n = 1'b1;
    idle(2);

    case (test_name)
      "reset_values":            t_reset_values();
      "dir_rw":                  t_dir_rw();
      "out_rw":                  t_out_rw();
      "out_set_clr":             t_out_set_clr();
      "cfg_rw":                  t_cfg_rw();
      "padin_sync":              t_padin_sync();
      "padin_gating":            t_padin_gating();
      "int_rise":                t_int_rise();
      "int_fall":                t_int_fall();
      "int_both":                t_int_both();
      "int_disabled":            t_int_disabled();
      "inttype_rfu_level":       t_inttype_rfu_level();
      "intstatus_clear_on_read": t_intstatus_clear_on_read();
      "irq_held_until_read":     t_irq_held_until_read();
      "unmapped_and_hi_bank":    t_unmapped_and_hi_bank();
      // @agent-dispatch — generated test entries are inserted above this line.
      default: begin
        $display("RESULT %s FAIL unknown test", test_name);
        $finish;
      end
    endcase

    if (n_checks == 0)        $display("RESULT %s FAIL no checks ran", test_name);
    else if (first_fail == "") $display("RESULT %s PASS (%0d checks)", test_name, n_checks);
    else                       $display("RESULT %s FAIL %s", test_name, first_fail);
    $finish;
  end

  initial begin
    #200us;
    $display("RESULT %s FAIL timeout", test_name);
    $finish;
  end

endmodule
