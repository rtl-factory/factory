// ASIC backend of the nand2mario dual-port RAM contract owned by
// wiki/src/nand2mario/rtl/memory/mas.md ("ASIC backend"). It defines the same
// module and ports as n2m_dual_port_ram.sv; only an ASIC source list (and
// the DV targets that prove this mapping) selects it.
//
// A store of SRAM_MIN_BYTES or more on one clock (the 8 KiB VRAM and WRAM)
// is one single-port n2a_sync_ram_wt, the common write-through contract
// whose IHP backend maps it to SRAM macros, shared by both ports. Port B
// keeps priority and its exact timing; a port-A write on a port-B read
// edge is posted for the next free edge and forwarded to later port-B reads
// of its address, so the outputs equal the portable backend's on every
// edge, in and out of reset, provided that port A never reads on a port-B
// read edge or while a write is posted, and never writes while one is
// posted except under a port-B reset (asserted outside reset). The consumers satisfy this: the PPU reads VRAM
// only in mode 3, where the CPU is blocked, so only a CPU write accepted
// just before mode 3 can meet a PPU read, and WRAM has no port-B reader.
// A reset never drops a posted write: a port-B reset frees the store (its
// read is discarded), so the write retires on that edge, and under a
// port-A reset alone it retires on the next edge without a port-B read.
// The contract's port registers are kept (read-valid registers, the port-A
// write-first bypass, the data holds). No output uses the store's word on
// a write edge: a same-edge port-A read and write returns the bypassed
// write data, so the write-through collision of the store is never seen
// (asserted below, as the common contract requires). Smaller or
// dual-clock stores (OAM, HRAM, the loader buffer) keep the portable
// two-port array, which the ASIC flow maps to flops.
//
// The selection boundary needs one module name in differently named files,
// so the filename lint is waived for this file only.
/* verilator lint_off DECLFILENAME */
`include "src/common/rtl/pkg/macros.vh"

module n2m_dual_port_ram #(
    parameter int unsigned DATA_WIDTH = 32,
    parameter int unsigned DEPTH = 256,
    parameter int unsigned BYTE_LANES = DATA_WIDTH / 8,
    parameter bit DUAL_CLOCK = 1'b0
) (
    input  logic                      clk_a_i,
    input  logic                      reset_a_i,
    input  logic                      read_a_i,
    input  logic                      write_a_i,
    input  logic [$clog2(DEPTH)-1:0]  address_a_i,
    input  logic [DATA_WIDTH-1:0]     write_data_a_i,
    input  logic [BYTE_LANES-1:0]     byte_enable_a_i,
    output logic [DATA_WIDTH-1:0]     read_data_a_o,
    output logic                      read_valid_a_o,

    input  logic                      clk_b_i,
    input  logic                      reset_b_i,
    input  logic                      read_b_i,
    input  logic [$clog2(DEPTH)-1:0]  address_b_i,
    output logic [DATA_WIDTH-1:0]     read_data_b_o,
    output logic                      read_valid_b_o
);
    // Why: the address ports derive their width from DEPTH, matching the
    // portable backend's interface; this reads it back from a port.
    localparam int unsigned ADDR_WIDTH = $bits(address_a_i);
    // Smallest store mapped to one shared single-port SRAM.
    localparam int unsigned SRAM_MIN_BYTES = 8192;
    localparam bit SHARED_SRAM = !DUAL_CLOCK && (DATA_WIDTH == 8) && (DEPTH >= SRAM_MIN_BYTES);
    localparam int unsigned IMPLEMENTATION_DATA_WIDTH =
        (DATA_WIDTH < 8) ? 8 : DATA_WIDTH;
    localparam int unsigned IMPLEMENTATION_DEPTH = (DEPTH < 2) ? 2 : DEPTH;
    localparam bit DEPTH_IS_POWER_OF_TWO =
        (DEPTH >= 2) && ((DEPTH & (DEPTH - 1)) == 0);

    logic                  write_a;
    logic [DATA_WIDTH-1:0] array_a, array_b;

    // Why: a request under reset must not change the store or the outputs, so
    // reset suppresses it: no write, no data update, valid low.
    assign write_a = write_a_i && !reset_a_i;

    generate
        if (SHARED_SRAM) begin : g_shared_sram
            // Why: one single-port store. Port B (the PPU) keeps its timing: on an
            // edge where it reads outside its reset, it owns the store (b_owns).
            // A port-A write on such an edge is posted: held in pend_*_q and
            // written on the next edge port B does not own. Port B still sees the
            // portable contract: the read on the write edge returns the old word
            // (read-first), and a later read of the posted address returns the
            // posted data (forwarded) until the store holds it.
            //
            // Reset never drops a posted write. A port-B read under reset_b_i is
            // discarded (its valid register resets), so it does not own the
            // store and a posted write retires on that edge. If port A writes on
            // that same edge (the VRAM boot clear writes during reset_b_i), its
            // write is posted behind the retiring one; outside reset the sharing
            // rule (asserted) excludes a write while one is posted, so this
            // happens only under reset_b_i, which frees the store every edge.
            // pend_q needs no reset: every edge port B does not own retires and
            // replaces it, and under reset_b_i it is known after one edge.
            logic                  pend_q, pend_d, pend_capture, pend_retire, reset_any, b_owns;
            logic [ADDR_WIDTH-1:0] pend_address_q;
            logic [DATA_WIDTH-1:0] pend_data_q, forward_data_q, store_data, store_write_data;
            logic                  forward_q, forward_d, store_write;
            logic [ADDR_WIDTH-1:0] address;
            assign reset_any = reset_a_i || reset_b_i;
            assign b_owns = read_b_i && !reset_b_i;
            assign pend_capture = write_a && (b_owns || pend_q);
            assign pend_retire = pend_q && !b_owns;
            assign store_write = (write_a && !b_owns && !pend_q) || pend_retire;
            assign store_write_data = pend_retire ? pend_data_q : write_data_a_i;
            always_comb begin
                address = address_b_i;
                if (pend_retire) address = pend_address_q;
                else if ((read_a_i || write_a_i) && !b_owns) address = address_a_i;
            end
            assign pend_d = pend_capture || (pend_q && b_owns);
            `DFF(pend_q, pend_d, clk_a_i)
            `DFF_EN(pend_address_q, address_a_i, clk_a_i, pend_capture)
            `DFF_EN(pend_data_q, write_data_a_i, clk_a_i, pend_capture)
            assign forward_d = b_owns && pend_q && (address_b_i == pend_address_q);
            `DFF(forward_q, forward_d, clk_a_i)
            `DFF(forward_data_q, pend_data_q, clk_a_i)

            n2a_sync_ram_wt #(
                .DATA_WIDTH (DATA_WIDTH),
                .DEPTH      (DEPTH)
            ) u_store (
                .clk_i          (clk_a_i),
                .write_enable_i (store_write),
                .address_i      (address),
                .write_data_i   (store_write_data),
                .read_data_o    (store_data)
            );
            assign array_a = store_data;
            assign array_b = forward_q ? forward_data_q : store_data;
            `UNUSED_BITS(unused_shared_inputs, {clk_b_i, byte_enable_a_i})

            `ASSERT_NEVER(shared_port_a_read_exclusive, clk_a_i, reset_any,
                read_a_i && (read_b_i || pend_q))
            `ASSERT_NEVER(shared_one_posted_write, clk_a_i, reset_any,
                write_a && pend_q)
            // Why: no output takes the store's word of a write edge: a port-A read on
            // a port-A write edge is bypassed, and a store write never shares an
            // edge with a port-B read, so the store's collision rule is unused.
            `ASSERT_NEVER(shared_write_word_unused, clk_a_i, reset_any,
                store_write && ((read_a_i && !write_a_i) || read_b_i))
        end else begin : g_two_port
            // Why: both ports read every edge, read-first, like the FPGA template, so
            // the two backends return the same word on a write edge.
            logic [IMPLEMENTATION_DATA_WIDTH-1:0] memory [0:IMPLEMENTATION_DEPTH-1];
            `MEM_WRITE_LANES(memory, clk_a_i, write_a, address_a_i, write_data_a_i,
                byte_enable_a_i, 8)
            `MEM_READ(array_a, memory, clk_a_i, address_a_i)
            if (DUAL_CLOCK) begin : g_dual_clock_read
                `MEM_READ(array_b, memory, clk_b_i, address_b_i)
            end else begin : g_single_clock_read
                `MEM_READ(array_b, memory, clk_a_i, address_b_i)
            end
        end
    endgenerate

    // Why: the array read is read-first, so port A gets its contract's
    // write-first result from this bypass; it can return the full input word
    // because the contract enables all lanes on a port-A write.
    logic                  bypass_a_q, bypass_a_d;
    logic [DATA_WIDTH-1:0] write_data_a_q, held_a_q;
    assign bypass_a_d = write_a_i;
    `DFF_RST(read_valid_a_o, read_a_i, clk_a_i, reset_a_i)
    `DFF(bypass_a_q, bypass_a_d, clk_a_i)
    `DFF(write_data_a_q, write_data_a_i, clk_a_i)
    assign read_data_a_o = !read_valid_a_o ? held_a_q :
                           (bypass_a_q ? write_data_a_q : array_a);
    `DFF(held_a_q, read_data_a_o, clk_a_i)

    logic [DATA_WIDTH-1:0] held_b_q;
    generate
        if (DUAL_CLOCK) begin : g_dual_clock_b
            `DFF_RST(read_valid_b_o, read_b_i, clk_b_i, reset_b_i)
            `DFF(held_b_q, read_data_b_o, clk_b_i)
            `ASSERT(dual_clock_collision_a, clk_a_i,
                (reset_a_i || reset_b_i),
                !(write_a_i && read_b_i && (address_a_i == address_b_i)))
            `ASSERT(dual_clock_collision_b, clk_b_i,
                (reset_a_i || reset_b_i),
                !(write_a_i && read_b_i && (address_a_i == address_b_i)))
            `ASSERT_KNOWN(b_control_known, clk_b_i, reset_b_i,
                {read_b_i, address_b_i})
        end else begin : g_single_clock_b
            `DFF_RST(read_valid_b_o, read_b_i, clk_a_i, reset_b_i)
            `DFF(held_b_q, read_data_b_o, clk_a_i)
            `ASSERT_KNOWN(b_control_known, clk_a_i, reset_b_i,
                {read_b_i, address_b_i})
        end
    endgenerate
    assign read_data_b_o = read_valid_b_o ? array_b : held_b_q;

    `ASSERT_PARAM(g_invalid_data_width,
        (DATA_WIDTH >= 8) && ((DATA_WIDTH % 8) == 0),
        "n2m_dual_port_ram DATA_WIDTH must be a positive byte multiple")
    `ASSERT_PARAM(g_invalid_depth, DEPTH_IS_POWER_OF_TWO,
        "n2m_dual_port_ram DEPTH must be a power of two and at least 2")
    `ASSERT_PARAM(g_invalid_byte_lanes, BYTE_LANES == (DATA_WIDTH / 8),
        "n2m_dual_port_ram BYTE_LANES must equal DATA_WIDTH/8")

    `ASSERT_KNOWN(a_control_known, clk_a_i, reset_a_i,
        {read_a_i, write_a_i, address_a_i, byte_enable_a_i})
    `ASSERT(a_write_data_known, clk_a_i, reset_a_i,
        !write_a_i || !$isunknown(write_data_a_i))
    `ASSERT(a_read_write_all_lanes, clk_a_i, reset_a_i,
        !(read_a_i && write_a_i) || (&byte_enable_a_i))
endmodule
/* verilator lint_on DECLFILENAME */
