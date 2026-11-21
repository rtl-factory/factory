`include "src/common/rtl/pkg/macros.vh"

// Blocking write-back, write-allocate data cache with a one-edge hit: 512
// sets of 16-byte lines and WAYS ways (2 = 16 KiB, 4 = 32 KiB), replaced by
// invalid-first, then tree pseudo-LRU. A board top selects WAYS.
//
// The accept edge reads every way's tag and data RAMs at the set taken
// directly from the request address. The following cycle (LOOKUP) compares
// tags and drives a hit response combinationally; on a hit, ready stays high
// so the next request may be accepted on the edge that samples this response.
// A store hit merges its bytes into the line already read and writes the full
// line on that same edge, so there is no read-modify-write bubble. Because a
// request accepted on that edge reads the old RAM word, a one-entry forward
// record substitutes the written line in the immediately following lookup.
// A miss drops ready, refills first, buffers its dirty victim, and completes
// through a registered response, as does a request outside the SDRAM space.
//
// With PREFETCH_LINES set (1 or 2), every line the cache installs from
// memory or from the prefetch buffer requests the next sequential line as a
// low-priority prefetch into a PREFETCH_LINES-entry buffer. The cache keeps
// one line transaction: a presented prefetch holds until accepted, and a
// demand writeback or refill waits for its response. A miss whose line is
// buffered installs it from the buffer in the refill-request cycle instead of
// reading memory. Displacing a dirty line drops a buffered copy of its line, and
// maintenance clears the buffer and discards a prefetch in flight.
//
// Per-set metadata (every way's tag, valid and dirty bits and the set's
// pseudo-LRU tree) is one block-RAM word per set, read with the data on the
// accept edge and rewritten whole (read-modify-write) by a hit, writeback,
// refill or maintenance step. A write lands one edge after the read it
// collides with, so a one-cycle metadata forward record substitutes the word
// written on the read edge. After reset a hardware sweep zeroes all 512 words
// one per clock; request and maintenance ready stay low until it completes.
module n2d_dcache #(
    // Supported geometries: 2 (16 KiB) and 4 (32 KiB) ways.
    parameter int unsigned WAYS = 4,
    // Prefetch buffer lines: 0 disables next-line prefetch.
    parameter int unsigned PREFETCH_LINES = 0
) (
    input  logic         clk_i,
    input  logic         reset_i,

    input  logic         cpu_request_valid_i,
    output logic         cpu_request_ready_o,
    input  logic         cpu_request_write_i,
    input  logic [31:0]  cpu_request_address_i,
    input  logic [31:0]  cpu_request_write_data_i,
    input  logic [3:0]   cpu_request_byte_enable_i,
    output logic         cpu_response_valid_o,
    output logic [31:0]  cpu_response_data_o,
    output logic         cpu_response_error_o,

    output logic         line_request_valid_o,
    input  logic         line_request_ready_i,
    output logic         line_request_write_o,
    output logic [25:0]  line_request_address_o,
    output logic [127:0] line_request_write_data_o,
    output logic [15:0]  line_request_byte_enable_o,
    // The presented line request is a prefetch: lowest arbitration priority.
    output logic         line_request_prefetch_o,
    // A background victim drain: also below other masters' demand traffic.
    output logic         line_request_background_o,
    input  logic         line_response_valid_i,
    input  logic [127:0] line_response_data_i,
    input  logic         line_response_error_i,

    input  logic         maintenance_valid_i,
    output logic         maintenance_ready_o,
    output logic         maintenance_done_o,
    output logic         maintenance_error_o,
    output logic         hit_event_o,
    output logic         miss_event_o,
    output logic         prefetch_issue_event_o,
    output logic         prefetch_useful_event_o,
    output logic         prefetch_drop_event_o,
    output logic         prefetch_deferred_event_o,
    output logic         prefetch_cancel_event_o,
    output logic         write_buffer_pending_o,
    output logic         write_buffer_wait_o
);
    import n2d_ls_types_pkg::*;

    localparam int unsigned SET_COUNT = 512;
    localparam int unsigned SET_WIDTH = 9;
    localparam int unsigned TAG_WIDTH = 13;
    localparam int unsigned WAY_WIDTH = $clog2(WAYS);
    // Tree pseudo-LRU: WAYS-1 node bits per set, heap-ordered from the root
    // (node n at bit n-1, children 2n and 2n+1). A node bit names the subtree
    // holding the next victim; a two-way set reduces to one victim bit.
    localparam int unsigned PLRU_WIDTH = WAYS - 1;
    localparam int unsigned LINE_WIDTH = 22;
    // One metadata word per set, packed {tag[WAYS], valid, dirty, plru}.
    localparam int unsigned SET_WORD_WIDTH = WAYS * TAG_WIDTH + 2 * WAYS + PLRU_WIDTH;
    localparam bit PREFETCH = PREFETCH_LINES != 0;
    // Buffer storage keeps one slot when prefetch is disabled; it is never
    // loaded then, so synthesis removes it.
    localparam int unsigned BUFFER_SLOTS = PREFETCH ? PREFETCH_LINES : 1;
    localparam int unsigned SLOT_WIDTH = BUFFER_SLOTS > 1 ? $clog2(BUFFER_SLOTS) : 1;

    `ASSERT_INITIAL(WAYS == 2 || WAYS == 4)
    `ASSERT_INITIAL(PREFETCH_LINES <= 2)

    localparam t_dcache_regs REGS_RESET = '{
        state: DCACHE_IDLE,
        request_address: 30'b0,
        request_write: 1'b0,
        request_write_data: 32'b0,
        request_byte_enable: 4'b0,
        init_done: 1'b0,
        init_set: '0,
        meta_read_set: '0,
        meta_forward_valid: 1'b0,
        meta_forward_set: '0,
        line_buffer: '0,
        miss_dirty: 1'b0,
        write_buffer_valid: 1'b0,
        write_buffer_line: '0,
        write_buffer_data: '0,
        write_buffer_active: 1'b0,
        write_buffer_accepted: 1'b0,
        write_buffer_failed: 1'b0,
        forward_valid: 1'b0,
        forward_set: '0,
        maintenance_set: '0,
        cpu_response_valid: 1'b0,
        cpu_response_data: 32'b0,
        cpu_response_error: 1'b0,
        maintenance_done: 1'b0,
        maintenance_error: 1'b0,
        hit_event: 1'b0,
        miss_event: 1'b0,
        prefetch_valid: 1'b0,
        prefetch_presented: 1'b0,
        prefetch_accepted: 1'b0,
        prefetch_discard: 1'b0,
        prefetch_line: '0,
        prefetch_issue_event: 1'b0,
        prefetch_useful_event: 1'b0,
        prefetch_cancel_event: 1'b0,
        prefetch_drop_backlog: 3'b0};
    t_dcache_regs regs_q, regs_d;
    // Why: fields whose widths follow WAYS or PREFETCH_LINES are registers of
    // their own beside the packaged cache state (same zero reset).
    // LOOKUP captures the miss victim way and the miss set's metadata word
    // because a successful refill rewrites that word with the new tag and
    // transfers its dirty victim.
    logic [WAY_WIDTH-1:0]      victim_way_q, victim_way_d;
    logic [SET_WORD_WIDTH-1:0] miss_word_q, miss_word_d;
    // Why: the metadata word, if any, written on the edge that read
    // meta_read_set; the synchronous RAM read cannot return it.
    logic [SET_WORD_WIDTH-1:0] meta_forward_word_q, meta_forward_word_d;
    logic [WAY_WIDTH-1:0]      forward_way_q, forward_way_d;
    logic [WAY_WIDTH-1:0]      maintenance_way_q, maintenance_way_d;
    logic [BUFFER_SLOTS-1:0]                  buffer_valid_q, buffer_valid_d;
    logic [BUFFER_SLOTS-1:0][LINE_WIDTH-1:0] buffer_line_q, buffer_line_d;
    logic [BUFFER_SLOTS-1:0][127:0]          buffer_data_q, buffer_data_d;
    logic [SLOT_WIDTH-1:0]                    buffer_next_q, buffer_next_d;

    logic [127:0] data_read_q [0:WAYS-1];
    logic [127:0] lookup_line [0:WAYS-1];
    logic [SET_WORD_WIDTH-1:0] set_word_read;
    logic [SET_WORD_WIDTH-1:0] set_word;
    assign set_word = (regs_q.meta_forward_valid &&
        regs_q.meta_forward_set == regs_q.meta_read_set) ?
        meta_forward_word_q : set_word_read;
    logic [WAYS-1:0][TAG_WIDTH-1:0] set_tag;
    logic [WAYS-1:0] set_valid;
    logic [WAYS-1:0] set_dirty;
    logic [PLRU_WIDTH-1:0] set_plru;
    assign {set_tag, set_valid, set_dirty, set_plru} = set_word;
    logic [WAYS-1:0][TAG_WIDTH-1:0] miss_tag;
    assign miss_tag = miss_word_q[SET_WORD_WIDTH-1 -: WAYS * TAG_WIDTH];
    logic [PLRU_WIDTH-1:0] miss_plru;
    assign miss_plru = miss_word_q[PLRU_WIDTH-1:0];

    logic [SET_WIDTH-1:0] request_set;
    logic [TAG_WIDTH-1:0] request_tag;
    logic [1:0] request_word;
    logic [WAYS-1:0] hit_ways;
    logic [WAY_WIDTH-1:0] selected_hit_way;
    logic [WAY_WIDTH-1:0] selected_victim_way;
    logic [127:0] merged_refill_line;
    integer refill_lane;
    logic [127:0] merged_hit_line;
    logic [31:0] hit_word;
    logic maintenance_advance_set;
    logic lookup;
    logic lookup_hit;
    logic lookup_miss;
    logic store_hit;
    logic request_accept;
    logic request_address_error;
    logic [SET_WIDTH-1:0] accept_set;

    assign request_set = regs_q.request_address[12:4];
    assign request_tag = regs_q.request_address[25:13];
    assign request_word = regs_q.request_address[3:2];
    assign accept_set = cpu_request_address_i[12:4];
    logic [TAG_WIDTH-1:0] victim_tag;
    assign victim_tag = miss_tag[victim_way_q];
    logic [WAY_WIDTH-1:0] plru_victim_way;
    always_comb begin
        int unsigned node;
        node = 1;
        plru_victim_way = '0;
        for (int level = 0; level < int'(WAY_WIDTH); level++) begin
            plru_victim_way = (plru_victim_way << 1) |
                WAY_WIDTH'(set_plru[node - 1]);
            node = 2 * node + 32'(set_plru[node - 1]);
        end
    end

    // Why: at most one way holds a tag, so the hit way is the OR of the indices
    // of the hitting ways. The victim is the lowest invalid way, otherwise the
    // set's pseudo-LRU way, so invalid ways fill before a valid line is evicted.
    always_comb begin
        selected_hit_way = '0;
        selected_victim_way = plru_victim_way;
        for (int way_index = WAYS - 1; way_index >= 0; way_index--) begin
            hit_ways[way_index] = set_valid[way_index] &&
                (set_tag[way_index] == request_tag);
            if (hit_ways[way_index]) selected_hit_way |= WAY_WIDTH'(way_index);
            if (!set_valid[way_index])
                selected_victim_way = WAY_WIDTH'(way_index);
        end
    end
    assign lookup = regs_q.state == DCACHE_LOOKUP;
    assign lookup_hit = lookup && (hit_ways != '0);
    assign lookup_miss = lookup && (hit_ways == '0);
    assign store_hit = lookup_hit && regs_q.request_write;

    // Why: forward the line written on the previous edge over the old RAM word
    // this lookup read for the same set and way, because the synchronous read
    // returned the pre-write data.
    genvar fwd_way;
    generate
    for (fwd_way = 0; fwd_way < WAYS; fwd_way = fwd_way + 1) begin : g_forward
        logic forward_match;
        assign forward_match = regs_q.forward_valid &&
            (regs_q.forward_set == request_set) &&
            (forward_way_q == WAY_WIDTH'(fwd_way));
        assign lookup_line[fwd_way] = forward_match ? regs_q.line_buffer :
            data_read_q[fwd_way];
    end
    endgenerate
    assign hit_word = lookup_line[selected_hit_way][request_word * 32 +: 32];

    // Ref: wiki/src/nand2doom/rtl/memory/mas.md "Next-line prefetch": only the
    // refill-request state consults the prefetch buffer, after the arrays missed.
    logic [LINE_WIDTH-1:0] request_line;
    assign request_line = regs_q.request_address[25:4];
    logic [BUFFER_SLOTS-1:0] buffer_match;
    logic [SLOT_WIDTH-1:0] buffer_match_slot;
    always_comb begin
        buffer_match_slot = '0;
        for (int slot = 0; slot < int'(BUFFER_SLOTS); slot++) begin
            buffer_match[slot] = PREFETCH && buffer_valid_q[slot] &&
                buffer_line_q[slot] == request_line;
            if (buffer_match[slot]) buffer_match_slot = SLOT_WIDTH'(slot);
        end
    end
    // Ref: wiki/src/nand2doom/rtl/memory/mas.md "Next-line prefetch": a buffered
    // miss line installs in the refill-request cycle as a refill would, with no
    // line request.
    logic buffer_refill;
    logic write_buffer_block;
    assign write_buffer_block = regs_q.write_buffer_valid &&
        (regs_q.miss_dirty || regs_q.write_buffer_line == request_line);
    assign buffer_refill = regs_q.state == DCACHE_REFILL_REQUEST &&
        !write_buffer_block && (buffer_match != '0);
    logic refill_ok;
    assign refill_ok = (regs_q.state == DCACHE_REFILL_RESPONSE &&
        line_response_valid_i && !line_response_error_i) || buffer_refill;
    logic [127:0] refill_line;
    assign refill_line = buffer_refill ? buffer_data_q[buffer_match_slot] :
        line_response_data_i;
    logic write_buffer_enqueue;
    assign write_buffer_enqueue = refill_ok && regs_q.miss_dirty;
    logic write_buffer_response;
    assign write_buffer_response = regs_q.write_buffer_active &&
        regs_q.write_buffer_accepted && line_response_valid_i;
    logic maintenance_entry_dirty;
    assign maintenance_entry_dirty = set_valid[maintenance_way_q] &&
        set_dirty[maintenance_way_q];
    logic maintenance_clear;
    assign maintenance_clear = (regs_q.state == DCACHE_MAINTENANCE_SCAN &&
         !maintenance_entry_dirty) ||
        (regs_q.state == DCACHE_MAINTENANCE_WRITEBACK_RESPONSE &&
         line_response_valid_i && !line_response_error_i);

    // Why: pointing every node on the way's path at the other subtree makes the
    // way of a hit or installing refill the most recently used, so the next
    // victim comes from elsewhere.
    logic [WAY_WIDTH-1:0] touched_way;
    assign touched_way = lookup_hit ? selected_hit_way : victim_way_q;
    logic [PLRU_WIDTH-1:0] touched_plru;
    always_comb begin
        int unsigned node;
        logic branch;
        node = 1;
        touched_plru = lookup_hit ? set_plru : miss_plru;
        for (int level = 0; level < int'(WAY_WIDTH); level++) begin
            branch = touched_way[int'(WAY_WIDTH) - 1 - level];
            touched_plru[node - 1] = !branch;
            node = 2 * node + 32'(branch);
        end
    end

    // Why: exactly one metadata writer per clock (the reset sweep, a hit, an
    // install, or a maintenance clear), so each rewrites the whole word from
    // the current one.
    logic meta_write;
    assign meta_write = !reset_i && (!regs_q.init_done || lookup_hit ||
        refill_ok || maintenance_clear);
    logic [SET_WIDTH-1:0] meta_write_set;
    assign meta_write_set = !regs_q.init_done ? regs_q.init_set :
        maintenance_clear ? regs_q.maintenance_set : request_set;
    logic [WAYS-1:0][TAG_WIDTH-1:0] write_tag;
    logic [WAYS-1:0] write_valid;
    logic [WAYS-1:0] write_dirty;
    logic [PLRU_WIDTH-1:0] write_plru;
    always_comb begin
        {write_tag, write_valid, write_dirty, write_plru} = miss_word_q;
        if (!regs_q.init_done) begin
            {write_tag, write_valid, write_dirty, write_plru} = '0;
        end else if (lookup_hit) begin
            {write_tag, write_valid, write_dirty, write_plru} = set_word;
            write_plru = touched_plru;
            if (store_hit) write_dirty[selected_hit_way] = 1'b1;
        end else if (refill_ok) begin
            write_tag[victim_way_q] = request_tag;
            write_valid[victim_way_q] = 1'b1;
            write_dirty[victim_way_q] = regs_q.request_write;
            write_plru = touched_plru;
        end else begin
            {write_tag, write_valid, write_dirty, write_plru} = set_word;
            write_valid[maintenance_way_q] = 1'b0;
            write_dirty[maintenance_way_q] = 1'b0;
        end
    end
    logic [SET_WORD_WIDTH-1:0] meta_write_word;
    assign meta_write_word = {write_tag, write_valid, write_dirty, write_plru};

    // Why: read the next set while retiring the current last-way entry. This
    // keeps the clean maintenance walk at one entry per cycle with synchronous
    // RAM.
    logic maintenance_last_way;
    assign maintenance_last_way = maintenance_way_q == WAY_WIDTH'(WAYS - 1);
    logic maintenance_last_set;
    assign maintenance_last_set = regs_q.maintenance_set == SET_WIDTH'(SET_COUNT - 1);
    assign maintenance_advance_set = maintenance_last_way && maintenance_clear;

    always_comb begin
        merged_refill_line = refill_line;
        for (refill_lane = 0; refill_lane < 4; refill_lane = refill_lane + 1) begin
            if (regs_q.request_write && regs_q.request_byte_enable[refill_lane]) begin
                merged_refill_line[(request_word * 32) + (refill_lane * 8) +: 8] =
                    regs_q.request_write_data[refill_lane * 8 +: 8];
            end
        end
    end

    always_comb begin
        merged_hit_line = lookup_line[selected_hit_way];
        for (int lane = 0; lane < 4; lane++) begin
            if (regs_q.request_byte_enable[lane])
                merged_hit_line[request_word * 32 + lane * 8 +: 8] =
                    regs_q.request_write_data[lane * 8 +: 8];
        end
    end

    assign cpu_request_ready_o = regs_q.init_done && !maintenance_valid_i &&
        ((regs_q.state == DCACHE_IDLE) || lookup_hit);
    assign request_accept = cpu_request_valid_i && cpu_request_ready_o;
    assign request_address_error = cpu_request_address_i[31:26] != 6'b0;
    assign maintenance_ready_o = regs_q.init_done && regs_q.state == DCACHE_IDLE;
    // Ref: wiki/src/nand2doom/rtl/memory/mas.md "Requirements" MEM-R6: one
    // outstanding line transaction, so a demand request is presented only while
    // no prefetch is presented or owed a response, and a presented prefetch
    // holds its payload until accepted.
    logic prefetch_inflight;
    assign prefetch_inflight = regs_q.prefetch_valid && regs_q.prefetch_presented;
    logic prefetch_present;
    assign prefetch_present = prefetch_inflight && !regs_q.prefetch_accepted;
    logic select_prefetch;
    // Why: the candidate stays internal until demand has completed, so a
    // prefetch never delays a demand miss: a hit or response may finish on this
    // edge, but any new CPU request wins. Once offered, the line request holds
    // until accepted.
    assign select_prefetch = regs_q.prefetch_valid && !regs_q.prefetch_presented &&
        (regs_q.state == DCACHE_IDLE || lookup_hit) && !cpu_request_valid_i &&
        !maintenance_valid_i &&
        !regs_q.write_buffer_valid && !regs_q.write_buffer_active;
    logic demand_request;
    assign demand_request = !prefetch_inflight && !regs_q.write_buffer_active &&
        ((regs_q.state == DCACHE_REFILL_REQUEST && !buffer_refill && !write_buffer_block) ||
         (regs_q.state == DCACHE_MAINTENANCE_WRITEBACK_REQUEST));
    logic demand_request_ready;
    assign demand_request_ready = demand_request && line_request_ready_i;
    logic write_buffer_present;
    assign write_buffer_present = regs_q.write_buffer_active && !regs_q.write_buffer_accepted;
    logic start_write_buffer;
    // Why: unpresented demand refills win over a background drain so a drain
    // adds no miss latency. A full slot or same-line read instead forces a
    // drain, since the victim has nowhere to queue or memory holds stale bytes;
    // maintenance drains first.
    assign start_write_buffer = regs_q.write_buffer_valid &&
        !regs_q.write_buffer_active && !prefetch_inflight &&
        !demand_request &&
        (regs_q.state == DCACHE_IDLE || regs_q.state == DCACHE_LOOKUP ||
         regs_q.state == DCACHE_REFILL_REQUEST || regs_q.state == DCACHE_MAINTENANCE_DRAIN) &&
        (!regs_q.write_buffer_failed || regs_q.state == DCACHE_MAINTENANCE_DRAIN ||
         (regs_q.state == DCACHE_REFILL_REQUEST && write_buffer_block));
    assign line_request_valid_o = prefetch_present || demand_request || write_buffer_present;
    assign line_request_prefetch_o = prefetch_present;
    assign line_request_background_o = write_buffer_present;
    assign line_request_write_o = write_buffer_present ||
        (!prefetch_present && regs_q.state == DCACHE_MAINTENANCE_WRITEBACK_REQUEST);
    assign line_request_address_o = write_buffer_present ?
        {regs_q.write_buffer_line, 4'b0000} :
        prefetch_present ? {regs_q.prefetch_line, 4'b0000} :
        (regs_q.state == DCACHE_REFILL_REQUEST) ?
        {regs_q.request_address[25:4], 4'b0000} :
        (regs_q.state == DCACHE_MAINTENANCE_WRITEBACK_REQUEST) ?
        {set_tag[maintenance_way_q], regs_q.maintenance_set, 4'b0000} :
        {victim_tag, request_set, 4'b0000};
    assign line_request_write_data_o = write_buffer_present ? regs_q.write_buffer_data :
        !line_request_write_o ? 128'b0 :
        (regs_q.state == DCACHE_MAINTENANCE_WRITEBACK_REQUEST) ?
        data_read_q[maintenance_way_q] : regs_q.line_buffer;
    assign line_request_byte_enable_o = line_request_write_o ? 16'hffff : 16'h0000;

    // Why: the CPU-side read address comes straight from the request address so
    // the accept edge performs the read. The maintenance walk reads its own
    // set; refill and store-hit writes use the captured request set.
    logic [SET_WIDTH-1:0] maintenance_read_set;
    assign maintenance_read_set = regs_q.maintenance_set + 9'(maintenance_advance_set);
    logic [SET_WIDTH-1:0] read_set;
    assign read_set = (regs_q.state >= DCACHE_MAINTENANCE_READ) ?
        maintenance_read_set : accept_set;

    // Why: one synchronous read and one full-line write per way, plus the shared
    // metadata word, so each maps to a simple dual-port M9K. Only control state
    // resets; the sweep after reset clears the metadata RAM, and the data RAMs
    // need no reset because no valid tag points at them.
    genvar way;
    generate
    for (way = 0; way < WAYS; way = way + 1) begin : g_way
        logic refill_write;
        assign refill_write = !reset_i && refill_ok && victim_way_q == WAY_WIDTH'(way);
        logic hit_write;
        assign hit_write = !reset_i && store_hit && selected_hit_way == WAY_WIDTH'(way);
        logic data_write;
        assign data_write = refill_write || hit_write;
        logic [127:0] write_line;
        assign write_line = refill_write ? merged_refill_line : merged_hit_line;
        // Why: the maintenance walk and the next request read a different set
        // than the one being written, so data is simple dual-port.
        n2a_sdp_ram #(.DATA_WIDTH(128), .DEPTH(SET_COUNT)) u_data_ram (
            .clk_i(clk_i),
            .write_enable_i(data_write),
            .write_address_i(request_set),
            .write_data_i(write_line),
            .read_address_i(read_set),
            .read_data_o(data_read_q[way])
        );
    end
    endgenerate

    n2a_sdp_ram #(.DATA_WIDTH(SET_WORD_WIDTH), .DEPTH(SET_COUNT)) u_set_ram (
        .clk_i(clk_i),
        .write_enable_i(meta_write),
        .write_address_i(meta_write_set),
        .write_data_i(meta_write_word),
        .read_address_i(read_set),
        .read_data_o(set_word_read)
    );

    // Why: every install (refill or buffer) starts a next-line prefetch to hide
    // sequential misses, except when maintenance is requested, a prefetch of
    // another line is owed (one line transaction at a time), or the next line is
    // already buffered or being prefetched (no duplicate fetch).
    logic [LINE_WIDTH-1:0] next_line;
    assign next_line = request_line + 1'b1;
    logic prefetch_response;
    assign prefetch_response = regs_q.prefetch_valid && regs_q.prefetch_accepted &&
        line_response_valid_i;
    logic maintenance_start;
    assign maintenance_start = regs_q.init_done && regs_q.state == DCACHE_IDLE &&
        maintenance_valid_i;
    logic next_line_held;
    always_comb begin
        next_line_held = regs_q.prefetch_valid && regs_q.prefetch_line == next_line;
        for (int slot = 0; slot < int'(BUFFER_SLOTS); slot++) begin
            if (buffer_valid_q[slot] && buffer_line_q[slot] == next_line)
                next_line_held = 1'b1;
        end
    end
    logic start_prefetch;
    assign start_prefetch = PREFETCH && refill_ok && !maintenance_valid_i &&
        !regs_q.write_buffer_active && !start_write_buffer &&
        (!prefetch_inflight || prefetch_response) && !next_line_held &&
        !(regs_q.write_buffer_valid && regs_q.write_buffer_line == next_line) &&
        !(write_buffer_enqueue && victim_line == next_line);
    logic cancel_prefetch;
    assign cancel_prefetch = regs_q.prefetch_valid && !regs_q.prefetch_presented &&
        (maintenance_start || (lookup_miss && request_line == regs_q.prefetch_line) ||
         (start_prefetch && next_line != regs_q.prefetch_line));
    // Why: a completed prefetch fills the buffer unless it failed or maintenance
    // discards it, so the buffer never holds bad or pre-maintenance data. The
    // slot is the lowest free one (including the one a buffer refill frees this
    // cycle), otherwise the round-robin slot, so free slots fill before a
    // buffered line is replaced.
    logic buffer_load;
    assign buffer_load = prefetch_response && !line_response_error_i &&
        !regs_q.prefetch_discard && !maintenance_start &&
        !(regs_q.write_buffer_valid && regs_q.write_buffer_line == regs_q.prefetch_line) &&
        !(write_buffer_enqueue && victim_line == regs_q.prefetch_line);
    logic [BUFFER_SLOTS-1:0] buffer_free;
    assign buffer_free = ~buffer_valid_q | (buffer_refill ? buffer_match : '0);
    logic [SLOT_WIDTH-1:0] load_slot;
    always_comb begin
        load_slot = buffer_next_q;
        for (int slot = int'(BUFFER_SLOTS) - 1; slot >= 0; slot--) begin
            if (buffer_free[slot]) load_slot = SLOT_WIDTH'(slot);
        end
    end
    // Why: drop any prefetched copy of a dirty victim on enqueue, because memory,
    // and so that copy, still holds the older bytes until the background drain.
    logic [LINE_WIDTH-1:0] victim_line;
    assign victim_line = {victim_tag, request_set};
    logic [BUFFER_SLOTS-1:0] writeback_match;
    always_comb begin
        for (int slot = 0; slot < int'(BUFFER_SLOTS); slot++) begin
            writeback_match[slot] = write_buffer_enqueue && buffer_valid_q[slot] &&
                buffer_line_q[slot] == victim_line;
        end
    end
    // Ref: wiki/src/nand2doom/rtl/core/mas.md "Performance monitor" event 82
    // (DCACHE_PREFETCH_DROP): counts lines dropped unused this clock (a
    // discarded or failed response, a replaced slot, a writeback match and the
    // slots maintenance clears); the backlog reports them one per clock.
    logic [2:0] drop_count;
    always_comb begin
        drop_count = 3'(prefetch_response && !buffer_load) +
            3'(buffer_load && !buffer_free[load_slot]);
        for (int slot = 0; slot < int'(BUFFER_SLOTS); slot++) begin
            drop_count += 3'(writeback_match[slot]) +
                3'(maintenance_start && buffer_valid_q[slot]);
        end
    end

    // Why: the defaults are applied ahead of the reset value selection, so pulse
    // outputs fall low every clock, including under reset.
    always_comb begin
        regs_d = regs_q;
        buffer_next_d = buffer_next_q;
        buffer_data_d = buffer_data_q;
        buffer_line_d = buffer_line_q;
        buffer_valid_d = buffer_valid_q;
        maintenance_way_d = maintenance_way_q;
        forward_way_d = forward_way_q;
        meta_forward_word_d = meta_forward_word_q;
        miss_word_d = miss_word_q;
        victim_way_d = victim_way_q;
        regs_d.cpu_response_valid = 1'b0;
        regs_d.cpu_response_error = 1'b0;
        regs_d.maintenance_done = 1'b0;
        regs_d.maintenance_error = 1'b0;
        regs_d.hit_event = lookup_hit;
        regs_d.miss_event = lookup_miss;
        regs_d.forward_valid = 1'b0;
        regs_d.prefetch_issue_event = select_prefetch;
        regs_d.prefetch_cancel_event = cancel_prefetch;
        regs_d.prefetch_useful_event = buffer_refill;
        regs_d.prefetch_drop_backlog = regs_q.prefetch_drop_backlog -
            3'(regs_q.prefetch_drop_backlog != 3'b0) + drop_count;
        regs_d.meta_read_set = read_set;
        regs_d.meta_forward_valid = meta_write;
        regs_d.meta_forward_set = meta_write_set;
        meta_forward_word_d = meta_write_word;
        if (!regs_q.init_done) begin
            regs_d.init_set = regs_q.init_set + 1'b1;
            regs_d.init_done = regs_q.init_set == SET_WIDTH'(SET_COUNT - 1);
        end
        if (start_write_buffer) begin
            regs_d.write_buffer_active = 1'b1;
            regs_d.write_buffer_accepted = 1'b0;
        end
        if (write_buffer_present && line_request_ready_i)
            regs_d.write_buffer_accepted = 1'b1;
        if (write_buffer_response) begin
            regs_d.write_buffer_active = 1'b0;
            regs_d.write_buffer_accepted = 1'b0;
            regs_d.write_buffer_failed = line_response_error_i;
            if (!line_response_error_i) regs_d.write_buffer_valid = 1'b0;
        end
        if (write_buffer_enqueue) begin
            regs_d.write_buffer_valid = 1'b1;
            regs_d.write_buffer_line = victim_line;
            regs_d.write_buffer_data = regs_q.line_buffer;
            regs_d.write_buffer_failed = 1'b0;
        end

        // Why: prefetch_presented is set only on selection, so the line contract
        // sees a prefetch only once it is offered.
        if (select_prefetch) regs_d.prefetch_presented = 1'b1;
        if (cancel_prefetch) begin
            regs_d.prefetch_valid = 1'b0;
            regs_d.prefetch_presented = 1'b0;
        end
        if (prefetch_present && line_request_ready_i) regs_d.prefetch_accepted = 1'b1;
        if (prefetch_response) begin
            regs_d.prefetch_valid = 1'b0;
            regs_d.prefetch_presented = 1'b0;
            regs_d.prefetch_accepted = 1'b0;
            regs_d.prefetch_discard = 1'b0;
        end
        if (maintenance_start) begin
            buffer_valid_d = '0;
            regs_d.prefetch_discard = prefetch_inflight && !prefetch_response;
        end
        buffer_valid_d &= ~writeback_match;
        if (buffer_refill) buffer_valid_d &= ~buffer_match;
        if (buffer_load) begin
            buffer_valid_d[load_slot] = 1'b1;
            buffer_line_d[load_slot] = regs_q.prefetch_line;
            buffer_data_d[load_slot] = line_response_data_i;
            buffer_next_d = SLOT_WIDTH'(BUFFER_SLOTS > 1 ? int'(load_slot) + 1 : 0);
        end
        if (start_prefetch) begin
            regs_d.prefetch_valid = 1'b1;
            regs_d.prefetch_presented = 1'b0;
            regs_d.prefetch_accepted = 1'b0;
            regs_d.prefetch_discard = 1'b0;
            regs_d.prefetch_line = next_line;
        end

        case (regs_q.state)
            DCACHE_IDLE, DCACHE_LOOKUP: begin
                if (lookup_miss) begin
                    victim_way_d = selected_victim_way;
                    miss_word_d = set_word;
                    regs_d.line_buffer = lookup_line[selected_victim_way];
                    regs_d.miss_dirty = set_valid[selected_victim_way] &&
                        set_dirty[selected_victim_way];
                    regs_d.state = DCACHE_REFILL_REQUEST;
                end else begin
                    if (store_hit) begin
                        regs_d.line_buffer = merged_hit_line;
                        regs_d.forward_valid = 1'b1;
                        forward_way_d = selected_hit_way;
                        regs_d.forward_set = request_set;
                    end
                    if (lookup && !request_accept) begin
                        regs_d.state = DCACHE_IDLE;
                    end else if (!lookup && maintenance_start) begin
                        regs_d.maintenance_set = '0;
                        maintenance_way_d = '0;
                        regs_d.state = DCACHE_MAINTENANCE_DRAIN;
                    end
                end
                if (request_accept) begin
                    regs_d.request_address = cpu_request_address_i[31:2];
                    regs_d.request_write = cpu_request_write_i;
                    regs_d.request_write_data = cpu_request_write_data_i;
                    regs_d.request_byte_enable = cpu_request_byte_enable_i;
                    if (request_address_error) begin
                        regs_d.cpu_response_data = 32'b0;
                        regs_d.cpu_response_error = 1'b1;
                        regs_d.cpu_response_valid = 1'b1;
                        regs_d.state = DCACHE_IDLE;
                    end else begin
                        regs_d.state = DCACHE_LOOKUP;
                    end
                end
            end

            DCACHE_REFILL_REQUEST: begin
                if (write_buffer_response && line_response_error_i && write_buffer_block) begin
                    regs_d.cpu_response_data = 32'b0;
                    regs_d.cpu_response_error = 1'b1;
                    regs_d.cpu_response_valid = 1'b1;
                    regs_d.state = DCACHE_IDLE;
                end else if (buffer_refill) begin
                    regs_d.cpu_response_data = regs_q.request_write ? 32'b0 :
                        refill_line[request_word * 32 +: 32];
                    regs_d.cpu_response_valid = 1'b1;
                    regs_d.state = DCACHE_IDLE;
                end else if (demand_request_ready) begin
                    regs_d.state = DCACHE_REFILL_RESPONSE;
                end
            end

            DCACHE_REFILL_RESPONSE: begin
                if (line_response_valid_i) begin
                    if (line_response_error_i) begin
                        regs_d.cpu_response_data = 32'b0;
                        regs_d.cpu_response_error = 1'b1;
                    end else begin
                        regs_d.cpu_response_data = regs_q.request_write ? 32'b0 :
                            line_response_data_i[request_word * 32 +: 32];
                    end
                    regs_d.cpu_response_valid = 1'b1;
                    regs_d.state = DCACHE_IDLE;
                end
            end

            DCACHE_MAINTENANCE_DRAIN: begin
                if (write_buffer_response && line_response_error_i) begin
                    regs_d.maintenance_error = 1'b1;
                    regs_d.maintenance_done = 1'b1;
                    regs_d.state = DCACHE_IDLE;
                end else if (!regs_q.write_buffer_valid) begin
                    regs_d.state = DCACHE_MAINTENANCE_READ;
                end
            end

            DCACHE_MAINTENANCE_READ: regs_d.state = DCACHE_MAINTENANCE_SCAN;

            DCACHE_MAINTENANCE_SCAN: begin
                regs_d.state = DCACHE_MAINTENANCE_SCAN;
                if (maintenance_entry_dirty) begin
                    regs_d.state = DCACHE_MAINTENANCE_WRITEBACK_REQUEST;
                end else begin
                    if (maintenance_last_way) begin
                        if (maintenance_last_set) begin
                            regs_d.maintenance_done = 1'b1;
                            regs_d.state = DCACHE_IDLE;
                        end else begin
                            regs_d.maintenance_set = regs_q.maintenance_set + 1'b1;
                            maintenance_way_d = '0;
                        end
                    end else begin
                        maintenance_way_d = maintenance_way_q + 1'b1;
                    end
                end
            end

            DCACHE_MAINTENANCE_WRITEBACK_REQUEST: begin
                if (demand_request_ready) regs_d.state = DCACHE_MAINTENANCE_WRITEBACK_RESPONSE;
            end

            DCACHE_MAINTENANCE_WRITEBACK_RESPONSE: begin
                if (line_response_valid_i) begin
                    if (line_response_error_i) begin
                        regs_d.maintenance_error = 1'b1;
                        regs_d.maintenance_done = 1'b1;
                        regs_d.state = DCACHE_IDLE;
                    end else begin
                        if (maintenance_last_way) begin
                            if (maintenance_last_set) begin
                                regs_d.maintenance_done = 1'b1;
                                regs_d.state = DCACHE_IDLE;
                            end else begin
                                regs_d.maintenance_set = regs_q.maintenance_set + 1'b1;
                                maintenance_way_d = '0;
                                regs_d.state = DCACHE_MAINTENANCE_SCAN;
                            end
                        end else begin
                            maintenance_way_d = maintenance_way_q + 1'b1;
                            regs_d.state = DCACHE_MAINTENANCE_SCAN;
                        end
                    end
                end
            end

            default: regs_d.state = DCACHE_IDLE;
        endcase
    end

    `DFF_RST_VAL(regs_q, regs_d, clk_i, reset_i, REGS_RESET)
    `DFF_RST(victim_way_q, victim_way_d, clk_i, reset_i)
    `DFF_RST(miss_word_q, miss_word_d, clk_i, reset_i)
    `DFF_RST(meta_forward_word_q, meta_forward_word_d, clk_i, reset_i)
    `DFF_RST(forward_way_q, forward_way_d, clk_i, reset_i)
    `DFF_RST(maintenance_way_q, maintenance_way_d, clk_i, reset_i)
    `DFF_RST(buffer_valid_q, buffer_valid_d, clk_i, reset_i)
    `DFF_RST(buffer_line_q, buffer_line_d, clk_i, reset_i)
    `DFF_RST(buffer_data_q, buffer_data_d, clk_i, reset_i)
    `DFF_RST(buffer_next_q, buffer_next_d, clk_i, reset_i)
    // Why: a hit responds combinationally in LOOKUP (stores return zero data); a
    // refill or local error responds from the registered fields. They never
    // coincide, so OR-ing the valids is safe.
    assign cpu_response_valid_o = lookup_hit || regs_q.cpu_response_valid;
    assign cpu_response_data_o = lookup_hit ?
        (regs_q.request_write ? 32'b0 : hit_word) : regs_q.cpu_response_data;
    assign cpu_response_error_o = regs_q.cpu_response_error;
    assign maintenance_done_o = regs_q.maintenance_done;
    assign maintenance_error_o = regs_q.maintenance_error;
    assign hit_event_o = regs_q.hit_event;
    assign miss_event_o = regs_q.miss_event;
    assign prefetch_issue_event_o = regs_q.prefetch_issue_event;
    assign prefetch_useful_event_o = regs_q.prefetch_useful_event;
    assign prefetch_drop_event_o = regs_q.prefetch_drop_backlog != 3'b0;
    assign prefetch_deferred_event_o = regs_q.prefetch_valid &&
        !regs_q.prefetch_presented && !select_prefetch;
    assign prefetch_cancel_event_o = regs_q.prefetch_cancel_event;
    assign write_buffer_pending_o = regs_q.write_buffer_valid;
    assign write_buffer_wait_o = regs_q.state == DCACHE_REFILL_REQUEST && write_buffer_block;

    `ASSERT_CLOCKED_ERROR_WHEN_UNGUARDED(clk_i, reset_i, request_accept,
        !$isunknown(cpu_request_address_i[1:0]),
        "D-cache request byte offset is unknown")
    `ASSERT_CLOCKED_ERROR_UNGUARDED(clk_i, reset_i,
        !(lookup_hit && regs_q.cpu_response_valid),
        "D-cache hit and registered response coincide")
    `ASSERT_CLOCKED_ERROR_UNGUARDED(clk_i, reset_i, !lookup || $onehot0(hit_ways),
        "D-cache tag present in more than one way")
    `ASSERT_CLOCKED_ERROR_UNGUARDED(clk_i, reset_i,
        !(prefetch_inflight && (regs_q.write_buffer_active ||
          regs_q.state == DCACHE_REFILL_RESPONSE ||
          regs_q.state == DCACHE_MAINTENANCE_WRITEBACK_RESPONSE)),
        "D-cache prefetch owed while a demand line response is owed")
    `ASSERT_CLOCKED_ERROR_UNGUARDED(clk_i, reset_i,
        !line_response_valid_i || prefetch_response ||
        write_buffer_response ||
        regs_q.state == DCACHE_REFILL_RESPONSE ||
        regs_q.state == DCACHE_MAINTENANCE_WRITEBACK_RESPONSE,
        "D-cache line response without an accepted transaction")
    `ASSERT_CLOCKED_ERROR_UNGUARDED(clk_i, reset_i,
        {1'b0, regs_q.prefetch_drop_backlog} + {1'b0, drop_count} <= 4'd7,
        "D-cache prefetch drop backlog overflow")
    `ASSERT_CLOCKED_ERROR_UNGUARDED(clk_i, reset_i,
        !write_buffer_enqueue || !regs_q.write_buffer_valid,
        "D-cache displaced dirty line overwrites a full write buffer")
    `ASSERT_CLOCKED_ERROR_UNGUARDED(clk_i, reset_i,
        !regs_q.write_buffer_active || regs_q.write_buffer_valid,
        "D-cache drain has no buffered dirty line")
    `ASSERT_ERROR_STABLE_WHEN(line_request_held, clk_i, reset_i,
        line_request_valid_o && !line_request_ready_i,
        {line_request_valid_o, line_request_write_o, line_request_address_o,
         line_request_write_data_o, line_request_byte_enable_o, line_request_prefetch_o})
    `ASSERT_ERROR_STABLE_WHEN(data_request_held, clk_i, reset_i,
        cpu_request_valid_i && !cpu_request_ready_o,
        {cpu_request_valid_i, cpu_request_write_i, cpu_request_address_i,
         cpu_request_write_data_i, cpu_request_byte_enable_i})
endmodule
