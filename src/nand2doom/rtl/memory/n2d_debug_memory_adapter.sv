`include "src/common/rtl/pkg/macros.vh"

module n2d_debug_memory_adapter #(
    parameter int unsigned MAX_WORDS = 8
) (
    input logic clk_i, input logic reset_i,
    input logic transaction_begin_i, input logic transaction_commit_i,
    input logic transaction_abort_i, output logic transaction_done_o,
    output logic transaction_error_o,
    input logic word_request_valid_i, output logic word_request_ready_o,
    input logic word_request_write_i, input logic [31:0] word_request_address_i,
    input logic [31:0] word_request_write_data_i,
    input logic [3:0] word_request_byte_enable_i,
    output logic word_response_valid_o, output logic [31:0] word_response_data_o,
    output logic word_response_error_o,
    output logic line_request_valid_o, input logic line_request_ready_i,
    output logic line_request_write_o, output logic [25:0] line_request_address_o,
    output logic [127:0] line_request_write_data_o,
    output logic [15:0] line_request_byte_enable_o,
    input logic line_response_valid_i, input logic [127:0] line_response_data_i,
    input logic line_response_error_i
);
    import n2d_ls_types_pkg::*;

    localparam int unsigned COUNT_WIDTH = (MAX_WORDS < 2) ? 1 : $clog2(MAX_WORDS + 1);
    localparam int unsigned INDEX_WIDTH = (MAX_WORDS < 2) ? 1 : $clog2(MAX_WORDS);
    localparam logic [COUNT_WIDTH-1:0] MAX_WORD_COUNT = COUNT_WIDTH'(MAX_WORDS);

    localparam t_debug_mem_regs REGS_RESET = '{
        state: DEBUG_MEM_IDLE, transaction: 1'b0, abort_pending: 1'b0, read_address: '0,
        word_response_valid: 1'b0, word_response_data: '0,
        word_response_error: 1'b0, transaction_done: 1'b0, transaction_error: 1'b0};
    t_debug_mem_regs regs_q, regs_d;
    // Why: staged-word count and index widths follow MAX_WORDS, so they are
    // registers of their own beside the packaged adapter state.
    logic [COUNT_WIDTH-1:0] count_q, count_d;
    logic [COUNT_WIDTH-1:0] committed_count_q, committed_count_d;
    logic [INDEX_WIDTH-1:0] index_q, index_d;
    logic [INDEX_WIDTH-1:0] rollback_index_q, rollback_index_d;
    // Why: staged words and the original lines they overwrite are reset-cleared
    // flop arrays with one indexed write per clock, so a rollback can restore
    // every overwritten line.
    logic [31:0] stage_address [0:MAX_WORDS-1];
    logic [31:0] stage_data [0:MAX_WORDS-1];
    logic [3:0] stage_enable [0:MAX_WORDS-1];
    logic [127:0] original_line [0:MAX_WORDS-1];
    logic stage_write, original_write;
    logic commit_active;

    assign word_request_ready_o = (regs_q.state == DEBUG_MEM_IDLE) &&
        (!word_request_write_i || (regs_q.transaction && count_q < MAX_WORD_COUNT));
    assign line_request_valid_o = (regs_q.state == DEBUG_MEM_WORD_READ_REQUEST) ||
        (regs_q.state == DEBUG_MEM_PREPARE_REQUEST) || (regs_q.state == DEBUG_MEM_PUBLISH_REQUEST) ||
        (regs_q.state == DEBUG_MEM_ROLLBACK_REQUEST);
    assign line_request_write_o = (regs_q.state == DEBUG_MEM_PUBLISH_REQUEST) ||
        (regs_q.state == DEBUG_MEM_ROLLBACK_REQUEST);
    assign commit_active = (regs_q.state == DEBUG_MEM_PREPARE_REQUEST) || (regs_q.state == DEBUG_MEM_PREPARE_RESPONSE) ||
        (regs_q.state == DEBUG_MEM_PUBLISH_REQUEST) || (regs_q.state == DEBUG_MEM_PUBLISH_RESPONSE) ||
        (regs_q.state == DEBUG_MEM_ROLLBACK_REQUEST) || (regs_q.state == DEBUG_MEM_ROLLBACK_RESPONSE);

    always_comb begin
        line_request_address_o = regs_q.read_address & 26'h3ff_fff0;
        line_request_write_data_o = '0;
        line_request_byte_enable_o = '0;
        if ((regs_q.state == DEBUG_MEM_PREPARE_REQUEST) || (regs_q.state == DEBUG_MEM_PUBLISH_REQUEST))
            line_request_address_o = stage_address[index_q][25:0] & 26'h3ff_fff0;
        else if (regs_q.state == DEBUG_MEM_ROLLBACK_REQUEST)
            line_request_address_o = stage_address[rollback_index_q][25:0] & 26'h3ff_fff0;
        if (regs_q.state == DEBUG_MEM_PUBLISH_REQUEST) begin
            line_request_write_data_o = 128'(stage_data[index_q]) <<
                (stage_address[index_q][3:2] * 32);
            line_request_byte_enable_o = 16'(stage_enable[index_q]) <<
                (stage_address[index_q][3:2] * 4);
        end else if (regs_q.state == DEBUG_MEM_ROLLBACK_REQUEST) begin
            line_request_write_data_o = original_line[rollback_index_q];
            line_request_byte_enable_o = 16'hffff;
        end
    end

    assign word_response_valid_o = regs_q.word_response_valid;
    assign word_response_data_o = regs_q.word_response_data;
    assign word_response_error_o = regs_q.word_response_error;
    assign transaction_done_o = regs_q.transaction_done;
    assign transaction_error_o = regs_q.transaction_error;

    always_comb begin
        regs_d = regs_q;
        rollback_index_d = rollback_index_q;
        index_d = index_q;
        committed_count_d = committed_count_q;
        count_d = count_q;
        stage_write = 1'b0;
        original_write = 1'b0;
        regs_d.word_response_valid = 1'b0;
        regs_d.word_response_error = 1'b0;
        regs_d.transaction_done = 1'b0;
        regs_d.transaction_error = 1'b0;
        if (transaction_begin_i) begin
            regs_d.transaction = 1'b1;
            regs_d.abort_pending = 1'b0;
            count_d = '0;
            committed_count_d = '0;
        end
        if (transaction_abort_i) begin
            regs_d.transaction = 1'b0;
            if (commit_active) regs_d.abort_pending = 1'b1;
        end
        if (transaction_abort_i && !commit_active) begin
            count_d = '0;
            regs_d.transaction_done = 1'b1;
            regs_d.state = DEBUG_MEM_IDLE;
        end else if (transaction_commit_i) begin
            regs_d.transaction = 1'b0;
            regs_d.abort_pending = 1'b0;
            committed_count_d = '0;
            index_d = '0;
            if (count_q == 0) begin
                regs_d.transaction_done = 1'b1;
                regs_d.state = DEBUG_MEM_IDLE;
            end else regs_d.state = DEBUG_MEM_PREPARE_REQUEST;
        end else begin
            case (regs_q.state)
                DEBUG_MEM_IDLE: if (word_request_valid_i && word_request_ready_o) begin
                    if (word_request_address_i[31:26] != 0 || word_request_address_i[1:0] != 0) begin
                        regs_d.word_response_error = 1'b1;
                        regs_d.word_response_valid = 1'b1;
                    end else if (word_request_write_i) begin
                        stage_write = 1'b1;
                        count_d = count_q + 1'b1;
                        regs_d.state = DEBUG_MEM_WORD_RESPONSE;
                    end else begin
                        regs_d.read_address = word_request_address_i[25:0];
                        regs_d.state = DEBUG_MEM_WORD_READ_REQUEST;
                    end
                end
                DEBUG_MEM_WORD_RESPONSE: begin
                    regs_d.word_response_valid = 1'b1;
                    regs_d.state = DEBUG_MEM_IDLE;
                end
                DEBUG_MEM_WORD_READ_REQUEST: if (line_request_ready_i) regs_d.state = DEBUG_MEM_WORD_READ_RESPONSE;
                DEBUG_MEM_WORD_READ_RESPONSE: if (line_response_valid_i) begin
                    case (regs_q.read_address[3:2])
                        2'd0: regs_d.word_response_data = line_response_data_i[31:0];
                        2'd1: regs_d.word_response_data = line_response_data_i[63:32];
                        2'd2: regs_d.word_response_data = line_response_data_i[95:64];
                        default: regs_d.word_response_data = line_response_data_i[127:96];
                    endcase
                    regs_d.word_response_error = line_response_error_i;
                    regs_d.word_response_valid = 1'b1;
                    regs_d.state = DEBUG_MEM_IDLE;
                end
                DEBUG_MEM_PREPARE_REQUEST: if (line_request_ready_i) regs_d.state = DEBUG_MEM_PREPARE_RESPONSE;
                DEBUG_MEM_PREPARE_RESPONSE: if (line_response_valid_i) begin
                    if (line_response_error_i || regs_q.abort_pending || transaction_abort_i) begin
                        if (committed_count_q == 0) begin
                            count_d = '0;
                            regs_d.abort_pending = 1'b0;
                            regs_d.transaction_done = 1'b1;
                            regs_d.transaction_error = line_response_error_i;
                            regs_d.state = DEBUG_MEM_IDLE;
                        end else begin
                            rollback_index_d = committed_count_q[INDEX_WIDTH-1:0] - 1'b1;
                            regs_d.state = DEBUG_MEM_ROLLBACK_REQUEST;
                        end
                    end else begin
                        original_write = 1'b1;
                        regs_d.state = DEBUG_MEM_PUBLISH_REQUEST;
                    end
                end
                DEBUG_MEM_PUBLISH_REQUEST: if (line_request_ready_i) regs_d.state = DEBUG_MEM_PUBLISH_RESPONSE;
                DEBUG_MEM_PUBLISH_RESPONSE: if (line_response_valid_i) begin
                    if (line_response_error_i) begin
                        if (committed_count_q == 0) begin
                            count_d = '0;
                            regs_d.transaction_done = 1'b1;
                            regs_d.transaction_error = 1'b1;
                            regs_d.state = DEBUG_MEM_IDLE;
                        end else begin
                            rollback_index_d = committed_count_q[INDEX_WIDTH-1:0] - 1'b1;
                            regs_d.state = DEBUG_MEM_ROLLBACK_REQUEST;
                        end
                    end else if (regs_q.abort_pending || transaction_abort_i) begin
                        rollback_index_d = index_q;
                        committed_count_d = committed_count_q + 1'b1;
                        regs_d.state = DEBUG_MEM_ROLLBACK_REQUEST;
                    end else if (index_q + 1'b1 == count_q) begin
                        count_d = '0;
                        committed_count_d = '0;
                        regs_d.transaction_done = 1'b1;
                        regs_d.state = DEBUG_MEM_IDLE;
                    end else begin
                        committed_count_d = committed_count_q + 1'b1;
                        index_d = index_q + 1'b1;
                        regs_d.state = DEBUG_MEM_PREPARE_REQUEST;
                    end
                end
                DEBUG_MEM_ROLLBACK_REQUEST: if (line_request_ready_i) regs_d.state = DEBUG_MEM_ROLLBACK_RESPONSE;
                DEBUG_MEM_ROLLBACK_RESPONSE: if (line_response_valid_i) begin
                    if (line_response_error_i) begin
                        regs_d.state = DEBUG_MEM_ROLLBACK_REQUEST;
                    end else if (rollback_index_q == 0) begin
                        count_d = '0;
                        committed_count_d = '0;
                        regs_d.abort_pending = 1'b0;
                        regs_d.transaction_done = 1'b1;
                        regs_d.transaction_error = 1'b1;
                        regs_d.state = DEBUG_MEM_IDLE;
                    end else begin
                        rollback_index_d = rollback_index_q - 1'b1;
                        regs_d.state = DEBUG_MEM_ROLLBACK_REQUEST;
                    end
                end
                default: regs_d.state = DEBUG_MEM_IDLE;
            endcase
        end
    end

    `DFF_RST_VAL(regs_q, regs_d, clk_i, reset_i, REGS_RESET)
    `DFF_RST(count_q, count_d, clk_i, reset_i)
    `DFF_RST(committed_count_q, committed_count_d, clk_i, reset_i)
    `DFF_RST(index_q, index_d, clk_i, reset_i)
    `DFF_RST(rollback_index_q, rollback_index_d, clk_i, reset_i)
    logic [INDEX_WIDTH-1:0] stage_write_index;
    assign stage_write_index = count_q[INDEX_WIDTH-1:0];
    `MEM_WRITE_RST(stage_address, clk_i, stage_write, stage_write_index, word_request_address_i, reset_i)
    `MEM_WRITE_RST(stage_data, clk_i, stage_write, stage_write_index, word_request_write_data_i, reset_i)
    `MEM_WRITE_RST(stage_enable, clk_i, stage_write, stage_write_index, word_request_byte_enable_i, reset_i)
    logic [INDEX_WIDTH-1:0] original_line_index;
    assign original_line_index = index_q;
    `MEM_WRITE_RST(original_line, clk_i, original_write, original_line_index, line_response_data_i, reset_i)

    `ASSERT_INITIAL(MAX_WORDS > 0)
    `ASSERT_CLOCKED_WHEN(clk_i, reset_i, word_request_valid_i && word_request_write_i,
        regs_q.transaction || transaction_begin_i)
    `ASSERT_CLOCKED(clk_i, reset_i, !(transaction_commit_i && transaction_abort_i))
endmodule
