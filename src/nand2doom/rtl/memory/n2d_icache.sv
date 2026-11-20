`include "src/common/rtl/pkg/macros.vh"

// Blocking 16 KiB two-way instruction cache with a one-edge hit.
//
// The accept edge reads both ways' packed {tag, line} words at the set taken
// directly from the request address. The following cycle (LOOKUP) compares
// tags and drives a hit response combinationally from the RAM outputs; on a
// hit, ready stays high so the next request may be accepted on the edge that
// samples this response. A miss drops ready, refills one line and returns the
// word through a registered response; a locally failed request (outside the
// 26-bit space or unaligned) also responds through that register.
module n2d_icache (
    input  logic         clk_i,
    input  logic         rst_i,

    input  logic         cpu_request_valid_i,
    output logic         cpu_request_ready_o,
    input  logic [31:0]  cpu_request_address_i,
    output logic         cpu_response_valid_o,
    output logic [31:0]  cpu_response_data_o,
    output logic         cpu_response_error_o,

    output logic         line_request_valid_o,
    input  logic         line_request_ready_i,
    output logic         line_request_write_o,
    output logic [25:0]  line_request_address_o,
    output logic [127:0] line_request_write_data_o,
    output logic [15:0]  line_request_byte_enable_o,
    input  logic         line_response_valid_i,
    input  logic [127:0] line_response_data_i,
    input  logic         line_response_error_i,

    input  logic         maintenance_valid_i,
    output logic         maintenance_ready_o,
    output logic         maintenance_done_o,
    output logic         hit_event_o,
    output logic         miss_event_o
);
    import n2d_ls_types_pkg::*;

    localparam int unsigned SET_COUNT = 512;
    localparam int unsigned SET_WIDTH = 9;
    localparam int unsigned TAG_WIDTH = 13;

    localparam icache_regs_t REGS_RESET = '{
        state: ICACHE_IDLE,
        request_address: 32'b0,
        victim_way: 1'b0,
        invalidate_set: '0,
        cpu_response_valid: 1'b0,
        cpu_response_data: 32'b0,
        cpu_response_error: 1'b0,
        maintenance_done: 1'b0,
        hit_event: 1'b0,
        miss_event: 1'b0};
    icache_regs_t regs_q, regs_d;

    logic [TAG_WIDTH-1:0] tag_read_q [0:1];
    logic [SET_COUNT-1:0] valid_q [0:1];
    logic [127:0] data_read_q [0:1];
    logic [SET_COUNT-1:0] replacement_q, replacement_d;

    logic [SET_WIDTH-1:0] request_set;
    logic [TAG_WIDTH-1:0] request_tag;
    logic [1:0]           request_word;
    logic                 hit_way0;
    logic                 hit_way1;
    logic                 selected_hit_way;
    logic                 lookup;
    logic                 lookup_hit;
    logic                 lookup_miss;
    logic [31:0]          hit_word;
    logic                 request_accept;
    logic                 request_address_error;
    logic                 refill_ok;
    logic                 invalidate;
    logic [SET_WIDTH-1:0] ram_set;

    assign request_set = regs_q.request_address[12:4];
    assign request_tag = regs_q.request_address[25:13];
    assign request_word = regs_q.request_address[3:2];
    assign hit_way0 = valid_q[0][request_set] && (tag_read_q[0] == request_tag);
    assign hit_way1 = valid_q[1][request_set] && (tag_read_q[1] == request_tag);
    assign selected_hit_way = hit_way1;
    assign lookup = regs_q.state == ICACHE_LOOKUP;
    assign lookup_hit = lookup && (hit_way0 || hit_way1);
    assign lookup_miss = lookup && !(hit_way0 || hit_way1);
    assign hit_word = data_read_q[selected_hit_way][request_word * 32 +: 32];
    assign refill_ok = regs_q.state == ICACHE_LINE_RESPONSE &&
        line_response_valid_i && !line_response_error_i;
    assign invalidate = regs_q.state == ICACHE_INVALIDATE;

    // A request is accepted from idle, or on the edge that samples a hit.
    // Ready never depends on request valid, so withdrawing an unaccepted
    // instruction request has no effect.
    assign cpu_request_ready_o = !maintenance_valid_i &&
        ((regs_q.state == ICACHE_IDLE) || lookup_hit);
    assign request_accept = cpu_request_valid_i && cpu_request_ready_o;
    assign request_address_error = (cpu_request_address_i[31:26] != 6'b0) ||
        (cpu_request_address_i[1:0] != 2'b0);
    assign maintenance_ready_o = regs_q.state == ICACHE_IDLE;
    assign line_request_valid_o = regs_q.state == ICACHE_LINE_REQUEST;
    assign line_request_write_o = 1'b0;
    assign line_request_address_o = {regs_q.request_address[25:4], 4'b0000};
    assign line_request_write_data_o = 128'b0;
    assign line_request_byte_enable_o = 16'b0;

    // The RAM address comes straight from the request address so the accept
    // edge performs the read; only a refill write addresses the captured set.
    assign ram_set = (regs_q.state == ICACHE_LINE_RESPONSE) ? request_set :
        cpu_request_address_i[12:4];

    logic [SET_WIDTH-1:0] metadata_set;
    assign metadata_set = invalidate ? regs_q.invalidate_set : request_set;
    // Pseudo-LRU bit per set: points at the way to victimize next.
    always_comb begin
        replacement_d = replacement_q;
        replacement_d[metadata_set] = !invalidate &&
            ~(lookup_hit ? selected_hit_way : regs_q.victim_way);
    end
    logic replacement_update;
    assign replacement_update = invalidate || refill_ok || lookup_hit;
    `N2A_DFF_RST_EN(replacement_q, replacement_d, clk_i,
                    replacement_update, rst_i, '0)

    // One synchronous read and one full-line write per way: infer simple
    // dual-port M9Ks. Only validity/control state resets, never RAM contents.
    genvar way;
    generate
    for (way = 0; way < 2; way = way + 1) begin : g_way
        // Each way owns its actual metadata registers; outer arrays are read views.
        logic [SET_COUNT-1:0] valid_state_q, valid_state_d;
        assign valid_q[way] = valid_state_q;
        logic valid_update;
        assign valid_update = invalidate || (refill_ok && regs_q.victim_way == 1'(way));
        always_comb begin
            valid_state_d = valid_state_q;
            valid_state_d[metadata_set] = !invalidate;
        end
        `N2A_DFF_RST_EN(valid_state_q, valid_state_d, clk_i,
                        valid_update, rst_i, '0)
        // Tag and line share one write enable and the read address equals the
        // write address, so one packed {tag, line} word per set keeps the
        // single-port shape (common memory contract, wiki/src/common/memory/HAS.md).
        logic refill_write;
        assign refill_write = !rst_i && refill_ok && regs_q.victim_way == 1'(way);
        logic [TAG_WIDTH+127:0] way_write;
        assign way_write = {request_tag, line_response_data_i};
        logic [TAG_WIDTH+127:0] way_read;
        n2a_sync_ram #(.DATA_WIDTH(TAG_WIDTH + 128), .DEPTH(SET_COUNT)) u_way_ram (
            .clk_i(clk_i),
            .write_enable_i(refill_write),
            .address_i(ram_set),
            .write_data_i(way_write),
            .read_data_o(way_read)
        );
        assign tag_read_q[way] = way_read[TAG_WIDTH+127:128];
        assign data_read_q[way] = way_read[127:0];
    end

    endgenerate

    // Pulse outputs default low every clock, including under reset, exactly
    // as the defaults are applied ahead of the reset value selection.
    always_comb begin
        regs_d = regs_q;
        regs_d.cpu_response_valid = 1'b0;
        regs_d.cpu_response_error = 1'b0;
        regs_d.maintenance_done = 1'b0;
        regs_d.hit_event = lookup_hit;
        regs_d.miss_event = lookup_miss;
        case (regs_q.state)
            ICACHE_IDLE, ICACHE_LOOKUP: begin
                if (lookup_miss) begin
                    if (!valid_q[0][request_set]) regs_d.victim_way = 1'b0;
                    else if (!valid_q[1][request_set]) regs_d.victim_way = 1'b1;
                    else regs_d.victim_way = replacement_q[request_set];
                    regs_d.state = ICACHE_LINE_REQUEST;
                end else if (lookup && !request_accept) begin
                    regs_d.state = ICACHE_IDLE;
                end else if (!lookup && maintenance_valid_i) begin
                    regs_d.invalidate_set = '0;
                    regs_d.state = ICACHE_INVALIDATE;
                end
                if (request_accept) begin
                    regs_d.request_address = cpu_request_address_i;
                    if (request_address_error) begin
                        regs_d.cpu_response_data = 32'b0;
                        regs_d.cpu_response_error = 1'b1;
                        regs_d.cpu_response_valid = 1'b1;
                        regs_d.state = ICACHE_IDLE;
                    end else begin
                        regs_d.state = ICACHE_LOOKUP;
                    end
                end
            end

            ICACHE_LINE_REQUEST: begin
                if (line_request_ready_i) regs_d.state = ICACHE_LINE_RESPONSE;
            end

            ICACHE_LINE_RESPONSE: begin
                if (line_response_valid_i) begin
                    if (line_response_error_i) begin
                        regs_d.cpu_response_data = 32'b0;
                        regs_d.cpu_response_error = 1'b1;
                    end else begin
                        regs_d.cpu_response_data = line_response_data_i[request_word * 32 +: 32];
                    end
                    regs_d.cpu_response_valid = 1'b1;
                    regs_d.state = ICACHE_IDLE;
                end
            end

            ICACHE_INVALIDATE: begin
                if (regs_q.invalidate_set == 9'd511) begin
                    regs_d.maintenance_done = 1'b1;
                    regs_d.state = ICACHE_IDLE;
                end else begin
                    regs_d.invalidate_set = regs_q.invalidate_set + 1'b1;
                end
            end

            default: regs_d.state = ICACHE_IDLE;
        endcase
    end

    `N2A_DFF_RST_VAL(regs_q, regs_d, clk_i, rst_i, REGS_RESET)
    // A hit responds combinationally in LOOKUP; a refill or local error
    // responds from the registered fields. They never coincide.
    assign cpu_response_valid_o = lookup_hit || regs_q.cpu_response_valid;
    assign cpu_response_data_o = lookup_hit ? hit_word : regs_q.cpu_response_data;
    assign cpu_response_error_o = regs_q.cpu_response_error;
    assign maintenance_done_o = regs_q.maintenance_done;
    assign hit_event_o = regs_q.hit_event;
    assign miss_event_o = regs_q.miss_event;

    `N2A_ASSERT_CLOCKED_ERROR_UNGUARDED(clk_i, rst_i,
        !(lookup_hit && regs_q.cpu_response_valid),
        "I-cache hit and registered response coincide")
endmodule
