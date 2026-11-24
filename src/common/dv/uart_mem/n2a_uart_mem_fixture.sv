`include "src/common/rtl/pkg/macros.vh"

// Simulation composition of the common UART memory endpoint: a host-side
// PHY pair the test drives by bytes, the endpoint between the device PHYs and
// a test-driven project byte stream, and a single-port word memory shared by
// the endpoint (fixed priority) and a pseudo-random CPU traffic source. The
// CPU stops at hold and acknowledges after HOLD_DELAY clocks. Monitor ports
// expose every memory transfer and every CPU stall for the scoreboard.
module n2a_uart_mem_fixture #(
    parameter int unsigned CLKS_PER_BIT = 4,
    parameter int unsigned LIVE_GAP_CYCLES = 32,
    parameter int unsigned GAP_CYCLES = 1500,
    parameter int unsigned HOLD_TIMEOUT_CYCLES = 400,
    parameter int unsigned MEM_TIMEOUT_CYCLES = 64,
    parameter int unsigned HOLD_DELAY = 24
) (
    input  logic        clk_i,
    input  logic        reset_i,

    // Host byte side.
    input  logic        host_tx_valid_i,
    input  logic [7:0]  host_tx_byte_i,
    output logic        host_tx_ready_o,
    output logic        host_rx_valid_o,
    output logic [7:0]  host_rx_byte_o,
    // Forces the host-to-device line low (framing-error injection).
    input  logic        host_line_low_i,

    // Project traffic stub.
    input  logic        down_idle_i,
    input  logic        print_valid_i,
    input  logic [7:0]  print_byte_i,
    output logic        print_ready_o,
    output logic        down_rx_valid_o,
    output logic [7:0]  down_rx_byte_o,
    output logic        down_rx_error_o,

    // Memory and CPU controls.
    input  logic        cpu_enable_i,
    input  logic        hold_block_i,
    input  logic        mem_stall_i,
    input  logic        mem_error_i,
    // Clocks an accepted request's response is held back (0: next clock).
    input  logic [15:0] mem_rsp_delay_i,

    // Monitors.
    output logic        ep_accept_o,
    output logic        ep_write_o,
    output logic [31:0] ep_addr_o,
    output logic [31:0] ep_wdata_o,
    output logic        ep_rsp_valid_o,
    output logic [31:0] ep_rsp_data_o,
    output logic        cpu_accept_o,
    output logic        cpu_write_o,
    output logic [31:0] cpu_addr_o,
    output logic [31:0] cpu_wdata_o,
    output logic        cpu_stall_o,
    output logic        hold_o,
    output logic        hold_ack_o,
    output logic        release_o,
    output logic        entry_valid_o,
    output logic [31:0] entry_addr_o,
    output logic        owner_o
);
    import n2a_uart_mem_pkg::*;

    // Program window 0x0000-0x07ff (held write and read), screen window
    // 0x1000-0x13ff (held write and read, live read), and an unmapped hole.
    localparam logic [31:0] ATTR_PROGRAM = UART_MEM_ATTR_WRITE | UART_MEM_ATTR_READ_HELD;
    localparam logic [31:0] ATTR_SCREEN = ATTR_PROGRAM | UART_MEM_ATTR_READ_LIVE;
    localparam logic [191:0] WINDOWS = {
        ATTR_SCREEN, 32'h0000_0400, 32'h0000_1000,
        ATTR_PROGRAM, 32'h0000_0800, 32'h0000_0000};

    logic serial_to_device, serial_to_host, device_line;
    logic [7:0] rx_byte, tx_byte;
    logic rx_valid, rx_error, tx_valid, tx_ready;
    logic host_rx_error;
    logic unused_busy_host_tx, unused_busy_host_rx, unused_busy_rx, unused_busy_tx;

    n2a_uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_host_tx (
        .clk_i, .reset_i, .byte_valid_i(host_tx_valid_i), .byte_ready_o(host_tx_ready_o),
        .byte_i(host_tx_byte_i), .busy_o(unused_busy_host_tx), .tx_o(serial_to_device));
    n2a_uart_rx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_host_rx (
        .clk_i, .reset_i, .rx_i(serial_to_host), .byte_o(host_rx_byte_o),
        .byte_valid_o(host_rx_valid_o), .busy_o(unused_busy_host_rx),
        .framing_error_o(host_rx_error));
    assign device_line = serial_to_device && !host_line_low_i;
    n2a_uart_rx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_rx (
        .clk_i, .reset_i, .rx_i(device_line), .byte_o(rx_byte), .byte_valid_o(rx_valid),
        .busy_o(unused_busy_rx), .framing_error_o(rx_error));
    n2a_uart_tx #(.CLKS_PER_BIT(CLKS_PER_BIT)) u_tx (
        .clk_i, .reset_i, .byte_valid_i(tx_valid), .byte_ready_o(tx_ready), .byte_i(tx_byte),
        .busy_o(unused_busy_tx), .tx_o(serial_to_host));

    logic mem_req_valid, mem_req_ready, mem_req_write, mem_rsp_valid, mem_rsp_error;
    logic [31:0] mem_req_addr, mem_req_wdata, mem_rsp_rdata;
    logic hold, hold_ack;

    n2a_uart_mem #(
        .PROJECT_ID(32'h5453_4554), .WINDOW_COUNT(2), .WINDOWS(WINDOWS),
        .ENTRY_ADDR(32'h0000_0100), .MAX_LENGTH(1024), .GAP_CYCLES(GAP_CYCLES),
        .HOLD_TIMEOUT_CYCLES(HOLD_TIMEOUT_CYCLES), .MEM_TIMEOUT_CYCLES(MEM_TIMEOUT_CYCLES),
        .LIVE_GAP_CYCLES(LIVE_GAP_CYCLES)
    ) u_endpoint (
        .clk_i, .reset_i,
        .rx_byte_i(rx_byte), .rx_valid_i(rx_valid), .rx_error_i(rx_error),
        .tx_byte_o(tx_byte), .tx_valid_o(tx_valid), .tx_ready_i(tx_ready),
        .down_rx_byte_o, .down_rx_valid_o, .down_rx_error_o,
        .down_tx_byte_i(print_byte_i), .down_tx_valid_i(print_valid_i),
        .down_tx_ready_o(print_ready_o), .down_idle_i, .owner_o,
        .hold_o(hold), .hold_ack_i(hold_ack), .release_o, .entry_valid_o, .entry_addr_o,
        .mem_req_valid_o(mem_req_valid), .mem_req_ready_i(mem_req_ready),
        .mem_req_write_o(mem_req_write), .mem_req_addr_o(mem_req_addr),
        .mem_req_wdata_o(mem_req_wdata), .mem_rsp_valid_i(mem_rsp_valid),
        .mem_rsp_rdata_i(mem_rsp_rdata), .mem_rsp_error_i(mem_rsp_error));

    logic [15:0] lfsr_q, lfsr_d;
    logic [7:0]  hold_count_q, hold_count_d;
    logic        cpu_valid_q, cpu_valid_d, cpu_write_q, cpu_write_d;
    logic [31:0] cpu_addr_q, cpu_addr_d, cpu_wdata_q, cpu_wdata_d;
    logic        cpu_ready, cpu_accept, ep_accept;
    logic        cpu_running;

    assign cpu_running = cpu_enable_i && !hold;
    assign hold_ack = hold && !hold_block_i && (hold_count_q == 8'(HOLD_DELAY)) && !cpu_valid_q;
    assign mem_req_ready = !mem_stall_i;
    assign ep_accept = mem_req_valid && mem_req_ready;
    assign cpu_ready = !ep_accept;
    assign cpu_accept = cpu_valid_q && cpu_ready;

    always_comb begin
        lfsr_d = {lfsr_q[14:0], lfsr_q[15] ^ lfsr_q[13] ^ lfsr_q[12] ^ lfsr_q[10]};
        hold_count_d = hold ? ((hold_count_q == 8'(HOLD_DELAY)) ? hold_count_q :
                               hold_count_q + 8'd1) : 8'd0;
        cpu_valid_d = cpu_valid_q && !cpu_accept;
        cpu_write_d = cpu_write_q;
        cpu_addr_d = cpu_addr_q;
        cpu_wdata_d = cpu_wdata_q;
        // Why: the model mimics a running program: a new access only after
        // the last is accepted, three clocks in four busy, and writes land in
        // the screen window, like drawing.
        if ((!cpu_valid_q || cpu_accept) && cpu_running && (lfsr_q[1:0] != 2'b00)) begin
            cpu_valid_d = 1'b1;
            cpu_write_d = lfsr_q[2];
            cpu_addr_d = lfsr_q[2] ? {22'h4, lfsr_q[11:4], 2'b00} :
                {19'h0, lfsr_q[12], 1'b0, lfsr_q[12] ? 1'b0 : lfsr_q[3], lfsr_q[11:4], 2'b00};
            cpu_wdata_d = {lfsr_q, lfsr_q ^ 16'ha5c3};
        end
    end
    `DFF_RST_VAL(lfsr_q, lfsr_d, clk_i, reset_i, 16'hace1)
    `DFF_RST_VAL(hold_count_q, hold_count_d, clk_i, reset_i, '0)
    `DFF_RST_VAL(cpu_valid_q, cpu_valid_d, clk_i, reset_i, 1'b0)
    `DFF_RST_VAL(cpu_write_q, cpu_write_d, clk_i, reset_i, 1'b0)
    `DFF_RST_VAL(cpu_addr_q, cpu_addr_d, clk_i, reset_i, '0)
    `DFF_RST_VAL(cpu_wdata_q, cpu_wdata_d, clk_i, reset_i, '0)

    logic        ram_write;
    logic [10:0] ram_address;
    logic [31:0] ram_wdata;
    logic        rsp_valid_d, rsp_error_d;
    logic        raw_valid_q, raw_error_q;
    logic [31:0] ram_rdata;
    logic        held_valid_q, held_valid_d, held_error_q, held_error_d;
    logic [31:0] held_data_q, held_data_d;
    logic [15:0] held_count_q, held_count_d;
    logic        held_release;
    assign ram_write = ep_accept ? mem_req_write : (cpu_accept && cpu_write_q);
    assign ram_address = ep_accept ? mem_req_addr[12:2] : cpu_addr_q[12:2];
    assign ram_wdata = ep_accept ? mem_req_wdata : cpu_wdata_q;
    n2a_sync_ram #(.DATA_WIDTH(32), .DEPTH(2048)) u_ram (
        .clk_i, .write_enable_i(ram_write), .address_i(ram_address),
        .write_data_i(ram_wdata), .read_data_o(ram_rdata));
    assign rsp_valid_d = ep_accept;
    assign rsp_error_d = ep_accept && mem_error_i;
    `DFF_RST_VAL(raw_valid_q, rsp_valid_d, clk_i, reset_i, 1'b0)
    `DFF_RST_VAL(raw_error_q, rsp_error_d, clk_i, reset_i, 1'b0)
    // Why: the data is captured on the clock after acceptance, as the RAM reads
    // it, so mem_rsp_delay_i changes only when the response arrives, not its data.
    assign held_release = held_valid_q && (held_count_q == 16'd0);
    always_comb begin
        held_valid_d = held_valid_q && !held_release;
        held_error_d = held_error_q;
        held_data_d = held_data_q;
        held_count_d = (held_valid_q && (held_count_q != 16'd0)) ? held_count_q - 16'd1 :
            held_count_q;
        if (raw_valid_q && (mem_rsp_delay_i != 16'd0)) begin
            held_valid_d = 1'b1;
            held_error_d = raw_error_q;
            held_data_d = ram_rdata;
            held_count_d = mem_rsp_delay_i - 16'd1;
        end
    end
    `DFF_RST_VAL(held_valid_q, held_valid_d, clk_i, reset_i, 1'b0)
    `DFF_RST_VAL(held_error_q, held_error_d, clk_i, reset_i, 1'b0)
    `DFF_RST_VAL(held_data_q, held_data_d, clk_i, reset_i, '0)
    `DFF_RST_VAL(held_count_q, held_count_d, clk_i, reset_i, '0)
    assign mem_rsp_valid = (raw_valid_q && (mem_rsp_delay_i == 16'd0)) || held_release;
    assign mem_rsp_error = held_release ? held_error_q : raw_error_q;
    assign mem_rsp_rdata = held_release ? held_data_q : ram_rdata;

    assign ep_accept_o = ep_accept;
    assign ep_write_o = mem_req_write;
    assign ep_addr_o = mem_req_addr;
    assign ep_wdata_o = mem_req_wdata;
    assign ep_rsp_valid_o = mem_rsp_valid;
    assign ep_rsp_data_o = mem_rsp_rdata;
    assign cpu_accept_o = cpu_accept;
    assign cpu_write_o = cpu_write_q;
    assign cpu_addr_o = cpu_addr_q;
    assign cpu_wdata_o = cpu_wdata_q;
    assign cpu_stall_o = cpu_valid_q && !cpu_ready;
    assign hold_o = hold;
    assign hold_ack_o = hold_ack;

    `UNUSED_BITS(unused_status, {unused_busy_host_tx, unused_busy_host_rx,
        unused_busy_rx, unused_busy_tx, host_rx_error})
endmodule
