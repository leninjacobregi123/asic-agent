// apb_timer — 32-bit periodic timer with prescaler and compare-match
// interrupt. Specification: designs/apb_timer/docs/apb_timer_spec.md.

module apb_timer (
    input  logic        HCLK,
    input  logic        HRESETn,
    input  logic [11:0] PADDR,
    input  logic [31:0] PWDATA,
    input  logic        PWRITE,
    input  logic        PSEL,
    input  logic        PENABLE,
    output logic [31:0] PRDATA,
    output logic        PREADY,
    output logic        PSLVERR,
    output logic        irq_o
);

    localparam logic [11:0] REG_CTRL     = 12'h000;
    localparam logic [11:0] REG_PRESCALE = 12'h004;
    localparam logic [11:0] REG_COMPARE  = 12'h008;
    localparam logic [11:0] REG_COUNT    = 12'h00C;
    localparam logic [11:0] REG_STATUS   = 12'h010;

    logic        r_en, r_irq_en, r_match;
    logic [15:0] r_prescale, r_pcnt;
    logic [31:0] r_compare, r_count;

    logic s_write, s_tick, s_hit;

    assign s_write = PSEL & PENABLE & PWRITE;
    assign s_tick  = r_en & (r_pcnt == r_prescale);
    assign s_hit   = s_tick & (r_count == r_compare);

    // Registers written over APB
    always_ff @(posedge HCLK or negedge HRESETn) begin
        if (!HRESETn) begin
            r_en       <= 1'b0;
            r_irq_en   <= 1'b0;
            r_prescale <= '0;
            r_compare  <= '0;
        end else if (s_write) begin
            case (PADDR)
                REG_CTRL:     begin r_en <= PWDATA[0]; r_irq_en <= PWDATA[1]; end
                REG_PRESCALE: r_prescale <= PWDATA[15:0];
                REG_COMPARE:  r_compare  <= PWDATA;
                default: ;
            endcase
        end
    end

    // Prescaler and counter
    always_ff @(posedge HCLK or negedge HRESETn) begin
        if (!HRESETn) begin
            r_pcnt  <= '0;
            r_count <= '0;
        end else if (r_en) begin
            r_pcnt <= s_tick ? '0 : r_pcnt + 16'd1;
            if (s_tick) r_count <= s_hit ? '0 : r_count + 32'd1;
        end
    end

    // STATUS.MATCH: set on a compare hit, cleared by writing 1; a hit wins.
    always_ff @(posedge HCLK or negedge HRESETn) begin
        if (!HRESETn)
            r_match <= 1'b0;
        else if (s_hit)
            r_match <= 1'b1;
        else if (s_write && PADDR == REG_STATUS && PWDATA[0])
            r_match <= 1'b0;
    end

    always_comb begin
        case (PADDR)
            REG_CTRL:     PRDATA = {30'd0, r_irq_en, r_en};
            REG_PRESCALE: PRDATA = {16'd0, r_prescale};
            REG_COMPARE:  PRDATA = r_compare;
            REG_COUNT:    PRDATA = r_count;
            REG_STATUS:   PRDATA = {31'd0, r_match};
            default:      PRDATA = '0;
        endcase
    end

    assign PREADY  = 1'b1;
    assign PSLVERR = 1'b0;
    assign irq_o   = r_match & r_irq_en;

endmodule
