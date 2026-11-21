`include "src/common/rtl/pkg/macros.vh"

module n2a_reset_sync #(
    parameter int unsigned STAGES = 2
) (
    input  logic clk_i,
    input  logic async_reset_i,
    output logic reset_o
);
    localparam int unsigned IMPLEMENTATION_STAGES = (STAGES < 2) ? 2 : STAGES;

    // Why: every stage stays visible so synthesis can identify the reset synchronizer.
    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED" *)
    logic [IMPLEMENTATION_STAGES-1:0] stages_q;
    logic [IMPLEMENTATION_STAGES-1:0] stages_d;

    `ASSERT_PARAM(g_invalid_stages, STAGES >= 2,
        "n2a_reset_sync STAGES must be at least 2")

    assign stages_d = {stages_q[IMPLEMENTATION_STAGES-2:0], 1'b0};

    // Asynchronous assertion fills every stage; release shifts zeros in.
    localparam logic [IMPLEMENTATION_STAGES-1:0] STAGES_RESET = {IMPLEMENTATION_STAGES{1'b1}};
    `DFF_ARST_VAL(stages_q, stages_d, clk_i, async_reset_i, STAGES_RESET)

    assign reset_o = stages_q[IMPLEMENTATION_STAGES-1];
endmodule
