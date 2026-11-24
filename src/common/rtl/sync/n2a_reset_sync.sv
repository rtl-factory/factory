`include "src/common/rtl/pkg/macros.vh"

module n2a_reset_sync #(
    parameter int unsigned STAGES = 2
) (
    input  logic clk_i,
    input  logic async_reset_i,
    output logic reset_o
);
    localparam int unsigned IMPLEMENTATION_STAGES = (STAGES < 2) ? 2 : STAGES;

    // Why: every stage stays visible so synthesis can identify the reset
    // synchronizer. The last stage is its own flop because it alone drives
    // reset_o: lint then sees the earlier stages only as data, and still flags
    // a user that takes reset_o as both a synchronous and an asynchronous reset.
    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED" *)
    logic [IMPLEMENTATION_STAGES-2:0] stages_q;
    logic [IMPLEMENTATION_STAGES-2:0] stages_d;
    (* altera_attribute = "-name SYNCHRONIZER_IDENTIFICATION FORCED" *)
    logic reset_q;

    `ASSERT_PARAM(g_invalid_stages, STAGES >= 2,
        "n2a_reset_sync STAGES must be at least 2")

    assign stages_d = stages_q << 1;

    // Why: this is the reset synchronizer: assertion must reach every stage
    // without a clock edge, and release shifts zeros in on clk_i.
    `DFF_ARST_VAL(stages_q, stages_d, clk_i, async_reset_i, '1)
    `DFF_ARST_VAL(reset_q, stages_q[IMPLEMENTATION_STAGES-2], clk_i, async_reset_i, 1'b1)

    assign reset_o = reset_q;
endmodule
