`default_nettype none
`include "src/common/rtl/pkg/macros.vh"

// Bus adapter of the common UART memory endpoint in nand2mario
// (wiki/src/nand2mario/rtl/has.md, uart row). One endpoint
// word becomes four byte requests on the cartridge backing's load port,
// lowest address first: byte k of the word is bits [8k+7:8k]. Address bit 21
// selects the cartridge RAM store, as on the loader's own frames, so the ROM
// window is at 0 and the RAM window at 0x0020_0000. One request at a time;
// the endpoint enforces the windows before a request reaches this port.
module n2m_uart_mem_port (
    input  logic                                   clk_i,
    input  logic                                   reset_i,

    // Endpoint word port.
    input  logic                                   ep_req_valid_i,
    output logic                                   ep_req_ready_o,
    input  logic                                   ep_req_write_i,
    input  logic [31:0]                            ep_req_addr_i,
    input  logic [31:0]                            ep_req_wdata_i,
    output logic                                   ep_rsp_valid_o,
    output logic [31:0]                            ep_rsp_rdata_o,
    output logic                                   ep_rsp_error_o,

    // Cartridge backing load port (one outstanding request).
    output logic                                   busy_o,
    output n2m_cartridge_pkg::t_backing_request    load_request_o,
    input  logic                                   load_ready_i,
    input  n2m_cartridge_pkg::t_backing_response   load_response_i
);
    import n2m_cartridge_pkg::*;

    typedef enum logic [1:0] {PORT_IDLE, PORT_REQUEST, PORT_WAIT, PORT_RESPOND} t_port_state;

    t_port_state state_q, state_d;
    logic        write_q, write_d;
    logic [31:0] addr_q, addr_d, data_q, data_d;
    logic [1:0]  lane_q, lane_d;
    logic        accept;

    assign ep_req_ready_o = state_q == PORT_IDLE;
    assign accept = load_request_o.valid && load_ready_i;
    assign busy_o = state_q != PORT_IDLE;
    assign ep_rsp_valid_o = state_q == PORT_RESPOND;
    assign ep_rsp_rdata_o = data_q;
    assign ep_rsp_error_o = 1'b0;

    always_comb begin
        load_request_o = '0;
        load_request_o.valid = state_q == PORT_REQUEST;
        load_request_o.write = write_q;
        load_request_o.ram = addr_q[21];
        load_request_o.address = addr_q[ROM_ADDRESS_WIDTH-1:0] + t_rom_address'(lane_q);
        load_request_o.write_data = data_q[8 * lane_q +: 8];
    end

    always_comb begin
        state_d = state_q;
        write_d = write_q;
        addr_d = addr_q;
        data_d = data_q;
        lane_d = lane_q;
        case (state_q)
            PORT_IDLE: if (ep_req_valid_i) begin
                write_d = ep_req_write_i;
                addr_d = ep_req_addr_i;
                data_d = ep_req_write_i ? ep_req_wdata_i : 32'h0;
                lane_d = 2'd0;
                state_d = PORT_REQUEST;
            end
            PORT_REQUEST: if (accept) state_d = PORT_WAIT;
            PORT_WAIT: if (load_response_i.valid) begin
                if (!write_q) data_d[8 * lane_q +: 8] = load_response_i.read_data;
                lane_d = lane_q + 2'd1;
                state_d = (lane_q == 2'd3) ? PORT_RESPOND : PORT_REQUEST;
            end
            PORT_RESPOND: state_d = PORT_IDLE;
            default: state_d = PORT_IDLE;
        endcase
    end

    `DFF_RST_VAL(state_q, state_d, clk_i, reset_i, PORT_IDLE)
    `DFF_RST(write_q, write_d, clk_i, reset_i)
    `DFF_RST(addr_q, addr_d, clk_i, reset_i)
    `DFF_RST(data_q, data_d, clk_i, reset_i)
    `DFF_RST(lane_q, lane_d, clk_i, reset_i)

    `ASSERT(load_response_requested, clk_i, reset_i,
        !load_response_i.valid || (state_q == PORT_WAIT))
    `ASSERT(request_aligned, clk_i, reset_i,
        !(ep_req_valid_i && ep_req_ready_o) || (ep_req_addr_i[1:0] == 2'b00))
endmodule
