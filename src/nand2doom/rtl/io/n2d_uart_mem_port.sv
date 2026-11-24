`include "src/common/rtl/pkg/macros.vh"

// Bus adapter of the common UART memory endpoint in nand2doom (wiki/src/nand2doom/rtl/io/mas.md
// "UART memory endpoint"). Screen addresses (the front framebuffer view and
// the scanned palette view) become one-cycle platform screen reads; every
// other address, SDRAM inside the endpoint's held-only window, becomes one
// word inside one transaction of the existing debug memory path, so loads
// keep its staging, commit and rollback. One request at a time.
module n2d_uart_mem_port
    import n2d_pkg::*;
(
    input  logic                  clk_i,
    input  logic                  reset_i,

    // Endpoint word port.
    input  logic                  ep_req_valid_i,
    output logic                  ep_req_ready_o,
    input  logic                  ep_req_write_i,
    input  logic [31:0]           ep_req_addr_i,
    input  logic [31:0]           ep_req_wdata_i,
    output logic                  ep_rsp_valid_o,
    output logic [31:0]           ep_rsp_rdata_o,
    output logic                  ep_rsp_error_o,

    // Debug memory path and platform screen port.
    output logic                  busy_o,
    output t_n2d_debug_txn_req    txn_req_o,
    input  t_n2d_debug_txn_status txn_status_i,
    output t_n2d_debug_mem_req    mem_req_o,
    input  t_n2d_debug_mem_rsp    mem_rsp_i,
    output t_n2d_screen_req       screen_req_o,
    input  t_n2d_screen_rsp       screen_rsp_i
);
    import n2d_types_pkg::*;

    localparam t_mem_port_regs REGS_RESET = '{state: MEM_PORT_IDLE, default: '0};
    t_mem_port_regs regs_q, regs_d;
    logic screen_address, ep_accept;

    assign screen_address = (ep_req_addr_i[31:16] == N2D_MEM_SCREEN_BASE[31:16]) ||
        (ep_req_addr_i[31:16] == N2D_MEM_PALETTE_BASE[31:16]);
    assign ep_req_ready_o = (regs_q.state == MEM_PORT_IDLE);
    assign ep_accept = ep_req_valid_i && ep_req_ready_o;

    always_comb begin
        regs_d = regs_q;
        regs_d.rsp_valid = 1'b0;
        regs_d.txn_begin = 1'b0;
        regs_d.txn_commit = 1'b0;
        regs_d.txn_abort = 1'b0;
        case (regs_q.state)
            MEM_PORT_IDLE: if (ep_accept) begin
                regs_d.write = ep_req_write_i;
                regs_d.addr = ep_req_addr_i;
                regs_d.wdata = ep_req_wdata_i;
                regs_d.error = 1'b0;
                regs_d.state = screen_address ? MEM_PORT_SCREEN : MEM_PORT_ARB;
            end
            MEM_PORT_SCREEN: if (screen_rsp_i.ready) regs_d.state = MEM_PORT_SCREEN_WAIT;
            MEM_PORT_SCREEN_WAIT: if (screen_rsp_i.rsp_valid) begin
                regs_d.rdata = screen_rsp_i.rdata;
                regs_d.rsp_valid = 1'b1;
                regs_d.state = MEM_PORT_IDLE;
            end
            MEM_PORT_ARB: if (txn_status_i.arb_grant) begin
                regs_d.txn_begin = 1'b1;
                regs_d.state = MEM_PORT_REQ;
            end
            MEM_PORT_REQ: begin
                if (!txn_status_i.arb_grant) begin
                    regs_d.error = 1'b1;
                    regs_d.txn_abort = 1'b1;
                    regs_d.state = MEM_PORT_FINISH;
                end else if (mem_rsp_i.ready) begin
                    regs_d.state = MEM_PORT_RSP;
                end
            end
            MEM_PORT_RSP: begin
                if (!txn_status_i.arb_grant || mem_rsp_i.rsp_valid) begin
                    regs_d.rdata = mem_rsp_i.rdata;
                    regs_d.error = !txn_status_i.arb_grant || mem_rsp_i.rsp_error;
                    regs_d.txn_commit = !regs_d.error;
                    regs_d.txn_abort = regs_d.error;
                    regs_d.state = MEM_PORT_FINISH;
                end
            end
            MEM_PORT_FINISH: if (txn_status_i.txn_done) begin
                // Ref: wiki/src/nand2doom/rtl/io/mas.md "Host-memory transaction boundary":
                // commit publishes the staged word; abort waits for txn_done (discard or
                // rollback) before the response.
                regs_d.error = regs_q.error || txn_status_i.txn_error;
                regs_d.rsp_valid = 1'b1;
                regs_d.state = MEM_PORT_IDLE;
            end
            default: regs_d.state = MEM_PORT_IDLE;
        endcase
    end

    `DFF_RST_VAL(regs_q, regs_d, clk_i, reset_i, REGS_RESET)

    assign busy_o = (regs_q.state != MEM_PORT_IDLE);
    assign txn_req_o = '{
        arb_req: (regs_q.state == MEM_PORT_ARB) || (regs_q.state == MEM_PORT_REQ) ||
            (regs_q.state == MEM_PORT_RSP) || (regs_q.state == MEM_PORT_FINISH),
        txn_begin: regs_q.txn_begin, txn_commit: regs_q.txn_commit,
        txn_abort: regs_q.txn_abort};
    assign mem_req_o = '{
        valid: (regs_q.state == MEM_PORT_REQ) && txn_status_i.arb_grant,
        write: regs_q.write, addr: regs_q.addr, wdata: regs_q.wdata,
        wstrb: regs_q.write ? 4'hf : 4'h0};
    assign screen_req_o = '{valid: (regs_q.state == MEM_PORT_SCREEN),
        palette: (regs_q.addr[31:16] == N2D_MEM_PALETTE_BASE[31:16]),
        offset: regs_q.addr[15:0]};
    assign ep_rsp_valid_o = regs_q.rsp_valid;
    assign ep_rsp_rdata_o = regs_q.rdata;
    assign ep_rsp_error_o = regs_q.error;

    `ASSERT_CLOCKED_FATAL(clk_i, reset_i, !(regs_q.txn_commit && regs_q.txn_abort),
        "endpoint port commit and abort overlap")
endmodule
