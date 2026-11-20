`include "src/common/rtl/pkg/macros.vh"

// Machine-mode hardware performance monitor: four programmable 64-bit
// counters (mhpmcounter3..6 and their high halves), their event selectors
// (mhpmevent3..6) and mcountinhibit. The CSR layout and the event ID scheme
// follow the nand2linux PMON; the event table is owned by
// wiki/src/nand2doom/rtl/core/spec.md ("Performance monitor").
//
// events_i carries one combinational occurrence bit per event ID. Each
// counter registers its selected, non-inhibited event in a pending bit and
// adds that bit on the following edge, so the adder starts from a flop and
// software sees an increment one edge later than nand2linux. Otherwise the
// semantics are nand2linux's: an event is selected and inhibited by the
// controls current in the cycle it occurs. events_o is the registered vector
// of all legal events, for direct observation by simulation harnesses.
//
// csr_address_i is the address of the CSR instruction being executed. The
// read is combinational from current state. csr_write_i commits on the
// writer's retirement edge: a counter-half write replaces that half of the
// up-to-date value, preserves the other half and discards that counter's
// event from the writer's cycle; inhibit and selector writes apply from the
// next cycle. Counter values therefore equal nand2linux's, one edge later.
module n2d_pmon (
    input  logic         clk_i,
    input  logic         rst_i,
    input  logic [127:0] events_i,
    input  logic [11:0]  csr_address_i,
    output logic         csr_hit_o,
    output logic [31:0]  csr_read_data_o,
    input  logic         csr_write_i,
    input  logic [31:0]  csr_write_data_i,
    output logic         inhibit_cycle_o,
    output logic         inhibit_instret_o,
    output logic [127:0] events_o
);
    import n2d_core_types_pkg::*;

    // CY, IR and HPM3..6; bit 1 (TM) is not implemented.
    localparam logic [31:0] MCOUNTINHIBIT_MASK = 32'h0000_007d;

    // Selectable event IDs. Bit i set means mhpmevent accepts ID i; any
    // other written value reads back as zero (NONE).
    localparam logic [127:0] EVENT_LEGAL =
        (128'd1 << 1)  | (128'd1 << 2)  | (128'd1 << 3)  | (128'd1 << 4)  |
        (128'd1 << 5)  | (128'd1 << 6)  | (128'd1 << 8)  | (128'd1 << 9)  |
        (128'd1 << 10) | (128'd1 << 12) | (128'd1 << 13) | (128'd1 << 14) |
        (128'd1 << 15) | (128'd1 << 16) | (128'd1 << 17) | (128'd1 << 18) |
        (128'd1 << 19) | (128'd1 << 29) | (128'd1 << 30) | (128'd1 << 32) |
        (128'd1 << 33) | (128'd1 << 34) | (128'd1 << 64) | (128'd1 << 65) |
        (128'd1 << 66) | (128'd1 << 67) | (128'd1 << 68) | (128'd1 << 69) |
        (128'd1 << 76) | (128'd1 << 77) | (128'd1 << 78) | (128'd1 << 79) |
        (128'd1 << 80) | (128'd1 << 81) | (128'd1 << 82) | (128'd1 << 83) |
        (128'd1 << 84) | (128'd1 << 85) | (128'd1 << 86) | (128'd1 << 87) |
        (128'd1 << 88) | (128'd1 << 89) | (128'd1 << 90) | (128'd1 << 91) |
        (128'd1 << 92) | (128'd1 << 93) | (128'd1 << 94) | (128'd1 << 95) |
        (128'd1 << 96) | (128'd1 << 97);

    pmon_regs_t regs_q, regs_d;

    always_comb begin
        csr_hit_o = 1'b0;
        csr_read_data_o = 32'b0;
        if (csr_address_i == 12'h320) begin
            csr_hit_o = 1'b1;
            csr_read_data_o = regs_q.inhibit;
        end
        for (int i = 0; i < 4; i++) begin
            if (csr_address_i == (12'hB03 + 12'(i))) begin
                csr_hit_o = 1'b1;
                csr_read_data_o = regs_q.count[i][31:0];
            end
            if (csr_address_i == (12'hB83 + 12'(i))) begin
                csr_hit_o = 1'b1;
                csr_read_data_o = regs_q.count[i][63:32];
            end
            if (csr_address_i == (12'h323 + 12'(i))) begin
                csr_hit_o = 1'b1;
                csr_read_data_o = {25'b0, regs_q.select[i]};
            end
        end
    end

    always_comb begin
        regs_d = regs_q;
        regs_d.events = events_i & EVENT_LEGAL;
        for (int i = 0; i < 4; i++) begin
            regs_d.count[i] = regs_q.count[i] + {63'b0, regs_q.pending[i]};
            regs_d.pending[i] = !regs_q.inhibit[i + 3] &&
                (events_i[regs_q.select[i]] && EVENT_LEGAL[regs_q.select[i]]);
        end
        if (csr_write_i) begin
            if (csr_address_i == 12'h320)
                regs_d.inhibit = csr_write_data_i & MCOUNTINHIBIT_MASK;
            for (int i = 0; i < 4; i++) begin
                if (csr_address_i == (12'hB03 + 12'(i))) begin
                    regs_d.count[i] = {regs_d.count[i][63:32], csr_write_data_i};
                    regs_d.pending[i] = 1'b0;
                end
                if (csr_address_i == (12'hB83 + 12'(i))) begin
                    regs_d.count[i] = {csr_write_data_i, regs_d.count[i][31:0]};
                    regs_d.pending[i] = 1'b0;
                end
                if (csr_address_i == (12'h323 + 12'(i)))
                    regs_d.select[i] = ((csr_write_data_i[31:7] == 25'b0) &&
                        EVENT_LEGAL[csr_write_data_i[6:0]]) ? csr_write_data_i[6:0] : 7'd0;
            end
        end
    end

    `N2A_DFF_RST(regs_q, regs_d, clk_i, rst_i)

    assign inhibit_cycle_o = regs_q.inhibit[0];
    assign inhibit_instret_o = regs_q.inhibit[2];
    assign events_o = regs_q.events;

    `N2A_ASSERT_CLOCKED(clk_i, rst_i, (regs_q.inhibit & ~MCOUNTINHIBIT_MASK) == 32'b0)
endmodule
