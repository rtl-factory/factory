`include "src/common/rtl/pkg/macros.vh"

module n2d_debug_device_line_bridge (
    input  logic         clk_i,
    input  logic         reset_i,
    input  logic         line_request_valid_i,
    output logic         line_request_ready_o,
    input  logic         line_request_write_i,
    input  logic [25:0]  line_request_address_i,
    input  logic [127:0] line_request_write_data_i,
    input  logic [15:0]  line_request_byte_enable_i,
    output logic         line_response_valid_o,
    output logic [127:0] line_response_data_o,
    output logic         line_response_error_o,

    output logic         framebuffer_request_valid_o,
    input  logic         framebuffer_request_ready_i,
    output logic         framebuffer_request_write_o,
    output logic [15:0]  framebuffer_request_address_o,
    output logic [31:0]  framebuffer_request_write_data_o,
    output logic [3:0]   framebuffer_request_write_strobe_o,
    input  logic         framebuffer_response_valid_i,
    input  logic [31:0]  framebuffer_response_read_data_i,

    output logic         palette_request_valid_o,
    input  logic         palette_request_ready_i,
    output logic         palette_request_write_o,
    output logic [7:0]   palette_request_index_o,
    output logic [31:0]  palette_request_write_data_o,
    output logic [3:0]   palette_request_write_strobe_o,
    input  logic         palette_response_valid_i,
    input  logic [31:0]  palette_response_read_data_i
);
    import n2d_types_pkg::*;

    localparam t_line_bridge_regs REGS_RESET = '{
        state: LINE_BRIDGE_IDLE, write: 1'b0, palette: 1'b0, address: '0, write_data: '0,
        byte_enable: '0, word: '0, response_data: '0, line_response_valid: 1'b0};
    /* verilator lint_off UNUSEDSIGNAL */
    t_line_bridge_regs regs_q;
    /* verilator lint_on UNUSEDSIGNAL */
    t_line_bridge_regs regs_d;
    logic selected_ready, selected_response_valid;
    logic [31:0] selected_response_data;
    logic word_enabled;

    assign line_request_ready_o = regs_q.state == LINE_BRIDGE_IDLE;
    assign line_response_data_o = regs_q.response_data;
    assign line_response_error_o = 1'b0;
    assign word_enabled = |regs_q.byte_enable[regs_q.word * 4 +: 4];
    assign framebuffer_request_valid_o = (regs_q.state == LINE_BRIDGE_ISSUE) && !regs_q.palette &&
        (!regs_q.write || word_enabled);
    assign palette_request_valid_o = (regs_q.state == LINE_BRIDGE_ISSUE) && regs_q.palette &&
        (!regs_q.write || word_enabled);
    assign framebuffer_request_write_o = regs_q.write;
    assign palette_request_write_o = regs_q.write;
    assign framebuffer_request_address_o =
        {regs_q.address[15:4], 4'b0} + {12'b0, regs_q.word, 2'b00};
    assign palette_request_index_o = regs_q.address[9:2] + {6'b0, regs_q.word};
    assign framebuffer_request_write_data_o = regs_q.write_data[regs_q.word * 32 +: 32];
    assign palette_request_write_data_o = regs_q.write_data[regs_q.word * 32 +: 32];
    assign framebuffer_request_write_strobe_o = regs_q.byte_enable[regs_q.word * 4 +: 4];
    assign palette_request_write_strobe_o = regs_q.byte_enable[regs_q.word * 4 +: 4];
    assign selected_ready = regs_q.palette ? palette_request_ready_i :
        framebuffer_request_ready_i;
    assign selected_response_valid = regs_q.palette ? palette_response_valid_i :
        framebuffer_response_valid_i;
    assign selected_response_data = regs_q.palette ? palette_response_read_data_i :
        framebuffer_response_read_data_i;

    assign line_response_valid_o = regs_q.line_response_valid;

    always_comb begin
        regs_d = regs_q;
        regs_d.line_response_valid = 1'b0;
        case (regs_q.state)
            LINE_BRIDGE_IDLE: if (line_request_valid_i) begin
                regs_d.write = line_request_write_i;
                regs_d.palette = line_request_address_i[16];
                regs_d.address = line_request_address_i;
                regs_d.write_data = line_request_write_data_i;
                regs_d.byte_enable = line_request_byte_enable_i;
                regs_d.word = '0;
                regs_d.response_data = '0;
                regs_d.state = LINE_BRIDGE_ISSUE;
            end
            LINE_BRIDGE_ISSUE: begin
                if (regs_q.write && !word_enabled) begin
                    if (regs_q.word == 2'd3) begin
                        regs_d.line_response_valid = 1'b1;
                        regs_d.state = LINE_BRIDGE_IDLE;
                    end else regs_d.word = regs_q.word + 1'b1;
                end else if (selected_ready) begin
                    regs_d.state = LINE_BRIDGE_RESPONSE;
                end
            end
            LINE_BRIDGE_RESPONSE: if (selected_response_valid) begin
                if (!regs_q.write) begin
                    regs_d.response_data[regs_q.word * 32 +: 32] = selected_response_data;
                end
                if (regs_q.word == 2'd3) begin
                    regs_d.line_response_valid = 1'b1;
                    regs_d.state = LINE_BRIDGE_IDLE;
                end else begin
                    regs_d.word = regs_q.word + 1'b1;
                    regs_d.state = LINE_BRIDGE_ISSUE;
                end
            end
            default: regs_d.state = LINE_BRIDGE_IDLE;
        endcase
    end

    `DFF_RST_VAL(regs_q, regs_d, clk_i, reset_i, REGS_RESET)

    `ASSERT_CLOCKED(clk_i, reset_i, !(framebuffer_request_valid_o && palette_request_valid_o))
    `ASSERT_CLOCKED_WHEN(clk_i, reset_i, line_request_valid_i, line_request_address_i[3:0] == 0)
endmodule
