// Machine-mode CSR file and trap state
//----------------------------------------------------------
// 1. Decode and read the bounded machine CSR set for the serialized Q102H
//    CSR instruction and compute its read/modify/write value.
// 2. Apply the CSR write, trap entry or MRET on the Q102H commit edge, or a
//    data-access trap entry on the Q103H response edge. Serialization makes
//    those sources mutually exclusive.
// 3. Advance mcycle every clock and minstret per ordinary retirement unless
//    mcountinhibit stops them; a write to either counter half replaces that
//    edge's increment.
// 4. Own the performance monitor n2d_pmon: CSR addresses this module does
//    not decode go to it, its writes commit on the same Q102H edge, and it
//    counts the core events from n2d_ctrl ORed with the platform events.
//----------------------------------------------------------

`include "src/common/rtl/pkg/macros.vh"

module n2d_csr (
    input  logic        clk_i,
    input  logic        reset_i,

    // Serialized Q102H CSR access
    input  logic [11:0] csr_address_i,
    input  logic [1:0]  csr_op_i,          // funct3[1:0]: RW, RS or RC
    input  logic        csr_write_intent_i,
    input  logic [31:0] csr_operand_i,
    output logic [31:0] csr_read_data_o,
    output logic        csr_legal_o,
    input  logic        csr_commit_i,

    // Trap entry (Q102H commit or Q103H data error) and MRET
    input  logic        trap_valid_i,
    input  logic [31:0] trap_epc_i,
    input  logic [31:0] trap_cause_i,
    input  logic [31:0] trap_value_i,
    input  logic        mret_commit_i,

    // One ordinary retirement: entering Q105H, or a CSR/MRET Q102H commit
    input  logic        instret_increment_i,

    // Performance monitor event vectors, one bit per event ID
    input  logic [127:0] pmon_events_i,
    output logic [127:0] pmon_events_o,

    output logic [31:0] mtvec_o,
    output logic [31:0] mepc_o
);
    import n2d_core_types_pkg::*;

    localparam t_csr_regs CSR_RESET = '{
        mstatus: 32'h0000_1800,
        mtvec: 32'b0,
        mscratch: 32'b0,
        mepc: 32'b0,
        mcause: 32'b0,
        mtval: 32'b0,
        mcycle: 64'b0,
        minstret: 64'b0};

    t_csr_regs csr_q, csr_d;
    logic        csr_exists;
    logic [31:0] csr_write_data;
    logic        csr_write;
    logic        pmon_csr_hit;
    logic [31:0] pmon_csr_read_data;
    logic        inhibit_cycle;
    logic        inhibit_instret;

    n2d_pmon u_pmon (
        .clk_i(clk_i),
        .reset_i(reset_i),
        .events_i(pmon_events_i),
        .csr_address_i(csr_address_i),
        .csr_hit_o(pmon_csr_hit),
        .csr_read_data_o(pmon_csr_read_data),
        .csr_write_i(csr_write),
        .csr_write_data_i(csr_write_data),
        .inhibit_cycle_o(inhibit_cycle),
        .inhibit_instret_o(inhibit_instret),
        .events_o(pmon_events_o)
    );

    always_comb begin
        csr_exists = 1'b1;
        case (csr_address_i)
            12'h300: csr_read_data_o = csr_q.mstatus | 32'h0000_1800;
            12'h301: csr_read_data_o = 32'h4000_1100;
            12'h305: csr_read_data_o = csr_q.mtvec;
            12'h340: csr_read_data_o = csr_q.mscratch;
            12'h341: csr_read_data_o = csr_q.mepc;
            12'h342: csr_read_data_o = csr_q.mcause;
            12'h343: csr_read_data_o = csr_q.mtval;
            12'hB00: csr_read_data_o = csr_q.mcycle[31:0];
            12'hB02: csr_read_data_o = csr_q.minstret[31:0];
            12'hB80: csr_read_data_o = csr_q.mcycle[63:32];
            12'hB82: csr_read_data_o = csr_q.minstret[63:32];
            default: begin
                csr_exists = pmon_csr_hit;
                csr_read_data_o = pmon_csr_read_data;
            end
        endcase
        csr_legal_o = csr_exists && !((csr_address_i == 12'h301) && csr_write_intent_i);

        case (csr_op_i)
            2'b01: csr_write_data = csr_operand_i;
            2'b10: csr_write_data = csr_read_data_o | csr_operand_i;
            2'b11: csr_write_data = csr_read_data_o & ~csr_operand_i;
            default: csr_write_data = 32'b0;
        endcase
    end

    always_comb begin
        csr_write = csr_commit_i && csr_write_intent_i && csr_legal_o;

        csr_d = csr_q;
        if (!inhibit_cycle) csr_d.mcycle = csr_q.mcycle + 64'd1;
        if (instret_increment_i && !inhibit_instret) csr_d.minstret = csr_q.minstret + 64'd1;

        if (csr_write) begin
            case (csr_address_i)
                12'h300: csr_d.mstatus = (csr_write_data & 32'h0000_0088) | 32'h0000_1800;
                12'h305: csr_d.mtvec = csr_write_data & 32'hFFFF_FFFC;
                12'h340: csr_d.mscratch = csr_write_data;
                12'h341: csr_d.mepc = csr_write_data & 32'hFFFF_FFFC;
                12'h342: csr_d.mcause = csr_write_data;
                12'h343: csr_d.mtval = csr_write_data;
                12'hB00: csr_d.mcycle = {csr_q.mcycle[63:32], csr_write_data};
                12'hB80: csr_d.mcycle = {csr_write_data, csr_q.mcycle[31:0]};
                12'hB02: csr_d.minstret = {csr_q.minstret[63:32], csr_write_data};
                12'hB82: csr_d.minstret = {csr_write_data, csr_q.minstret[31:0]};
                default: begin
                end
            endcase
        end

        // Ref: RISC-V Privileged ISA, mstatus on trap entry: MPP records M, MPIE
        // takes MIE and MIE clears.
        if (trap_valid_i) begin
            csr_d.mepc = trap_epc_i;
            csr_d.mcause = trap_cause_i;
            csr_d.mtval = trap_value_i;
            csr_d.mstatus[12:11] = 2'b11;
            csr_d.mstatus[7] = csr_q.mstatus[3];
            csr_d.mstatus[3] = 1'b0;
        end else if (mret_commit_i) begin
            csr_d.mstatus[12:11] = 2'b11;
            csr_d.mstatus[3] = csr_q.mstatus[7];
            csr_d.mstatus[7] = 1'b1;
        end
    end

    assign mtvec_o = csr_q.mtvec;
    assign mepc_o = csr_q.mepc;

    `DFF_RST_VAL(csr_q, csr_d, clk_i, reset_i, CSR_RESET)

    `ASSERT_NEVER(csr_single_source, clk_i, reset_i,
        (trap_valid_i && (csr_commit_i || mret_commit_i)) || (csr_commit_i && mret_commit_i))
endmodule
