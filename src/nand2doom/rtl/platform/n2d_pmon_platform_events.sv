`include "src/common/rtl/pkg/macros.vh"

// Platform PMON event sources by event ID (wiki/src/nand2doom/rtl/core/mas.md
// "Performance monitor"), derived from the existing cache, line-memory and
// uncached-data handshakes of n2d_system without changing those blocks.
// The output is combinational; the core's PMON registers it before counting.
//
// Line masters are indexed 0 = I-cache, 1 = D-cache, 2 = debug, as on the
// n2d_system memory_request_* boundary. The downstream memory request is not
// visible here, so MEM_BACKPRESSURE (33) is reconstructed from the shared
// line-memory contract (wiki/src/nand2doom/rtl/memory/mas.md): with one
// outstanding transaction, the controller presents a downstream request
// exactly when it is idle and some master is valid, and grants `ready` to
// one master exactly when that downstream request is accepted. Contention
// between masters is MEM_MASTER_WAIT (77), not backpressure.
//
// MEM_OWNER_* (87..91) name the owner of the one line transaction from its
// acceptance clock through its response clock: exactly one while memory is
// occupied, none otherwise, and never together with MEM_BACKPRESSURE. The
// perf harness splits D-miss waits by them (wiki/src/nand2doom/dv/platform/dv-plan.md).
module n2d_pmon_platform_events (
    input  logic         clk_i,
    input  logic         reset_i,
    input  logic         instruction_hit_event_i,
    input  logic         instruction_miss_event_i,
    input  logic         data_hit_event_i,
    input  logic         data_miss_event_i,
    // Registered D-cache prefetch pulses (wiki/src/nand2doom/rtl/memory/mas.md).
    input  logic         data_prefetch_issue_event_i,
    input  logic         data_prefetch_useful_event_i,
    input  logic         data_prefetch_drop_event_i,
    input  logic         data_prefetch_deferred_event_i,
    input  logic         data_prefetch_cancel_event_i,
    input  logic         data_write_buffer_pending_i,
    input  logic         data_write_buffer_wait_i,
    input  logic [2:0]   line_request_valid_i,
    input  logic [2:0]   line_request_ready_i,
    // The D-cache line request is a next-line prefetch.
    input  logic         line_request_prefetch_i,
    // The I-cache never writes and debug response errors are no event; the
    // full boundary vectors are kept for a direct connection.
    /* verilator lint_off UNUSEDSIGNAL */
    input  logic [2:0]   line_request_write_i,
    input  logic [2:0]   line_response_valid_i,
    input  logic [2:0]   line_response_error_i,
    /* verilator lint_on UNUSEDSIGNAL */
    // Accepted CPU data request and its decode.
    input  logic         data_accept_i,
    input  logic         data_write_i,
    input  logic         data_sdram_i,
    input  logic         data_framebuffer_i,
    // An accepted uncached data request awaits its response.
    input  logic         uncached_wait_i,
    output logic [127:0] events_o
);
    import n2d_types_pkg::*;

    t_pmon_track track_q, track_d;
    logic [2:0] line_accept;
    logic       uncached_accept;
    logic [4:0] accept_owner;

    assign line_accept = line_request_valid_i & line_request_ready_i;
    assign uncached_accept = data_accept_i && !data_sdram_i;
    // Ref: wiki/src/nand2doom/rtl/core/mas.md "Performance monitor" events 87-91,
    // bit 0 first: owner of a transaction accepted this clock is fetch, data
    // demand read, data prefetch, data write (writeback or maintenance), debug.
    assign accept_owner = {line_accept[2],
        line_accept[1] && line_request_write_i[1],
        line_accept[1] && !line_request_write_i[1] && line_request_prefetch_i,
        line_accept[1] && !line_request_write_i[1] && !line_request_prefetch_i,
        line_accept[0]};

    always_comb begin
        track_d = track_q;
        if (data_accept_i && data_sdram_i)
            track_d.dcache_write = data_write_i;
        if (line_response_valid_i[0])
            track_d.icache_line_read_pending = 1'b0;
        if (line_accept[0])
            track_d.icache_line_read_pending = 1'b1;
        if (line_response_valid_i[1])
            track_d.dcache_line_read_pending = 1'b0;
        if (line_accept[1] && !line_request_write_i[1])
            track_d.dcache_line_read_pending = 1'b1;
        // Why: one outstanding line transaction, so memory is busy from acceptance
        // through its single response pulse.
        if (|line_response_valid_i) begin
            track_d.memory_busy = 1'b0;
            track_d.owner = 5'b0;
        end
        if (|line_accept) begin
            track_d.memory_busy = 1'b1;
            track_d.owner = accept_owner;
        end
    end

    `DFF_RST(track_q, track_d, clk_i, reset_i)

    always_comb begin
        events_o = 128'b0;
        events_o[12] = instruction_hit_event_i || instruction_miss_event_i;
        events_o[13] = instruction_miss_event_i;
        events_o[14] = line_response_valid_i[0] && !line_response_error_i[0];
        events_o[15] = (data_hit_event_i || data_miss_event_i) && !track_q.dcache_write;
        events_o[16] = data_miss_event_i && !track_q.dcache_write;
        events_o[17] = data_miss_event_i && track_q.dcache_write;
        events_o[18] = line_response_valid_i[1] && !line_response_error_i[1] &&
            track_q.dcache_line_read_pending;
        events_o[19] = line_accept[1] && line_request_write_i[1];
        events_o[29] = line_accept[0];
        events_o[30] = line_accept[1] && !line_request_write_i[1];
        events_o[32] = |(line_accept[2:1] & line_request_write_i[2:1]);
        events_o[33] = !track_q.memory_busy && |line_request_valid_i && !(|line_accept);
        events_o[34] = track_q.icache_line_read_pending || track_q.dcache_line_read_pending;
        events_o[64] = (data_hit_event_i || data_miss_event_i) && track_q.dcache_write;
        events_o[65] = uncached_accept && data_framebuffer_i && data_write_i;
        events_o[66] = uncached_accept && !(data_framebuffer_i && data_write_i);
        events_o[67] = uncached_wait_i;
        events_o[77] = |(line_request_valid_i & ~line_request_ready_i);
        events_o[80] = data_prefetch_issue_event_i;
        events_o[81] = data_prefetch_useful_event_i;
        events_o[82] = data_prefetch_drop_event_i;
        events_o[91:87] = accept_owner | track_q.owner;
        events_o[94] = data_write_buffer_pending_i;
        events_o[95] = data_write_buffer_wait_i;
        events_o[96] = data_prefetch_deferred_event_i;
        events_o[97] = data_prefetch_cancel_event_i;
    end

    `ASSERT_CLOCKED(clk_i, reset_i, $onehot0(line_accept))
    `ASSERT_CLOCKED_WHEN(clk_i, reset_i, track_q.memory_busy, line_accept == 3'b000)
    `ASSERT_CLOCKED(clk_i, reset_i, $onehot0(events_o[91:87]))
    `ASSERT_CLOCKED(clk_i, reset_i, track_q.memory_busy == (track_q.owner != 5'b0))
endmodule
