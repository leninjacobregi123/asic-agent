// Testbench harness for apb_timer (designs/apb_timer/docs/apb_timer_spec.md).
//
// Same contract as designs/apb_gpio/tb/tb_apb_gpio.sv: one binary, the test selected with
// +TEST=<name>, exactly one line per test:
//
//     RESULT <name> PASS
//     RESULT <name> FAIL <first failing check>
//
// Timing used by the counting tests: apb_write's register update happens on
// the third clock edge of the task. Enabling (CTRL write), idling n cycles and
// disabling (CTRL write) therefore leaves the timer enabled for n + 3 edges.

`timescale 1ns/1ps

module tb_apb_timer;

  // Register offsets, from the spec register map.
  localparam logic [11:0] CTRL     = 12'h000;
  localparam logic [11:0] PRESCALE = 12'h004;
  localparam logic [11:0] COMPARE  = 12'h008;
  localparam logic [11:0] COUNT    = 12'h00C;
  localparam logic [11:0] STATUS   = 12'h010;

  logic        clk = 1'b0;
  logic        rst_n = 1'b0;
  logic [11:0] paddr = '0;
  logic [31:0] pwdata = '0;
  logic        pwrite = 1'b0, psel = 1'b0, penable = 1'b0;
  logic [31:0] prdata;
  logic        pready, pslverr;
  logic        irq;

  apb_timer dut (
    .HCLK(clk), .HRESETn(rst_n),
    .PADDR(paddr), .PWDATA(pwdata), .PWRITE(pwrite), .PSEL(psel),
    .PENABLE(penable), .PRDATA(prdata), .PREADY(pready), .PSLVERR(pslverr),
    .irq_o(irq)
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
    #1 data = prdata;
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

  // Spec: every register resets to 0x0; irq_o low.
  task automatic t_reset_values();
    apb_read(CTRL, rd);     check_eq(rd, 0, "CTRL reset value");
    apb_read(PRESCALE, rd); check_eq(rd, 0, "PRESCALE reset value");
    apb_read(COMPARE, rd);  check_eq(rd, 0, "COMPARE reset value");
    apb_read(COUNT, rd);    check_eq(rd, 0, "COUNT reset value");
    apb_read(STATUS, rd);   check_eq(rd, 0, "STATUS reset value");
    check(irq === 1'b0, "irq_o low after reset");
  endtask

  // Spec: CTRL bits[1:0], PRESCALE bits[15:0], COMPARE bits[31:0] are R/W;
  // other bits read 0.
  task automatic t_regs_rw();
    apb_write(CTRL, 32'hFFFF_FFFE);     apb_read(CTRL, rd);     check_eq(rd, 32'h2, "CTRL keeps only bits[1:0]");
    apb_write(CTRL, 32'h0);
    apb_write(PRESCALE, 32'hABCD_1234); apb_read(PRESCALE, rd); check_eq(rd, 32'h1234, "PRESCALE keeps only bits[15:0]");
    apb_write(COMPARE, 32'hDEAD_BEEF);  apb_read(COMPARE, rd);  check_eq(rd, 32'hDEAD_BEEF, "COMPARE is 32-bit R/W");
  endtask

  // Spec: COUNT is read-only; writes are ignored.
  task automatic t_count_readonly();
    apb_write(COUNT, 32'h1234_5678);
    apb_read(COUNT, rd); check_eq(rd, 0, "write to COUNT is ignored");
  endtask

  // Spec: with PRESCALE=0 the counter advances every cycle while EN=1.
  task automatic t_count_runs();
    apb_write(COMPARE, 32'hFFFF_FFFF);
    apb_write(CTRL, 32'h1); idle(20); apb_write(CTRL, 32'h0);
    apb_read(COUNT, rd); check_eq(rd, 23, "COUNT after 23 enabled cycles with PRESCALE=0");
  endtask

  // Spec: the counter advances once every PRESCALE+1 cycles.
  task automatic t_prescale_slows();
    apb_write(COMPARE, 32'hFFFF_FFFF);
    apb_write(PRESCALE, 32'd3);
    apb_write(CTRL, 32'h1); idle(20); apb_write(CTRL, 32'h0);
    apb_read(COUNT, rd); check_eq(rd, 5, "COUNT after 23 enabled cycles with PRESCALE=3 (one tick per 4)");
  endtask

  // Spec: while EN=0, COUNT holds; clearing EN does not reset COUNT.
  task automatic t_en_holds_count();
    apb_write(COMPARE, 32'hFFFF_FFFF);
    apb_write(CTRL, 32'h1); idle(20); apb_write(CTRL, 32'h0);
    idle(30);
    apb_read(COUNT, rd); check_eq(rd, 23, "COUNT holds while EN=0");
  endtask

  // Spec: on a tick with COUNT == COMPARE, COUNT returns to 0 and MATCH is set;
  // the timer repeats every COMPARE+1 ticks.
  task automatic t_match_wraps();
    apb_write(COMPARE, 32'd3);
    apb_write(CTRL, 32'h1); idle(20); apb_write(CTRL, 32'h0);
    apb_read(COUNT, rd);  check_eq(rd, 3, "COUNT wraps with period COMPARE+1 (23 ticks, period 4)");
    apb_read(STATUS, rd); check_eq(rd, 1, "STATUS.MATCH set after a compare match");
  endtask

  // Spec: irq_o is a level while MATCH and IRQ_EN are 1, until software
  // writes 1 to STATUS bit 0.
  task automatic t_irq_level_until_clear();
    apb_write(COMPARE, 32'd2);
    apb_write(CTRL, 32'h3); idle(10); apb_write(CTRL, 32'h2);
    count_irq(10, hi); check(hi == 10, $sformatf("irq_o held high until cleared (high %0d of 10 cycles)", hi));
    apb_write(STATUS, 32'h0);
    check(irq === 1'b1, "writing 0 to STATUS does not clear MATCH");
    apb_write(STATUS, 32'h1);
    check(irq === 1'b0, "writing 1 to STATUS bit 0 clears the interrupt");
    apb_read(STATUS, rd); check_eq(rd, 0, "STATUS.MATCH cleared by writing 1");
  endtask

  // Spec: with IRQ_EN=0, MATCH is set but irq_o stays low.
  task automatic t_irq_disabled();
    apb_write(COMPARE, 32'd2);
    apb_write(CTRL, 32'h1); idle(10); apb_write(CTRL, 32'h0);
    apb_read(STATUS, rd); check_eq(rd, 1, "MATCH set with IRQ_EN=0");
    count_irq(5, hi); check(hi == 0, "irq_o low while IRQ_EN=0");
  endtask

  // Spec: offsets not listed read as 0 and ignore writes.
  task automatic t_unmapped_reads_zero();
    apb_write(12'h014, 32'hFFFF_FFFF);
    apb_read(12'h014, rd); check_eq(rd, 0, "unmapped offset 0x14 reads 0");
    apb_read(12'h07C, rd); check_eq(rd, 0, "unmapped offset 0x7C reads 0");
  endtask

  // @agent-tests — the request pipeline inserts generated test tasks above this line.

  // ---- dispatcher -----------------------------------------------------------
  initial begin
    if (!$value$plusargs("TEST=%s", test_name)) test_name = "reset_values";
    if ($test$plusargs("WAVES")) begin
      $dumpfile({test_name, ".vcd"});
      $dumpvars(0, tb_apb_timer);
    end

    repeat (3) @(posedge clk);
    #1 rst_n = 1'b1;
    idle(2);

    case (test_name)
      "reset_values":          t_reset_values();
      "regs_rw":               t_regs_rw();
      "count_readonly":        t_count_readonly();
      "count_runs":            t_count_runs();
      "prescale_slows":        t_prescale_slows();
      "en_holds_count":        t_en_holds_count();
      "match_wraps":           t_match_wraps();
      "irq_level_until_clear": t_irq_level_until_clear();
      "irq_disabled":          t_irq_disabled();
      "unmapped_reads_zero":   t_unmapped_reads_zero();
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
