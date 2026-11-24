`include "src/common/rtl/pkg/macros.vh"

module n2a_cdc_bit_sync #(
    parameter int unsigned STAGES = 2,
    parameter logic RESET_VALUE = 1'b0
) (
    input  logic clk_i,
    input  logic reset_i,
    input  logic async_i,
    output logic sync_o
);
    localparam int unsigned IMPLEMENTATION_STAGES = (STAGES < 2) ? 2 : STAGES;

    // Why: a flop chain is safe only for a stable asynchronous scalar level.
    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED" *)
    logic [IMPLEMENTATION_STAGES-1:0] stages_q;
    logic [IMPLEMENTATION_STAGES-1:0] stages_d;

    `ASSERT_PARAM(g_invalid_stages, STAGES >= 2,
        "n2a_cdc_bit_sync STAGES must be at least 2")

    assign stages_d = {stages_q[IMPLEMENTATION_STAGES-2:0], async_i};

    // Why: reset loads every stage with RESET_VALUE, the input's resting level,
    // so leaving reset shows no false edge.
    localparam logic [IMPLEMENTATION_STAGES-1:0] STAGES_RESET = {IMPLEMENTATION_STAGES{RESET_VALUE}};
    `DFF_RST_VAL(stages_q, stages_d, clk_i, reset_i, STAGES_RESET)

    assign sync_o = stages_q[IMPLEMENTATION_STAGES-1];
endmodule
