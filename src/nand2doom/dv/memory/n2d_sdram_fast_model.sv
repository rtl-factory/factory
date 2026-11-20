module n2d_sdram_fast_model #(
    parameter int unsigned ADDRESS_WIDTH = 26,
    parameter int unsigned LATENCY_CYCLES = 2
) (
    input  logic         clk_i,
    input  logic         reset_i,
    input  logic         request_valid_i,
    output logic         request_ready_o,
    input  logic         request_write_i,
    input  logic [25:0]  request_address_i,
    input  logic [127:0] request_write_data_i,
    input  logic [15:0]  request_byte_enable_i,
    input  logic         preload_valid_i,
    input  logic [25:0]  preload_address_i,
    input  logic [2047:0] preload_data_i,
    input  logic [255:0] preload_byte_enable_i,
    input  logic         force_error_i,
    output logic         response_valid_o,
    output logic [127:0] response_data_o,
    output logic         response_error_o
);
    localparam int unsigned LINE_COUNT = 1 << (ADDRESS_WIDTH - 4);
    localparam int unsigned COUNT_WIDTH = (LATENCY_CYCLES < 2) ? 1 : $clog2(LATENCY_CYCLES + 1);
    logic [127:0] memory [0:LINE_COUNT-1];
    logic busy_q;
    logic [COUNT_WIDTH-1:0] count_q;
    logic write_q;
    logic [ADDRESS_WIDTH-5:0] line_q;
    logic [127:0] data_q;
    logic [15:0] enables_q;
    logic request_error_q;
    integer lane;
    integer preload_lane;

    assign request_ready_o = !busy_q;

    always_ff @(posedge clk_i) begin
        if (reset_i) begin
            busy_q <= 1'b0;
            count_q <= '0;
            response_valid_o <= 1'b0;
            response_data_o <= '0;
            response_error_o <= 1'b0;
            request_error_q <= 1'b0;
        end else begin
            response_valid_o <= 1'b0;
            response_error_o <= 1'b0;
            if (!busy_q && request_valid_i) begin
                busy_q <= 1'b1;
                count_q <= COUNT_WIDTH'(LATENCY_CYCLES);
                write_q <= request_write_i;
                line_q <= request_address_i[ADDRESS_WIDTH-1:4];
                data_q <= request_write_data_i;
                enables_q <= request_byte_enable_i;
                request_error_q <= force_error_i ||
                    (request_address_i[3:0] != 0) ||
                    (!request_write_i && request_byte_enable_i != 0);
            end else if (busy_q && count_q != 0) begin
                count_q <= count_q - 1'b1;
            end else if (busy_q) begin
                if (request_error_q) begin
                    response_data_o <= '0;
                    response_error_o <= 1'b1;
                end else if (write_q) begin
                    for (lane = 0; lane < 16; lane = lane + 1) begin
                        if (enables_q[lane]) memory[line_q][lane*8 +: 8] <= data_q[lane*8 +: 8];
                    end
                    response_data_o <= '0;
                end else begin
                    response_data_o <= memory[line_q];
                end
                response_valid_o <= 1'b1;
                busy_q <= 1'b0;
            end
        end
        // Simulation-only image loading is intentionally legal while reset
        // is asserted; reset quiets all product masters but does not clear the
        // modeled SDRAM contents.
        if (preload_valid_i) begin
            for (preload_lane = 0; preload_lane < 256;
                 preload_lane = preload_lane + 1) begin
                if (preload_byte_enable_i[preload_lane]) begin
                    memory[22'((preload_address_i + 26'(preload_lane)) >> 4)]
                          [((preload_address_i + 26'(preload_lane)) & 26'hf) * 8 +: 8]
                        <= preload_data_i[preload_lane * 8 +: 8];
                end
            end
        end
    end
endmodule
